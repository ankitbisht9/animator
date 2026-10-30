"""TeachQuiz (plan T5, ADR-070): how much a lesson teaches, as a number from 0 to 100.

After Code2Video's TeachQuiz: a teacher model writes a multiple-choice quiz on what a good lesson of this
length must teach (the depth tiers of ADR-063); a student model answers it from the lesson alone (what
the narrator said in each scene and, when asked, one picture of each scene). An answer counts only when
it is right and the lesson supports it: the student quotes the narrator's words (found in the lesson)
or names the scene whose picture shows it. A model's own knowledge does not inflate the score.

The quiz depends only on the topic, the audience and the length, so lessons made with different
settings or models are judged on the same questions (the evaluation runner keeps one per topic).
Two small model calls a lesson (about a cent or less with the default model); never part of making a
lesson.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from pydantic import BaseModel, ValidationError

from animator.config import Settings
from animator.llm import CallContext, LLMBadOutput, LLMClient, LLMIncomplete, LLMRefusal, LLMRequest
from animator.llm.types import ImagePart, Message
from animator.pipeline.depth import tier_for
from animator.pipeline.models import Generation
from animator.pipeline.prompts import load_prompt

# Questions a quiz asks, by the lesson's depth (ADR-063): a 1-minute Snapshot teaches one idea well, a
# Masterclass many.
QUESTIONS = {"snapshot": 4, "essentials": 6, "deep_dive": 8, "mastery": 10, "masterclass": 12}
QUOTE_SHARE = 0.8  # a quote is the lesson's when this share of its word pairs are the narrator's


class QuizQuestion(BaseModel):
    id: str
    question: str
    choices: list[str]
    answer: int
    concept: str


class Quiz(BaseModel):
    topic: str
    audience: str
    seconds: int
    questions: list[QuizQuestion]


class _Written(BaseModel):
    questions: list[QuizQuestion]


class QuizAnswer(BaseModel):
    id: str
    choice: int
    evidence: str
    picture: int


class QuizAnswers(BaseModel):
    answers: list[QuizAnswer]


def _strict(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


QUIZ_SCHEMA = _strict(
    {
        "questions": {
            "type": "array",
            "items": _strict(
                {
                    "id": {"type": "string"},
                    "question": {"type": "string"},
                    "choices": {"type": "array", "items": {"type": "string"}},
                    "answer": {"type": "integer"},
                    "concept": {"type": "string"},
                }
            ),
        }
    }
)
ANSWERS_SCHEMA = _strict(
    {
        "answers": {
            "type": "array",
            "items": _strict(
                {
                    "id": {"type": "string"},
                    "choice": {"type": "integer"},
                    "evidence": {"type": "string"},
                    "picture": {"type": "integer"},
                }
            ),
        }
    }
)


@dataclass
class Material:
    """What a lesson says (its narration, scene by scene) and, when asked, shows (a picture a scene)."""

    scenes: list[str]
    pictures: list[bytes] = field(default_factory=list)


def material_of(gen: Generation, pictures: Sequence[bytes] = ()) -> Material:
    """The narration of each scene a learner sees."""
    scenes = []
    for rec in gen.scenes:
        if not rec.playable or rec.dsl is None:
            continue
        beats = (rec.dsl.get("narration") or {}).get("beats") or []
        scenes.append(" ".join(str(b.get("text", "")).replace("**", "") for b in beats).strip())
    return Material(scenes, list(pictures))


def models_for(settings: Settings) -> tuple[list[str], Any]:
    """The quiz's models: ANIMATOR_MODEL_QUIZ (else the storyboard model), then the fallbacks."""
    primary = settings.animator_model_quiz.strip() or settings.animator_model_storyboard.strip()
    chain = [primary] if primary else []
    for m in settings.animator_model_fallbacks.split(","):
        if m.strip() and m.strip() not in chain:
            chain.append(m.strip())
    if not chain:
        raise ValueError("no model for the quiz (ANIMATOR_MODEL_QUIZ or ANIMATOR_MODEL_STORYBOARD)")
    return chain, settings.animator_effort_quiz


def _request(
    settings: Settings, name: str, message: Message, schema_name: str, schema: dict[str, Any], label: str
) -> LLMRequest:
    prompt = load_prompt(name)
    models, effort = models_for(settings)
    return LLMRequest(
        stage="quiz",
        prompt_version=prompt.version,
        instructions=prompt.text,
        messages=[message],
        schema_name=schema_name,
        schema=schema,
        models=models,
        effort=effort,
        max_output_tokens=8_000,
        label=label,
        cache_key=f"animator-{prompt.version}",
    )


async def _answer[M: BaseModel](
    llm: LLMClient, request: LLMRequest, model: type[M], check: Any, ctx: CallContext | None, tries: int = 2
) -> M:
    """A call whose answer must parse and pass `check` (which raises ValueError); retried once with
    the error shown."""
    base = request.messages[0]
    last: Exception | None = None
    for n in range(tries):
        if last is not None:
            request.messages = [
                base,
                Message.user(f"Your previous answer could not be used: {last}. Answer again."),
            ]
            request.label = f"{request.label} (retry {n})"
        try:
            out = model.model_validate((await llm.generate(request, ctx)).data)
            check(out)
            return out
        except (ValidationError, ValueError, LLMBadOutput, LLMRefusal, LLMIncomplete) as e:
            last = e
    assert last is not None
    raise last


def questions_for(seconds: float) -> int:
    """How many questions a quiz on a lesson this long asks."""
    return QUESTIONS.get(tier_for(seconds).id, 6)


def _check_quiz(n: int) -> Any:
    def check(q: Any) -> None:
        if len(q.questions) != n:
            raise ValueError(f"write exactly {n} questions, not {len(q.questions)}")
        if len({x.id for x in q.questions}) != n:
            raise ValueError("every question needs its own id")
        for x in q.questions:
            if len(x.choices) != 4 or len(set(x.choices)) != 4:
                raise ValueError(f"question {x.id} needs exactly 4 different choices")
            if not 0 <= x.answer <= 3:
                raise ValueError(f"question {x.id}: the answer is a position from 0 to 3")

    return check


async def make_quiz(
    llm: LLMClient,
    settings: Settings,
    topic: str,
    audience: str,
    seconds: float,
    ctx: CallContext | None = None,
    questions: int | None = None,
) -> Quiz:
    """The quiz for a lesson of this length on this topic (one call): as many questions as its depth
    teaches ideas, unless `questions` says."""
    tier = tier_for(seconds)
    questions = questions or questions_for(seconds)
    message = Message.user(
        "\n".join(
            [
                f"Topic: {topic}",
                f"Audience: {audience}",
                f"Lesson length: {round(seconds)} seconds. Depth: {tier.name}: {tier.brief}",
                f"Questions: {questions}",
            ]
        )
    )
    request = _request(settings, "teachquiz", message, "Quiz", QUIZ_SCHEMA, "teachquiz quiz")
    written = await _answer(llm, request, _Written, _check_quiz(questions), ctx)
    return Quiz(topic=topic, audience=audience, seconds=round(seconds), questions=written.questions)


def _check_answers(quiz: Quiz) -> Any:
    def check(a: Any) -> None:
        ids = {q.id for q in quiz.questions}
        got = {x.id for x in a.answers}
        if not ids <= got:
            raise ValueError(f"answer every question: missing {', '.join(sorted(ids - got))}")

    return check


async def take_quiz(
    llm: LLMClient, settings: Settings, quiz: Quiz, material: Material, ctx: CallContext | None = None
) -> QuizAnswers:
    """The student's answers, from the lesson alone (one call; the pictures as small images)."""
    lines = ["The lesson, scene by scene:"]
    for i, said in enumerate(material.scenes, 1):
        lines.append(f"Scene {i}: {said}")
    if material.pictures:
        lines.append(
            f"{len(material.pictures)} pictures follow, one a scene, in order (picture n is scene n)."
        )
    lines.append("")
    lines.append("The quiz:")
    for q in quiz.questions:
        options = " | ".join(f"{i}: {c}" for i, c in enumerate(q.choices))
        lines.append(f"{q.id}. {q.question} Choices: {options}")
    parts: list[Any] = ["\n".join(lines), *(ImagePart(png, "low") for png in material.pictures)]
    request = _request(
        settings, "student", Message.user(*parts), "QuizAnswers", ANSWERS_SCHEMA, "teachquiz answers"
    )
    return await _answer(llm, request, QuizAnswers, _check_answers(quiz), ctx)


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold())


def supported(answer: QuizAnswer, material: Material) -> bool:
    """Whether the lesson backs the answer: the quote is the narrator's own words (most of its word
    pairs said in the lesson), or the picture named was shown."""
    if 1 <= answer.picture <= len(material.pictures):
        return True
    quote = _words(answer.evidence)
    if len(quote) < 2:
        return False
    said = _words(" ".join(material.scenes))
    pairs = set(pairwise(said))
    mine = list(pairwise(quote))
    return sum(p in pairs for p in mine) / len(mine) >= QUOTE_SHARE


def score(quiz: Quiz, answers: QuizAnswers, material: Material) -> dict[str, Any]:
    """TeachQuiz: the share of questions answered right with the lesson's support (0 to 100), and what
    went into it."""
    by_id = {a.id: a for a in answers.answers}
    rows = []
    for q in quiz.questions:
        a = by_id.get(q.id)
        right = a is not None and a.choice == q.answer
        backed = a is not None and supported(a, material)
        rows.append(
            {
                "id": q.id,
                "concept": q.concept,
                "right": right,
                "supported": backed,
                "not_taught": a is None or a.choice == -1,
                "evidence": (a.evidence if a else "")[:200],
                "picture": a.picture if a else 0,
            }
        )
    n = len(rows) or 1
    taught = sum(1 for r in rows if r["right"] and r["supported"])
    return {
        "teachquiz": round(100 * taught / n),
        "questions": len(rows),
        "right_and_supported": taught,
        "right": sum(1 for r in rows if r["right"]),
        "not_taught": sum(1 for r in rows if r["not_taught"]),
        "pictures": len(material.pictures),
        "per_question": rows,
    }


async def teachquiz(
    llm: LLMClient,
    settings: Settings,
    gen: Generation,
    quiz: Quiz | None = None,
    pictures: Sequence[bytes] = (),
) -> tuple[dict[str, Any], Quiz]:
    """A lesson's TeachQuiz score (and the quiz, made when not given), with what its calls cost."""
    costs: list[float] = []
    ctx = CallContext(on_record=lambda r: costs.append(r.cost_usd or 0.0))
    seconds = gen.request.duration_min * 60  # the length asked for: lessons asked alike share a quiz
    if quiz is None:
        quiz = await make_quiz(llm, settings, gen.request.topic, gen.request.audience, seconds, ctx)
    material = material_of(gen, pictures)
    answers = await take_quiz(llm, settings, quiz, material, ctx)
    result = score(quiz, answers, material)
    result["cost_usd"] = round(sum(costs), 5)
    return result, quiz
