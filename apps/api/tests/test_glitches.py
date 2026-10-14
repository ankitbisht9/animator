"""Visual glitch cleanup (ADR-040): the polish applies the engine's new hints (a wrapped row widened,
hidden text uncovered, content brought back into the stage). ADR-042 reverted the model repair round:
a valid scene is kept as the polish leaves it, with no extra model call."""

from __future__ import annotations

import copy
from typing import Any

from animator.engine_client import EngineClient, EngineWorkerPool
from animator.llm import LLMRequest
from animator.pipeline.generator import GLITCHES, UNTIDY, glitches_of
from animator.pipeline.tidy import autofix
from tests.test_pipeline import SCENES, Script, run

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


# ---------------------------------------------------------------------- no model repair round (ADR-042)


async def glitch_count(pool: EngineWorkerPool, dsl: dict[str, Any]) -> list[str]:
    res = await EngineClient(pool).compile(dsl, None)
    assert res.ok, [i.message for i in res.issues if i.severity == "error"]
    return [i.code for i in glitches_of(res)]


async def test_the_glitch_scene_really_has_a_glitch_the_polish_cannot_fix(pool: EngineWorkerPool) -> None:
    assert "LEAVES_CONTAINER" in await glitch_count(pool, with_pebble(560))
    assert await glitch_count(pool, with_pebble(470)) == []
    assert {"HIDDEN_TEXT", "OUT_OF_STAGE", "TEXT_WRAPPED", "LEAVES_CONTAINER"} <= GLITCHES


async def test_nodes_piled_on_one_spot_are_a_glitch_the_polish_moves_clear(pool: EngineWorkerPool) -> None:
    """ADR-060: commit D' drawn right on commit D, each labelled on its own, is a glitch (the overlap
    check used to skip them: one id prefix). The engine's hint moves D' clear, its label following."""
    s: dict[str, Any] = copy.deepcopy(SCENES[0])
    commits = [
        {"id": "commit_d", "type": "circle", "params": {"radius": 34}, "place": {"x": 1300, "y": 420}},
        {"id": "commit_d_prime", "type": "circle", "params": {"radius": 34}, "place": {"x": 1300, "y": 420}},
        {
            "id": "d_label",
            "type": "label",
            "params": {"text": "D"},
            "place": {"relativeTo": "commit_d", "side": "above"},
        },
        {
            "id": "d2_label",
            "type": "label",
            "params": {"text": "D'"},
            "place": {"relativeTo": "commit_d_prime", "side": "below"},
        },
    ]
    s["objects"] += commits
    s["timeline"] += [
        {"id": f"mk_{o['id']}", "action": "create", "target": o["id"], "start": {"at": 0.5}} for o in commits
    ]
    engine = EngineClient(pool)
    res = await engine.compile(s, None)
    assert res.ok, [i.message for i in res.issues if i.severity == "error"]
    piles = [i for i in glitches_of(res) if i.code == "NODE_OVERLAP"]
    assert piles and piles[0].fix is not None and "NODE_OVERLAP" in UNTIDY
    fixed = autofix(s, [i.model_dump() for i in piles])
    assert fixed is not None
    again = await engine.compile(fixed, None)
    assert again.ok and not any(i.code == "NODE_OVERLAP" for i in again.issues)


async def test_a_branch_line_beside_its_commits_is_moved_onto_them(pool: EngineWorkerPool) -> None:
    """ADR-060 (seen in a fresh Git lesson): a branch drawn as a path whose `d` starts at its place
    (`M 0 0 L 145 50 L 290 50` at the first commit) is drawn centred there, beside its commits. The
    engine's hint moves it onto them: every corner then sits on a commit."""
    s: dict[str, Any] = copy.deepcopy(SCENES[0])
    commits = [
        {"id": f"commit_{i}", "type": "circle", "params": {"radius": 22}, "place": {"x": x, "y": y}}
        for i, (x, y) in enumerate([(900, 500), (1045, 550), (1190, 550)])
    ]
    lane = {
        "id": "rebase_path",
        "type": "path",
        "params": {"d": "M 0 0 L 145 50 L 290 50", "closed": False},
        "place": {"x": 900, "y": 500, "anchor": "center"},
    }
    s["objects"] += [*commits, lane]
    s["timeline"] += [
        {"id": f"mk_{o['id']}", "action": "create", "target": o["id"], "start": {"at": 0.5}}
        for o in [*commits, lane]
    ]
    engine = EngineClient(pool)
    res = await engine.compile(s, None)
    assert res.ok, [i.message for i in res.issues if i.severity == "error"]
    off = [i for i in glitches_of(res) if i.code == "PATH_OFF_ITS_POINTS"]
    assert off and "PATH_OFF_ITS_POINTS" in UNTIDY
    fixed = autofix(s, [i.model_dump() for i in off])
    assert fixed is not None
    moved = next(o for o in fixed["objects"] if o["id"] == "rebase_path")
    assert moved["place"] == {"x": 1045.0, "y": 525.0, "anchor": "center"}
    again = await engine.compile(fixed, None)
    assert again.ok and not any(i.code == "PATH_OFF_ITS_POINTS" for i in again.issues)


def scripted(repair: dict[str, Any] | None) -> Script:
    """Scene 1 comes with the pebble through the jar's floor; any next call would return `repair`."""

    def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        if i == 0:
            return with_pebble(560) if attempt == 1 else copy.deepcopy(repair)
        return copy.deepcopy(SCENES[i])

    return Script(scene)


async def test_a_valid_scene_with_a_glitch_gets_no_extra_model_call(pool: EngineWorkerPool) -> None:
    script = scripted({"objects": [pebble(470)]})
    gen, _ = await run(pool, script)
    s1 = gen.scenes[0]
    assert s1.status == "ready" and len(s1.attempts) == 1
    assert not any(r.label == "scene 1 attempt 2" for r in script.calls)
    assert (
        s1.dsl is not None and next(o for o in s1.dsl["objects"] if o["id"] == "pebble")["place"]["y"] == 560
    )
    assert gen.metrics.glitch_repairs == 0 and gen.metrics.glitches_left >= 1
