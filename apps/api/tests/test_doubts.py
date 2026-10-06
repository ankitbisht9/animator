"""Doubts (ADR-050): a learner raises a hand mid-lesson and the tutor answers in one model call. The
answer reuses the lesson's own picture (the moment the hand went up, the things talked about glowing)
or shows a board; it fits a share of the lesson's length; a follow-up sees the conversation; the lesson
itself is never changed; there is a budget and a limit on follow-ups."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from animator.api.app import create_app
from animator.doubts.context import answer_cap, lesson_block, words_for
from animator.doubts.models import AskRequest, DoubtAnswer
from animator.doubts.service import DoubtError, DoubtService
from animator.engine_client import EngineClient, EngineWorkerPool
from animator.jobs.store import MemoryStore
from animator.llm import FakeLLM, LLMRequest
from animator.pipeline.models import Generation
from animator.tts import FakeTTS, SpeechCache
from tests.test_generations_api import read_events
from tests.test_pipeline import Script, run, settings


def picture(focus: list[str], **kw: Any) -> dict[str, Any]:
    return {
        "kind": "lesson",
        "depth": "standard",
        "title": "Why it floats",
        "visual": {"mode": "scene", "scene": 2, "icon": None, "equation": None, "equation_beat": None},
        "beats": [
            {
                "text": "Look at this part first.",
                "spoken": None,
                "tone": "warm",
                "focus": focus[:1],
                "note": "Look here first",
            },
            {
                "text": "The water pushes it **up**.",
                "spoken": None,
                "tone": "thoughtful",
                "focus": focus[1:2],
                "note": None,
            },
            {
                "text": "That is why it floats. Let's keep going.",
                "spoken": None,
                "tone": "warm",
                "focus": ["not_on_screen"],
                "note": None,
            },
        ],
        "suggestion": None,
        **kw,
    }


BOARD = {
    "kind": "related",
    "depth": "quick",
    "title": "Density in short",
    "visual": {
        "mode": "board",
        "scene": None,
        "icon": "balance",
        "equation": [
            {"id": "rho", "tex": "\\rho"},
            {"id": "eq", "tex": "="},
            {"id": "mv", "tex": "\\frac{m}{V}"},
        ],
        "equation_beat": 1,
    },
    "beats": [
        {
            "text": "Density is mass divided by volume.",
            "spoken": None,
            "tone": "thoughtful",
            "focus": [],
            "note": "Density = mass / volume",
        },
        {
            "text": "Things less dense than water float.",
            "spoken": None,
            "tone": "thoughtful",
            "focus": [],
            "note": "Less dense floats",
        },
        {
            "text": "It is a topic of its own: you can make a lesson on it.",
            "spoken": None,
            "tone": "warm",
            "focus": [],
            "note": None,
        },
    ],
    "suggestion": "Density",
}


class Tutor(Script):
    """The fake model: the lesson as usual, and doubts answered from a queue."""

    def __init__(self) -> None:
        super().__init__()
        self.doubts: list[Any] = []
        self.doubt_calls: list[LLMRequest] = []

    def __call__(self, req: LLMRequest) -> Any:
        if req.stage == "doubt":
            self.doubt_calls.append(req)
            return copy.deepcopy(self.doubts.pop(0))
        return super().__call__(req)


def on_screen(gen: Generation, k: int) -> list[str]:
    state = gen.scenes[k].end_state or {}
    return [
        oid
        for oid, o in state["objects"].items()
        if o["lifecycle"] == "active" and o["visible"] and not oid.startswith("title")
    ]


async def lesson(pool: EngineWorkerPool, tutor: Tutor, **kw: Any) -> tuple[Generation, MemoryStore]:
    store = MemoryStore()
    gen, _ = await run(pool, tutor, store=store)
    assert gen.status == "complete"
    return gen, store


def service(
    pool: EngineWorkerPool, tutor: Tutor, store: MemoryStore, speech: SpeechCache | None = None, **kw: Any
) -> DoubtService:
    s = settings(animator_storage_backend="memory", **kw)
    return DoubtService(EngineClient(pool), FakeLLM(tutor, cost=0.01), s, speech, store)


# ---------------------------------------------------------------------- pure


def test_an_answer_takes_at_most_a_share_of_the_lesson() -> None:
    assert answer_cap(60, 0.5, 20, 60) == 30  # a one-minute lesson: half a minute at most
    assert answer_cap(20, 0.5, 20, 60) == 20  # never below the least
    assert answer_cap(600, 0.5, 20, 60) == 60  # never above the most
    assert words_for(30) == 69


# ---------------------------------------------------------------------- with the engine


async def test_a_doubt_about_the_picture_reuses_it_and_a_follow_up_sees_the_conversation(
    pool: EngineWorkerPool,
) -> None:
    tutor = Tutor()
    gen, store = await lesson(pool, tutor)
    before = gen.model_dump_json()
    shown = on_screen(gen, 1)
    tutor.doubts = [picture(shown), picture(shown, title="Simpler")]
    svc = service(pool, tutor, store)

    first = await svc.ask(gen.id, AskRequest(question="Why does it go up?", scene_index=1, t=2.0))
    assert first["visual"] == {"mode": "scene", "scene": 2}
    assert first["prev_state"] is not None and first["prev_state"]["sceneId"] == gen.scenes[1].id
    glows = [a for a in first["scene"]["timeline"] if a["action"] == "highlight"]
    assert [g["target"] for g in glows] == shown[:2]  # an id not on screen is not pointed at
    assert [o["type"] for o in first["scene"]["objects"]] == ["callout"]  # the note, pointing at shown[0]
    assert first["sentences"][1] == "The water pushes it **up**." and first["narration"] is None

    ask = tutor.doubt_calls[0]
    lesson_text, turn_text = (m.parts[0].text for m in ask.messages)  # type: ignore[union-attr]
    assert "LESSON" in lesson_text and gen.scenes[0].title in lesson_text
    assert "On screen right now (scene 2)" in turn_text and "Why does it go up?" in turn_text
    assert ask.models == ["m-board", "m-scene"] or ask.models[0] == "m-board"

    second = await svc.ask(
        gen.id,
        AskRequest(question="I still don't get it.", scene_index=0, t=0, session_id=first["session_id"]),
    )
    assert second["session_id"] == first["session_id"] and second["n"] == 2
    follow = tutor.doubt_calls[1].messages[1].parts[0].text  # type: ignore[union-attr]
    assert "THIS DOUBT SO FAR" in follow and "Why does it go up?" in follow and "turn 2" in follow
    assert "more simply" in follow
    # The same moment of the lesson (the hand went up once), the same lesson message (cached).
    assert second["prev_state"]["time"] == first["prev_state"]["time"]
    assert tutor.doubt_calls[1].messages[0] == tutor.doubt_calls[0].messages[0]
    assert (await store.get(gen.id)).model_dump_json() == before  # type: ignore[union-attr]
    saved = svc.get(gen.id, first["session_id"])
    assert saved is not None and [t["question"] for t in saved["turns"]] == [
        "Why does it go up?",
        "I still don't get it.",
    ]


async def test_a_concept_gets_a_board_and_a_related_topic_a_suggestion(pool: EngineWorkerPool) -> None:
    tutor = Tutor()
    gen, store = await lesson(pool, tutor)
    tutor.doubts = [BOARD]
    svc = service(pool, tutor, store)
    got = await svc.ask(gen.id, AskRequest(question="What is density?", scene_index=0, t=1.0))
    assert got["visual"]["mode"] == "board" and got["prev_state"] is None
    ids = [o["id"] for o in got["scene"]["objects"]]
    assert ids[:3] == ["doubt_title", "doubt_icon", "doubt_eq"] and "doubt_point_2" in ids
    assert got["kind"] == "related" and got["suggestion"] == "Density"


async def test_an_answer_fits_its_time_and_an_unrelated_doubt_gets_the_board(pool: EngineWorkerPool) -> None:
    tutor = Tutor()
    gen, store = await lesson(pool, tutor)
    long = picture(on_screen(gen, 1), kind="unrelated")
    long["beats"] = long["beats"] * 3 + [
        {"text": " ".join(["word"] * 80), "spoken": None, "tone": "neutral", "focus": [], "note": None}
    ]
    tutor.doubts = [long]
    svc = service(
        pool, tutor, store, animator_doubt_min_s=5, animator_doubt_max_s=10, animator_doubt_share=0.1
    )
    got = await svc.ask(gen.id, AskRequest(question="Who won the match?", scene_index=1, t=1.0))
    assert got["visual"]["mode"] == "board"  # nothing of the lesson to point at
    assert 5 <= got["cap_s"] <= 10  # a tenth of the lesson, within [5, 10] s
    assert sum(len(s.split()) for s in got["sentences"]) <= words_for(got["cap_s"]) * 1.25


async def test_a_spoken_answer_is_timed_to_its_voice(pool: EngineWorkerPool, tmp_path: Path) -> None:
    tutor = Tutor()
    gen, store = await lesson(pool, tutor)
    tutor.doubts = [BOARD]
    speech = SpeechCache(FakeTTS(seconds_per_word=0.4), tmp_path)
    svc = service(pool, tutor, store, speech)
    got = await svc.ask(gen.id, AskRequest(question="What is density?", scene_index=0, t=1.0))
    beats = got["narration"]["beats"]
    assert len(beats) == 3 and len({b["audio"] for b in beats}) == 1  # one take
    words = sum(len(s.split()) for s in got["sentences"])
    assert got["duration"] >= words * 0.4  # the scene lasts as long as its voice
    phrases = await svc.phrases("Hinglish")
    assert phrases["greeting"][0]["audio"] and phrases["greeting"][0]["text"].startswith("Arre")


async def test_the_last_sentence_keeps_its_glow_when_the_voice_is_quick(
    pool: EngineWorkerPool, tmp_path: Path
) -> None:
    """Glows are sized to each sentence: one on the last sentence never outlasts the scene, even when
    the voice says it faster than estimated (the scene is then fitted, shorter, to the voice)."""
    tutor = Tutor()
    gen, store = await lesson(pool, tutor)
    shown = on_screen(gen, 1)
    answer = picture(shown)
    answer["beats"][2]["focus"] = shown[:2]
    tutor.doubts = [answer]
    speech = SpeechCache(FakeTTS(seconds_per_word=0.2), tmp_path)
    got = await service(pool, tutor, store, speech).ask(
        gen.id, AskRequest(question="Why?", scene_index=1, t=1.0)
    )
    glows = [a["id"] for a in got["scene"]["timeline"] if a["action"] == "highlight"]
    assert "doubt_glow_3_1" in glows and got["narration"] is not None


async def test_doubts_have_a_budget_and_a_limit_on_follow_ups(pool: EngineWorkerPool) -> None:
    tutor = Tutor()
    gen, store = await lesson(pool, tutor)
    shown = on_screen(gen, 1)
    tutor.doubts = [picture(shown) for _ in range(4)]
    svc = service(pool, tutor, store, animator_doubt_max_turns=2, animator_doubt_budget_usd=0.025)
    first = await svc.ask(gen.id, AskRequest(question="Why?", scene_index=1, t=1.0))
    await svc.ask(
        gen.id, AskRequest(question="Why again?", scene_index=1, t=1.0, session_id=first["session_id"])
    )
    with pytest.raises(DoubtError) as limit:
        await svc.ask(
            gen.id, AskRequest(question="And?", scene_index=1, t=1.0, session_id=first["session_id"])
        )
    assert limit.value.status == 429 and "many answers" in limit.value.message
    await svc.ask(gen.id, AskRequest(question="New doubt", scene_index=1, t=1.0))  # $0.03 spent now
    with pytest.raises(DoubtError) as budget:
        await svc.ask(gen.id, AskRequest(question="One more", scene_index=1, t=1.0))
    assert budget.value.status == 429 and "budget" in budget.value.message
    with pytest.raises(DoubtError) as missing:
        await svc.ask("g_000000000000", AskRequest(question="Hello", scene_index=0, t=0))
    assert missing.value.status == 404


async def test_the_lesson_context_is_what_was_said_and_shown(pool: EngineWorkerPool) -> None:
    tutor = Tutor()
    gen, _ = await lesson(pool, tutor)
    text = lesson_block(gen)
    for rec in gen.scenes:
        assert f'Scene {rec.index + 1} "{rec.title}"' in text
    first_beat = gen.scenes[0].dsl["narration"]["beats"][0]["text"]  # type: ignore[index]
    assert first_beat in text and "Picture at its end:" in text
    assert DoubtAnswer.model_validate(BOARD).visual.mode == "board"


# ---------------------------------------------------------------------- over HTTP


def test_doubts_over_http(worker_script: Path) -> None:
    tutor = Tutor()
    s = settings(openai_api_key="sk-proj-secret-value-that-must-never-leak")
    app = create_app(settings=s, llm=FakeLLM(tutor), store=MemoryStore(), resume_on_start=False)
    with TestClient(app) as client:
        assert client.get("/api/doubts/config").json() == {
            "enabled": True,
            "voice": False,
            "transcribe": True,
            "max_turns": 8,
        }
        phrases = client.get("/api/doubts/phrases", params={"language": "English"}).json()
        assert phrases["greeting"] and all(p["audio"] is None for p in phrases["greeting"])
        gen_id = client.post("/api/generations", json={"topic": "Archimedes' principle"}).json()["id"]
        read_events(client, gen_id)
        tutor.doubts = [BOARD]
        res = client.post(
            f"/api/doubts/{gen_id}", json={"question": "What is density?", "scene_index": 0, "t": 1.0}
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["kind"] == "related" and body["scene"]["id"].startswith("doubt_")
        assert "secret" not in json.dumps(body)
        got = client.get(f"/api/doubts/{gen_id}/{body['session_id']}").json()
        assert got["turns"][0]["question"] == "What is density?"
        assert (
            client.post(f"/api/doubts/{gen_id}", json={"question": "", "scene_index": 0, "t": 0}).status_code
            == 422
        )
        assert client.get(f"/api/doubts/{gen_id}/d_000000000000").status_code == 404
        # The lesson's own record and call log know nothing of doubts.
        calls = client.get(f"/api/generations/{gen_id}/calls").json()
        assert all(c["stage"] != "doubt" for c in calls)


def test_doubts_can_be_turned_off(worker_script: Path) -> None:
    s = settings(animator_doubts_enabled=False)
    app = create_app(settings=s, llm=FakeLLM(Script()), store=MemoryStore(), resume_on_start=False)
    with TestClient(app) as client:
        assert client.get("/api/doubts/config").json() == {"enabled": False}
        assert (
            client.post(
                "/api/doubts/g_000000000000", json={"question": "Hi", "scene_index": 0, "t": 0}
            ).status_code
            == 404
        )
