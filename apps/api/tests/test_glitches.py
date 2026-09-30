"""Visual glitch cleanup (ADR-040): the polish applies the engine's new hints (a wrapped row widened,
hidden text uncovered, content brought back into the stage), and a valid scene that still shows a
glitch gets one patch round with the engine's measured fixes, kept only when it helps."""

from __future__ import annotations

import copy
from typing import Any

from animator.engine_client import EngineClient, EngineWorkerPool
from animator.llm import LLMRequest
from animator.pipeline.generator import GLITCHES, glitches_of
from animator.pipeline.stages import format_issues
from animator.pipeline.tidy import autofix
from tests.test_pipeline import SCENES, Script, run, settings

JAR = {
    "id": "jar",
    "type": "container",
    "params": {"shape": "beaker", "width": 200, "height": 240, "level": 0.5},
    "place": {"x": 1350, "y": 420},
}


def pebble(y: float) -> dict[str, Any]:
    return {
        "id": "pebble",
        "type": "block",
        "params": {"width": 80, "height": 80},
        "place": {"x": 1350, "y": y},
    }


def with_pebble(y: float) -> dict[str, Any]:
    """Scene 1 with a jar and a pebble; at y=560 the pebble sticks out through the jar's floor."""
    s: dict[str, Any] = copy.deepcopy(SCENES[0])
    s["objects"] += [copy.deepcopy(JAR), pebble(y)]
    s["timeline"] += [
        {"id": "mk_jar", "action": "create", "target": "jar", "start": {"at": 0.5}},
        {"id": "mk_pebble", "action": "create", "target": "pebble", "start": {"at": 0.6}},
    ]
    return s


# ---------------------------------------------------------------------- the polish


def test_autofix_widens_a_wrapped_row_and_moves_what_the_engine_says() -> None:
    dsl = {
        "objects": [
            {
                "id": "row",
                "type": "text",
                "params": {"text": "1 2 3", "maxWidth": 300},
                "place": {"x": 800, "y": 400},
            },
            {"id": "note", "type": "label", "params": {"text": "hi"}, "place": {"x": 1500, "y": 400}},
        ],
        "timeline": [],
    }
    issues = [
        {
            "code": "TEXT_WRAPPED",
            "path": "/objects/row",
            "severity": "warning",
            "fix": {"dx": 0, "dy": 0, "params": {"maxWidth": 900}},
        },
        {"code": "OUT_OF_STAGE", "path": "/objects/note", "severity": "warning", "fix": {"dx": -60, "dy": 0}},
    ]
    fixed = autofix(dsl, issues)
    assert fixed is not None
    row, note = fixed["objects"]
    assert row["params"]["maxWidth"] == 900 and row["place"] == {"x": 800, "y": 400}
    assert note["place"] == {"x": 1440, "y": 400}


def test_repair_messages_carry_the_engines_measured_fix() -> None:
    text = format_issues(
        [
            {
                "code": "OUT_OF_STAGE",
                "path": "/objects/v",
                "message": "'v' reaches 16 px outside the stage.",
                "fix": {"dx": -24, "dy": 0},
            },
            {
                "code": "TEXT_WRAPPED",
                "path": "/objects/row",
                "message": "wraps",
                "fix": {"dx": 0, "dy": 0, "params": {"maxWidth": 1100}},
            },
        ],
        hints=True,
    )
    assert "[engine: move it by about (-24, 0) px]" in text and "[engine: set maxWidth to 1100]" in text
    assert "[engine" not in format_issues([{"code": "X", "message": "m", "fix": {"dx": 5, "dy": 0}}])


# ---------------------------------------------------------------------- the repair round


async def glitch_count(pool: EngineWorkerPool, dsl: dict[str, Any]) -> list[str]:
    res = await EngineClient(pool).compile(dsl, None)
    assert res.ok, [i.message for i in res.issues if i.severity == "error"]
    return [i.code for i in glitches_of(res)]


async def test_the_glitch_scene_really_has_a_glitch_the_polish_cannot_fix(pool: EngineWorkerPool) -> None:
    assert "LEAVES_CONTAINER" in await glitch_count(pool, with_pebble(560))
    assert await glitch_count(pool, with_pebble(470)) == []
    assert {"HIDDEN_TEXT", "OUT_OF_STAGE", "TEXT_WRAPPED", "LEAVES_CONTAINER"} <= GLITCHES


def scripted(repair: dict[str, Any] | None) -> Script:
    """Scene 1 comes with the pebble through the jar's floor; its next call returns `repair`."""

    def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        if i == 0:
            return with_pebble(560) if attempt == 1 else copy.deepcopy(repair)
        return copy.deepcopy(SCENES[i])

    return Script(scene)


async def test_a_glitch_the_polish_cannot_fix_gets_one_patch_round(pool: EngineWorkerPool) -> None:
    script = scripted({"objects": [pebble(470)]})  # the patch: only the pebble, moved up into the jar
    gen, _ = await run(pool, script)
    s1 = gen.scenes[0]
    assert s1.status == "ready" and len(s1.attempts) == 2
    assert [e["code"] for e in s1.attempts[1].errors] == ["LEAVES_CONTAINER"] and s1.attempts[1].ok
    req = next(r for r in script.calls if r.label == "scene 1 attempt 2")
    text = req.messages[0].parts[-1].text  # type: ignore[union-attr]
    assert req.stage == "correction" and "visual glitches" in text and "LEAVES_CONTAINER" in text
    assert (
        s1.dsl is not None and next(o for o in s1.dsl["objects"] if o["id"] == "pebble")["place"]["y"] == 470
    )
    assert gen.metrics.glitch_repairs == 1 and gen.metrics.glitches_left == 0
    assert gen.metrics.corrections == 0  # a valid scene cleaned up is not a failed attempt


async def test_a_repair_that_does_not_help_is_dropped(pool: EngineWorkerPool) -> None:
    gen, _ = await run(pool, scripted({"objects": [pebble(560)]}))  # still through the floor
    s1 = gen.scenes[0]
    assert s1.status == "ready" and s1.attempts[-1].ok is False
    assert (
        s1.dsl is not None and next(o for o in s1.dsl["objects"] if o["id"] == "pebble")["place"]["y"] == 560
    )
    # Scenes 2-4 carry the jar and pebble over (continuity) and try their own repair too.
    assert gen.metrics.glitch_repairs >= 1 and gen.metrics.glitches_left >= 1


async def test_the_repair_round_can_be_switched_off(pool: EngineWorkerPool) -> None:
    script = scripted({"objects": [pebble(470)]})
    gen, _ = await run(pool, script, settings(animator_glitch_repair=False))
    assert not any(r.label == "scene 1 attempt 2" for r in script.calls)
    assert len(gen.scenes[0].attempts) == 1 and gen.metrics.glitch_repairs == 0
