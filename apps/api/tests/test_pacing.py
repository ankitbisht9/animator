"""Keep it moving (ADR-067): a stretch of narration longer than five seconds with nothing changing on
screen gets a gentle highlight: on the thing the narration names, as it is named, or else a soft glow
on what the picture last showed. Never on words, pointers, groups or things not on screen; at most two
a scene; the engine checks the result and the pipeline keeps it only when the scene stays valid."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any

import pytest

from animator.engine_client import EngineClient, EngineWorkerPool
from animator.jobs.runner import GenerationRunner
from animator.jobs.store import MemoryStore
from animator.llm import FakeLLM, Usage
from animator.pipeline.generator import Pipeline
from animator.pipeline.models import GenerationRequest
from animator.pipeline.pacing import (
    MOST,
    PREFIX,
    PULSE_S,
    STILL_S,
    keep_moving,
    kept_moving,
    still_stretches,
)
from animator.tts import FakeTTS, SpeechCache, caption_words
from tests.test_teaching import Teaching, run_teaching, teaching

LONG = "It pushes water up the pipe all day, slowly and steadily, while the town sleeps."


def scene(
    objects: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    beats: list[tuple[float, str]],
    duration: float,
) -> dict[str, Any]:
    return {
        "dsl_version": "1.0",
        "id": "x",
        "title": "A pump",
        "duration": duration,
        "layout": "stage_with_caption",
        "narration": {
            "beats": [{"id": f"b{i + 1}", "text": text, "at": at} for i, (at, text) in enumerate(beats)]
        },
        "objects": [
            {
                "id": "title_s01",
                "type": "text",
                "params": {"text": "A pump", "size": "heading", "weight": "bold"},
                "place": {"slot": "title_bar"},
                "layer": "overlay",
            },
            *objects,
        ],
        "timeline": [{"id": "t0", "action": "write", "target": "title_s01", "start": {"at": 0.2}}, *timeline],
    }


def rect(oid: str, x: float, w: float = 240, h: float = 160, **kw: Any) -> dict[str, Any]:
    return {
        "id": oid,
        "type": "rectangle",
        "params": {"width": w, "height": h},
        "place": {"x": x, "y": 340},
        **kw,
    }


def circle(oid: str, x: float, r: float = 40, **kw: Any) -> dict[str, Any]:
    return {"id": oid, "type": "circle", "params": {"radius": r}, "place": {"x": x, "y": 340}, **kw}


def create(oid: str, at: float) -> dict[str, Any]:
    return {"id": f"c_{oid}", "action": "create", "target": oid, "start": {"at": at}}


async def compiled(
    pool: EngineWorkerPool, dsl: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    res = await EngineClient(pool).compile(dsl, None)
    assert res.ok and res.compiled is not None, [i.message for i in res.issues]
    return res.compiled, [b.model_dump() for b in res.beats or []]


def added(dsl: dict[str, Any]) -> list[dict[str, Any]]:
    return [a for a in dsl["timeline"] if str(a.get("id", "")).startswith(PREFIX)]


def longest_still(comp: dict[str, Any], beats: list[dict[str, Any]]) -> float:
    return max((b - a for a, b in still_stretches(comp, beats)), default=0.0)


# ---------------------------------------------------------------------- what counts as still


async def test_still_stretches_are_the_narration_with_nothing_changing(pool: EngineWorkerPool) -> None:
    dsl = scene(
        [rect("pump", 500), circle("ball", 900)],
        [
            create("pump", 0.0),
            create("ball", 0.1),
            {"id": "pause", "action": "wait", "start": {"at": 3.0}},  # waiting changes nothing
            {
                "id": "roll",
                "action": "move",
                "target": "ball",
                "by": {"x": -60, "y": 0},
                "start": {"at": 6.0},
                "duration": 1,
            },
        ],
        [(0.0, "Here is the pump."), (5.0, LONG)],
        12,
    )
    comp, beats = await compiled(pool, dsl)
    (a1, b1), (a2, b2) = still_stretches(comp, beats)
    assert 0.7 < a1 < 2.0 and b1 == pytest.approx(6.0)  # after the entrances, until the ball rolls
    assert a2 == pytest.approx(7.3) and b2 == pytest.approx(12.0)  # the roll, as the eye still sees it


async def test_a_binding_to_time_and_particles_keep_the_picture_moving(pool: EngineWorkerPool) -> None:
    swing = {
        "id": "swing",
        "action": "bind",
        "target": "ball",
        "property": "x",
        "bind": "900 + 40*sin(2*t)",
        "start": {"at": 2.0},
    }
    dsl = scene(
        [rect("pump", 500), circle("ball", 900)],
        [create("pump", 0.0), create("ball", 0.1), swing],
        [(0.0, LONG)],
        12,
    )
    comp, beats = await compiled(pool, dsl)
    assert longest_still(comp, beats) < 2.0  # only before the swing starts
    # A binding that does not depend on time changes nothing after it is set.
    still = copy.deepcopy(dsl)
    still["timeline"][-1]["bind"] = "900 + 40"
    comp, beats = await compiled(pool, still)
    assert longest_still(comp, beats) > STILL_S


# ---------------------------------------------------------------------- where the highlights go


async def test_a_long_still_stretch_gets_a_soft_glow_on_what_the_picture_last_showed(
    pool: EngineWorkerPool,
) -> None:
    dsl = scene(
        [circle("ball", 900), rect("pump", 500)],
        [create("ball", 0.1), create("pump", 1.0)],
        [(0.0, "Here are the ball and the pump."), (4.5, LONG)],
        9,
    )
    before = copy.deepcopy(dsl)
    comp, beats = await compiled(pool, dsl)
    assert longest_still(comp, beats) > STILL_S
    moved = keep_moving(dsl, comp, beats)
    assert moved is not None and dsl == before  # the scene given is left as it was
    [a] = added(moved)
    assert a["action"] == "highlight" and a["target"] == "pump" and a["style"] == "glow"
    assert a["duration"] == PULSE_S and a["start"]["at"].startswith("beat:")
    comp2, beats2 = await compiled(pool, moved)
    assert [b["start"] for b in beats2] == [b["start"] for b in beats]  # the voice's timing is kept
    assert longest_still(comp2, beats2) <= STILL_S
    assert kept_moving(moved) == 1 and kept_moving(dsl) == 0


async def test_the_highlight_lands_on_the_thing_as_the_narration_names_it(pool: EngineWorkerPool) -> None:
    dsl = scene(
        [circle("ball", 900), rect("pump", 500)],
        [create("ball", 0.1), create("pump", 1.0)],
        [
            (0.0, "Here are the ball and the pump."),
            (5.0, "Water fills the pipe, and then the ball floats up."),
        ],
        9,
    )
    words = {
        "b2": [
            ("Water", 0.0),
            ("fills", 0.4),
            ("the", 0.7),
            ("pipe,", 0.9),
            ("and", 1.3),
            ("then", 1.5),
            ("the", 1.7),
            ("ball", 1.85),
            ("floats", 2.2),
            ("up.", 2.6),
        ]
    }
    comp, beats = await compiled(pool, dsl)
    moved = keep_moving(dsl, comp, beats, words)
    assert moved is not None
    [a] = added(moved)
    # "ball" is said 1.85 s into the second sentence: the pulse starts just before it.
    assert a["target"] == "ball" and a["style"] == "pulse"
    assert a["start"] == {"at": "beat:b2", "delay": 1.7}
    comp2, beats2 = await compiled(pool, moved)
    assert longest_still(comp2, beats2) <= STILL_S


async def test_words_pointers_groups_and_things_gone_are_never_highlighted(pool: EngineWorkerPool) -> None:
    objects = [
        {"id": "pump_label", "type": "label", "params": {"text": "Pump"}, "place": {"x": 640, "y": 160}},
        {
            "id": "flow_arrow",
            "type": "arrow",
            "params": {"from": {"x": 300, "y": 600}, "to": {"x": 500, "y": 600}},
        },
        {"id": "cart", "type": "group", "params": {}, "place": {"x": 900, "y": 340}},
        rect("cart_body", 900, w=200, h=60, parent="cart"),
        circle("cart_wheel", 860, r=15, parent="cart"),
        circle("ghost", 300, r=30),
    ]
    timeline = [
        create("ghost", 0.0),
        {"id": "gone", "action": "remove", "target": "ghost", "start": {"at": 1.0}},
        create("cart", 0.2),
        create("cart_body", 0.2),
        create("cart_wheel", 0.6),  # popped in last
        create("pump_label", 1.2),
        create("flow_arrow", 1.4),
    ]
    dsl = scene(objects, timeline, [(0.0, "Here is the cart."), (4.0, LONG)], 10)
    comp, beats = await compiled(pool, dsl)
    words = {"b2": [("ghost", 1.0), ("pump", 2.0), ("arrow", 2.5)]}
    moved = keep_moving(dsl, comp, beats, words)
    assert moved is not None
    # Not the label or the arrow it names, not the ghost that has gone, not the group (which draws
    # nothing of its own) and not the wheel that popped in last: the cart's body.
    assert [(a["target"], a["style"]) for a in added(moved)] == [("cart_body", "glow")]
    await compiled(pool, moved)


async def test_a_long_stretch_gets_highlights_spread_through_it_two_at_most(pool: EngineWorkerPool) -> None:
    dsl = scene([rect("pump", 640)], [create("pump", 0.0)], [(0.0, "Here is the pump."), (4.0, LONG)], 13)
    comp, beats = await compiled(pool, dsl)
    assert 10 < longest_still(comp, beats) < 15
    moved = keep_moving(dsl, comp, beats)
    assert moved is not None and len(added(moved)) == 2
    comp2, beats2 = await compiled(pool, moved)
    assert longest_still(comp2, beats2) <= STILL_S  # no still part left over five seconds
    # A very long stillness still gets no more than two.
    dsl = scene([rect("pump", 640)], [create("pump", 0.0)], [(0.0, "Here is the pump."), (4.0, LONG)], 30)
    comp, beats = await compiled(pool, dsl)
    moved = keep_moving(dsl, comp, beats)
    assert moved is not None and len(added(moved)) == MOST
    starts = sorted(a["start"]["delay"] for a in added(moved))
    assert starts[1] - starts[0] >= STILL_S / 2


async def test_nothing_is_added_while_the_picture_keeps_changing(pool: EngineWorkerPool) -> None:
    hops = [
        {
            "id": f"hop{i}",
            "action": "move",
            "target": "ball",
            "by": {"x": 30, "y": 0},
            "start": {"at": 2.0 + 3.0 * i},
            "duration": 0.8,
        }
        for i in range(4)
    ]
    dsl = scene(
        [circle("ball", 500)], [create("ball", 0.0), *hops], [(0.0, "Watch the ball."), (5.0, LONG)], 13
    )
    comp, beats = await compiled(pool, dsl)
    assert longest_still(comp, beats) <= STILL_S
    assert keep_moving(dsl, comp, beats) is None
    # Nor when only words are on screen.
    words_only = scene([], [], [(0.0, LONG)], 10)
    comp, beats = await compiled(pool, words_only)
    assert keep_moving(words_only, comp, beats) is None


async def test_a_diagram_part_named_gets_its_ring(pool: EngineWorkerPool) -> None:
    cycle = {
        "id": "water_cycle",
        "type": "diagram",
        "params": {
            "nodes": [
                {"id": "rain", "label": "Rain"},
                {"id": "river", "label": "River"},
                {"id": "sea", "label": "Sea"},
            ],
            "edges": [{"from": "rain", "to": "river"}, {"from": "river", "to": "sea"}],
        },
        "place": {"slot": "stage.center"},
    }
    dsl = scene(
        [cycle],
        [create("water_cycle", 0.0)],
        [(0.0, "Here is the cycle."), (4.0, "Most of it runs down into a river, and on to the sea.")],
        9,
    )
    comp, beats = await compiled(pool, dsl)
    words = {
        "b2": [
            ("Most", 0.0),
            ("of", 0.3),
            ("it", 0.45),
            ("runs", 0.6),
            ("down", 0.9),
            ("into", 1.2),
            ("a", 1.4),
            ("river,", 1.5),
            ("and", 2.0),
            ("on", 2.2),
            ("to", 2.35),
            ("the", 2.5),
            ("sea.", 2.7),
        ]
    }
    moved = keep_moving(dsl, comp, beats, words)
    assert moved is not None
    [a] = added(moved)
    assert a["target"] == "water_cycle" and a["terms"] in (["river"], ["sea"]) and a["style"] == "pulse"
    await compiled(pool, moved)


async def test_ids_never_clash_with_the_scenes_own(pool: EngineWorkerPool) -> None:
    dsl = scene([rect("pump", 640)], [create("pump", 0.0)], [(0.0, "Here is the pump."), (4.0, LONG)], 10)
    dsl["timeline"][1]["id"] = f"{PREFIX}1"  # the scene already has an action with that id
    comp, beats = await compiled(pool, dsl)
    moved = keep_moving(dsl, comp, beats)
    assert moved is not None
    assert [a["id"] for a in added(moved)] == [f"{PREFIX}1", f"{PREFIX}2"]
    await compiled(pool, moved)


# ---------------------------------------------------------------------- in a lesson


def quiet_scene() -> dict[str, Any]:
    """A valid scene whose second sentence goes on with nothing changing on screen."""
    return scene(
        [rect("tank", 640, w=300, h=200)],
        [create("tank", 0.3)],
        [(0.0, "Look at the **tank**."), (4.0, LONG)],
        10,
    )


async def test_a_lesson_keeps_moving_while_the_narrator_talks(pool: EngineWorkerPool) -> None:
    script = Teaching()
    script.scene = lambda i, a, r: quiet_scene()
    gen, _ = await run_teaching(pool, script, None)
    assert gen.status == "complete", gen.error
    marks = [a for s in gen.scenes for a in added(s.dsl or {"timeline": []})]
    assert marks and all(a["target"] == "tank" and a["action"] == "highlight" for a in marks)
    assert gen.metrics.kept_moving == len(marks)
    assert all(s.status == "ready" for s in gen.scenes)


async def test_keeping_it_moving_can_be_switched_off(pool: EngineWorkerPool) -> None:
    script = Teaching()
    script.scene = lambda i, a, r: quiet_scene()
    gen, _ = await run_teaching(pool, script, None, animator_keep_moving=False)
    assert gen.status == "complete", gen.error
    assert not [a for s in gen.scenes for a in added(s.dsl or {"timeline": []})]
    assert gen.metrics.kept_moving == 0


def named_scene() -> dict[str, Any]:
    """A scene whose long second sentence names the pipe, then the tank, with nothing changing."""
    out = scene(
        [rect("tank", 640, w=300, h=200), rect("pipe", 640, w=420, h=30)],
        [create("tank", 0.3), create("pipe", 0.5)],
        [(0.0, "Look at the **tank**."), (4.0, LONG)],
        10,
    )
    out["objects"][2]["place"] = {"x": 640, "y": 520}
    beats = out["narration"]["beats"]
    beats[1]["text"] = (
        "Water runs in from the pipe all day long, and very slowly, bit by bit, the tank fills up to the top."
    )
    for b in beats:
        b.pop("at")
    return out


async def test_with_a_voice_each_highlight_lands_as_its_thing_is_named(
    pool: EngineWorkerPool, tmp_path: Path
) -> None:
    s = teaching(animator_tts_enabled=True, animator_tts_cache_dir=tmp_path)
    script = Teaching()
    script.scene = lambda i, a, r: named_scene()
    speech = SpeechCache(FakeTTS(seconds_per_word=0.5), tmp_path)
    fake = FakeLLM(script, usage=Usage(1000, 0, 500), cost=0.01)
    runner = GenerationRunner(MemoryStore(), Pipeline(EngineClient(pool), fake, s, None, speech), s)
    gen = await runner.start(GenerationRequest(topic="Archimedes' principle", duration_min=0.5))
    await asyncio.wait_for(runner._tasks[gen.id], timeout=120)
    gen = await runner.get(gen.id)
    assert gen is not None and gen.status == "complete", gen and gen.error
    for rec in gen.scenes:
        assert rec.dsl is not None and rec.narration is not None
        text = next(b["text"] for b in rec.dsl["narration"]["beats"] if b["id"] == "b2")
        voiced = next(b for b in rec.narration["beats"] if b["id"] == "b2")
        said = {w.strip(".,"): t[0] for w, t in zip(caption_words(text), voiced["words"], strict=True)}
        marks = added(rec.dsl)
        assert [(a["target"], a["style"]) for a in marks] == [("pipe", "pulse"), ("tank", "pulse")]
        for a in marks:
            assert a["start"]["at"] == "beat:b2"
            assert a["start"]["delay"] == pytest.approx(said[a["target"]] - 0.15, abs=0.02)
    assert gen.metrics.kept_moving == 2 * len(gen.scenes)
