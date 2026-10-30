"""TeachQuiz (ADR-070): how much a lesson teaches. A quiz on what a lesson of its length must teach,
answered by a student from the lesson alone; an answer counts only when it is right and the lesson
backs it (the narrator's own words, or a picture shown). Scripted models: no cost."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

import animator.eval.__main__ as cli
from animator.engine_client import EngineWorkerPool
from animator.eval import summarize
from animator.eval.quiz import (
    Material,
    Quiz,
    QuizAnswer,
    QuizAnswers,
    make_quiz,
    material_of,
    questions_for,
    score,
    supported,
    take_quiz,
    teachquiz,
)
from animator.jobs.store import FileStore
from animator.llm import FakeLLM, LLMBadOutput, LLMRequest, Usage
from animator.llm.types import ImagePart
from tests.test_pipeline import Script, run, settings


def written(n: int = 6) -> dict[str, Any]:
    """A quiz as the teacher model writes it: the right answer in a different place each time."""
    return {
        "questions": [
            {
                "id": f"q{i + 1}",
                "question": f"Why does thing {i + 1} happen?",
                "choices": [f"reason {i + 1}{c}" for c in "abcd"],
                "answer": i % 4,
                "concept": f"idea {i + 1}",
            }
            for i in range(n)
        ]
    }


def text_of(req: LLMRequest) -> str:
    return "\n".join(str(getattr(p, "text", "")) for m in req.messages for p in m.parts)


def the_quiz(n: int = 4) -> Quiz:
    return Quiz.model_validate({"topic": "Floating", "audience": "a learner", "seconds": 60, **written(n)})


LESSON = Material(
    [
        "Water pushes up on the block with the weight of the water it moves aside.",
        "So a ship floats when it moves aside its own weight of water.",
    ]
)


# ---------------------------------------------------------------------- the quiz


def asked(req: LLMRequest) -> int:
    """How many questions a quiz request asks for."""
    return int(text_of(req).split("Questions: ")[1].split()[0])


async def test_a_quiz_tests_what_a_lesson_of_its_length_must_teach() -> None:
    llm = FakeLLM(lambda r: written(asked(r)))
    quiz = await make_quiz(llm, settings(), "Archimedes' principle", "a high-school student", 60)
    assert len(quiz.questions) == 4 and quiz.seconds == 60  # a Snapshot teaches one idea well
    [req] = llm.requests
    assert req.stage == "quiz" and req.prompt_version == "teachquiz.v1" and req.label == "teachquiz quiz"
    assert "Depth: Snapshot" in text_of(req) and "Questions: 4" in text_of(req)
    assert req.models[0] == "m-board"  # the storyboard model unless the quiz has its own
    own = FakeLLM(lambda r: written(asked(r)))
    deep = await make_quiz(
        own, settings(animator_model_quiz="m-quiz"), "Archimedes' principle", "a learner", 180
    )
    assert own.requests[0].models[0] == "m-quiz" and "Depth: Deep dive" in text_of(own.requests[0])
    assert len(deep.questions) == 8  # a deeper lesson, a longer quiz


async def test_a_quiz_that_breaks_the_rules_is_asked_for_again() -> None:
    replies = iter([{"questions": written(6)["questions"][:5]}, written(6)])  # five questions, then six
    llm = FakeLLM(lambda r: next(replies))
    quiz = await make_quiz(llm, settings(), "Floating", "a learner", 120)
    assert len(quiz.questions) == 6 and len(llm.requests) == 2
    assert "write exactly 6 questions" in text_of(llm.requests[1])
    three = written(6)
    three["questions"][0]["choices"] = ["a", "b", "c"]
    with pytest.raises(ValueError, match="4 different choices"):
        await make_quiz(FakeLLM(lambda r: copy.deepcopy(three)), settings(), "Floating", "a learner", 120)


# ---------------------------------------------------------------------- the score


def answer(qid: str, choice: int, evidence: str = "", picture: int = 0) -> QuizAnswer:
    return QuizAnswer(id=qid, choice=choice, evidence=evidence, picture=picture)


def test_an_answer_counts_only_when_right_and_backed_by_the_lesson() -> None:
    quiz = the_quiz(4)  # right answers: q1 0, q2 1, q3 2, q4 3
    answers = QuizAnswers(
        answers=[
            answer(
                "q1", 0, "water pushes up on the block, with the weight of the water"
            ),  # the narrator's words
            answer("q2", 1, "Archimedes found this in his bath in Syracuse"),  # right, but not the lesson's
            answer("q3", -1),  # not taught
            answer("q4", 3, picture=2),  # right, shown in scene 2's picture
        ]
    )
    without = score(quiz, answers, LESSON)
    assert without["teachquiz"] == 25 and without["right"] == 3 and without["not_taught"] == 1
    assert [r["supported"] for r in without["per_question"]] == [True, False, False, False]
    shown = Material(LESSON.scenes, [b"png1", b"png2"])
    assert score(quiz, answers, shown)["teachquiz"] == 50  # with the pictures, scene 2's counts
    wrong = QuizAnswers(answers=[answer("q1", 2, "water pushes up on the block with the weight")])
    assert score(quiz, wrong, LESSON)["teachquiz"] == 0  # backed but wrong


def test_a_quote_must_be_the_narrators_own_words() -> None:
    assert supported(answer("q", 0, "So a ship floats when it moves aside its own weight"), LESSON)
    assert supported(answer("q", 0, "a SHIP floats -- when it moves aside its own weight!"), LESSON)
    assert not supported(answer("q", 0, "a ship sinks when it is heavier than water"), LESSON)
    assert not supported(answer("q", 0, "water"), LESSON)  # one word backs nothing
    assert not supported(answer("q", 0, picture=3), Material(LESSON.scenes, [b"a", b"b"]))  # no scene 3


# ---------------------------------------------------------------------- the student


async def test_the_student_reads_every_scene_and_sees_its_pictures() -> None:
    quiz = the_quiz(2)
    replies = iter(
        [
            {"answers": [{"id": "q1", "choice": 0, "evidence": "", "picture": 1}]},  # q2 forgotten
            {
                "answers": [
                    {"id": "q1", "choice": 0, "evidence": "", "picture": 1},
                    {"id": "q2", "choice": 1, "evidence": "a ship floats when it moves aside", "picture": 0},
                ]
            },
        ]
    )
    llm = FakeLLM(lambda r: next(replies))
    got = await take_quiz(llm, settings(), quiz, Material(LESSON.scenes, [b"p1", b"p2"]))
    assert [a.id for a in got.answers] == ["q1", "q2"] and len(llm.requests) == 2
    first = llm.requests[0]
    assert first.prompt_version == "student.v1"
    assert "set aside everything else you know" in first.instructions
    said = text_of(first)
    assert "Scene 1: Water pushes up" in said and "Scene 2: So a ship floats" in said
    assert "q1. Why does thing 1 happen?" in said and "0: reason 1a" in said
    images = [p for m in first.messages for p in m.parts if isinstance(p, ImagePart)]
    assert [i.png for i in images] == [b"p1", b"p2"] and all(i.detail == "low" for i in images)
    assert "missing q2" in text_of(llm.requests[1])


async def test_a_student_that_keeps_failing_raises() -> None:
    llm = FakeLLM(lambda r: LLMBadOutput("not JSON"))
    with pytest.raises(LLMBadOutput):
        await take_quiz(llm, settings(), the_quiz(2), LESSON)


# ---------------------------------------------------------------------- a real lesson


def marking(gen_said: str) -> Any:
    """Quiz and student: the student answers q1 right, quoting the lesson, and the rest not taught."""
    quote = " ".join(gen_said.split()[:6])

    def reply(req: LLMRequest) -> Any:
        if req.label.startswith("teachquiz quiz"):
            return written(asked(req))
        n = text_of(req).count(". Why does thing ")  # the questions the student is shown
        return {
            "answers": [
                {
                    "id": f"q{i + 1}",
                    "choice": 0 if i == 0 else -1,
                    "evidence": quote if i == 0 else "",
                    "picture": 0,
                }
                for i in range(n)
            ]
        }

    return reply


async def test_a_lessons_teachquiz(pool: EngineWorkerPool) -> None:
    gen, _ = await run(pool, Script())
    assert gen.status == "complete"
    material = material_of(gen)
    assert len(material.scenes) == len([s for s in gen.scenes if s.playable]) and all(material.scenes)
    llm = FakeLLM(marking(material.scenes[0]), usage=Usage(2000, 0, 400), cost=0.002)
    result, quiz = await teachquiz(llm, settings(), gen)
    n = len(quiz.questions)
    assert n == questions_for(quiz.seconds) and quiz.seconds == round(gen.request.duration_min * 60)
    assert result["teachquiz"] == round(100 / n) and result["right_and_supported"] == 1
    assert result["not_taught"] == n - 1
    assert result["cost_usd"] == pytest.approx(0.004)  # the quiz and the answers
    again, _ = await teachquiz(llm, settings(), gen, quiz)  # a kept quiz: only the student's call
    assert again["cost_usd"] == pytest.approx(0.002)


async def test_stored_lessons_are_scored_on_one_kept_quiz(
    pool: EngineWorkerPool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "QUIZZES", tmp_path / "quizzes")
    gen, _ = await run(pool, Script())
    await FileStore(tmp_path / "data").save(gen)
    llm = FakeLLM(marking(material_of(gen).scenes[0]), cost=0.001)
    first = await cli.score_stored(llm, settings(), tmp_path / "data", None)
    n = first[0]["questions"]
    assert [r["id"] for r in first] == [gen.id] and first[0]["teachquiz"] == round(100 / n)
    kept = cli.quiz_file(settings(), gen)
    assert kept.is_file() and kept.name.endswith(f"-{n}q.json")
    second = await cli.score_stored(llm, settings(), tmp_path / "data", None)
    assert second[0]["teachquiz"] == first[0]["teachquiz"]
    labels = [r.label for r in llm.requests]
    assert labels.count("teachquiz quiz") == 1 and labels.count("teachquiz answers") == 2  # the quiz was kept
    report = cli.teachquiz_md("t", first)
    assert "| TeachQuiz |" in report and f"Mean TeachQuiz: {round(100 / n):.1f} over 1 lessons" in report
    # The run report gains the column and the mean when lessons were scored.
    rows = [
        {
            "topic_id": "a",
            "status": "complete",
            "scenes": 1,
            "video_s": 60.0,
            "first_pass_valid": 1.0,
            "ready_within_3_corrections": 1.0,
            "degraded_rate": 0.0,
            "corrections_per_scene": 0.0,
            "layout_warnings_per_scene": 0.0,
            "first_play_s": 5.0,
            "cost_usd": 0.1,
            "cost_per_video_min": 0.1,
            "teachquiz": 17,
        }
    ]
    assert summarize(rows)["teachquiz"] == 17.0
