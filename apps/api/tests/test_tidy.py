"""Deterministic fixes and patch corrections: no model round for slips with one obvious reading."""

from __future__ import annotations

import asyncio
import copy
from typing import Any

from animator.engine_client import EngineClient, EngineWorkerPool
from animator.jobs.runner import GenerationRunner
from animator.jobs.store import MemoryStore
from animator.llm import FakeLLM, LLMAuthError, LLMRequest
from animator.pipeline.generator import Pipeline
from animator.pipeline.models import GenerationRequest
from animator.pipeline.schemas import apply_patch, patch_schema
from animator.pipeline.tidy import autofix, tidy
from tests.test_pipeline import SCENES, Script, broken, run, settings, stages_for


def test_tidy_keeps_the_relative_start_a_model_meant() -> None:
    dsl: dict[str, Any] = {
        "timeline": [
            {"id": "a", "start": {"at": 0, "after": "x", "with": None, "delay": 0.2}},
            {"id": "b", "start": {"at": 1.5, "after": None, "with": "a"}},
            {"id": "c", "start": {"at": 2.0, "after": None, "with": None}},
        ]
    }
    starts = [a["start"] for a in tidy(dsl)["timeline"]]
    assert starts[0] == {"at": None, "after": "x", "with": None, "delay": 0.2}
    assert starts[1] == {"at": None, "after": None, "with": "a"}
    assert starts[2] == {"at": 2.0, "after": None, "with": None}


def test_tidy_writes_time_references_the_engine_way() -> None:
    def at(value: str) -> dict[str, Any]:
        return {"id": "a", "start": {"at": value, "after": None, "with": None}}

    dsl: dict[str, Any] = {
        "narration": {"beats": [{"id": "b1"}, {"id": "b2"}]},
        "cues": [{"id": "splash"}],
        "timeline": [at("b2"), at("Beat: b1"), at("b2_end"), at("splash"), at("beat-end:b1"), at("nowhere")],
    }
    got = [a["start"]["at"] for a in tidy(dsl)["timeline"]]
    assert got == ["beat:b2", "beat:b1", "beat_end:b2", "cue:splash", "beat_end:b1", "nowhere"]


def test_tidy_spells_tex_the_way_the_engine_supports() -> None:
    dsl = {
        "objects": [
            {
                "type": "equation",
                "params": {"parts": [{"id": "a", "tex": r"\Delta p \longrightarrow \textrm{low}\lt 1"}]},
            }
        ]
    }
    assert tidy(dsl)["objects"][0]["params"]["parts"][0]["tex"] == r"\Delta p \rightarrow \mathrm{low}< 1"


def test_autofix_fixes_sure_things_only() -> None:
    dsl: dict[str, Any] = {"duration": 10, "objects": [{"id": "t", "params": {"size": 300}}]}
    late = {"code": "ACTION_EXCEEDS_SCENE", "message": "Action 'a' ends at 10.3s; the scene lasts 10s."}
    big = {
        "code": "SCHEMA",
        "path": "/objects/0/params/size",
        "message": "Too big: expected number to be <=160",
    }
    fixed = autofix(dsl, [late, big])
    assert fixed is not None and fixed["duration"] == 10.6 and fixed["objects"][0]["params"]["size"] == 160
    assert dsl["duration"] == 10  # the original is untouched
    no_hint = {"code": "OUT_OF_BOUNDS", "path": "/objects/t", "message": "'t' is 30% outside"}
    partial = autofix(dsl, [late, no_hint])  # what has a sure fix is fixed; the model gets the rest
    assert partial is not None and partial["duration"] == 10.6
    way_late = {"code": "ACTION_EXCEEDS_SCENE", "message": "Action 'a' ends at 19s but the scene lasts 10s."}
    assert autofix(dsl, [way_late, no_hint]) is None
    assert autofix(dsl, []) is None


def test_autofix_brings_an_object_back_into_the_frame_as_the_engine_says() -> None:
    def obj(oid: str, place: dict[str, Any]) -> dict[str, Any]:
        return {"id": oid, "type": "block", "params": {}, "place": place, "scale": None}

    dsl = {
        "objects": [
            obj("slot", {"slot": "stage.center", "anchor": None, "offset": None}),
            obj("raw", {"x": 1500.0, "y": 400.0, "anchor": None}),
            obj("pinned", {"at": "slot.top", "anchor": None, "offset": None, "follow": None}),
        ]
    }

    def oob(oid: str, fix: dict[str, float]) -> dict[str, Any]:
        return {"code": "OUT_OF_BOUNDS", "path": f"/objects/{oid}", "message": "outside", "fix": fix}

    out = autofix(dsl, [oob("slot", {"dx": 0, "dy": -40, "scale": 0.8}), oob("raw", {"dx": -120, "dy": 0})])
    assert out is not None
    slot, raw, _ = out["objects"]
    assert slot["place"]["offset"] == {"x": 0.0, "y": -40.0} and slot["scale"] == 0.8
    assert raw["place"]["x"] == 1380.0 and raw["place"]["y"] == 400.0
    alone = autofix(dsl, [oob("pinned", {"dx": 10, "dy": 0})])  # its pin offset moves; it still follows
    assert alone is not None and alone["objects"][2]["place"]["offset"] == {"x": 10.0, "y": 0.0}
    both = autofix(dsl, [oob("slot", {"dx": 0, "dy": -40}), oob("pinned", {"dx": 0, "dy": -40})])
    assert both is not None and both["objects"][2]["place"]["offset"] is None  # moves with 'slot'


def test_autofix_completes_a_bare_id_where_an_anchor_belongs() -> None:
    dsl: dict[str, Any] = {
        "objects": [
            {"id": "earth", "type": "ball", "params": {}, "place": {"x": 800, "y": 450}},
            {"id": "tag", "type": "label", "params": {}, "place": {"at": "earth", "offset": None}},
            {"id": "ray", "type": "line", "params": {"from": "sun", "to": {"x": 1, "y": 2}}, "place": None},
        ]
    }

    def bad(path: str) -> dict[str, Any]:
        return {"code": "SCHEMA", "path": path, "message": "anchor references look like 'object_id.anchor'"}

    out = autofix(dsl, [bad("/objects/1/place/at"), bad("/objects/2/params/from")])
    assert out is not None and out["objects"][1]["place"]["at"] == "earth.center"
    assert out["objects"][2]["params"]["from"] == "sun"  # no object 'sun': the model must decide


def test_autofix_moves_an_object_off_the_title_as_the_engine_says() -> None:
    dsl = {"objects": [{"id": "switch", "type": "icon", "params": {}, "place": {"x": 800.0, "y": 110.0}}]}
    covered = {
        "code": "TITLE_COVERED",
        "path": "/objects/switch",
        "message": "covers",
        "fix": {"dx": 0, "dy": 64},
    }
    out = autofix(dsl, [covered])
    assert out is not None and out["objects"][0]["place"] == {"x": 800.0, "y": 174.0}


def test_autofix_moves_the_points_of_a_line_drawn_off_frame() -> None:
    line = {
        "id": "axis",
        "type": "line",
        "place": None,
        "params": {"from": {"x": 1500, "y": 100}, "to": "sun.center", "dashed": False},
    }
    fix = {"dx": -200.0, "dy": 40.0}
    out = autofix({"objects": [line]}, [{"code": "OUT_OF_BOUNDS", "path": "/objects/axis", "fix": fix}])
    assert out is not None
    assert out["objects"][0]["params"]["from"] == {"x": 1300.0, "y": 140.0}
    assert out["objects"][0]["params"]["to"] == "sun.center"  # follows its object
    anchored = {**line, "params": {"from": "a.center", "to": "b.center"}}
    assert (
        autofix({"objects": [anchored]}, [{"code": "OUT_OF_BOUNDS", "path": "/objects/axis", "fix": fix}])
        is None
    )


def test_a_patch_replaces_adds_and_removes_by_id() -> None:
    scene: dict[str, Any] = {
        "duration": 10,
        "narration": {"beats": [{"id": "b1", "text": "Hi", "at": 0}]},
        "objects": [{"id": "a", "v": 1}, {"id": "b", "v": 1}, {"id": "c", "v": 1}],
        "timeline": [{"id": "x", "target": "a"}, {"id": "y", "target": "c"}, {"id": "z", "target": "b"}],
        "solvers": [],
        "values": None,
    }
    patch = {
        "objects": [{"id": "a", "v": 2}, {"id": "d", "v": 1}],
        "remove_objects": ["c"],
        "timeline": [{"id": "w", "target": "d"}],
        "remove_actions": ["z"],
        "solvers": None,
        "values": None,
        "duration": 11,
        "narration": None,
        "camera": None,
    }
    out = apply_patch(scene, patch)
    assert out["objects"] == [{"id": "a", "v": 2}, {"id": "b", "v": 1}, {"id": "d", "v": 1}]
    assert [a["id"] for a in out["timeline"]] == ["x", "w"]  # y went with its object, z was removed
    assert out["duration"] == 11 and out["narration"] == scene["narration"]
    assert scene["objects"][0]["v"] == 1  # the original is untouched


def test_the_patch_schema_is_strict_and_reuses_the_scene_definitions() -> None:
    arr = {"anyOf": [{"type": "array", "items": {"$ref": "#/$defs/Object"}}, {"type": "null"}]}
    scene = {
        "properties": {k: arr for k in ("objects", "timeline", "solvers", "values", "narration", "camera")},
        "$defs": {"Object": {"type": "object"}},
    }
    s = patch_schema(scene)
    assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
    assert s["$defs"] is scene["$defs"] and "remove_objects" in s["properties"]


async def test_a_correction_is_a_patch_merged_into_the_previous_scene(pool: EngineWorkerPool) -> None:
    def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        if i == 0 and attempt == 1:
            return broken(SCENES[0])
        if i == 0:
            good = copy.deepcopy(SCENES[0])
            return {
                "objects": None,
                "remove_objects": None,
                "timeline": [good["timeline"][0]],  # the action with the wrong target, fixed
                "remove_actions": None,
                "solvers": None,
                "values": None,
                "duration": None,
                "narration": None,
                "camera": None,
            }
        return copy.deepcopy(SCENES[i])

    script = Script(scene)
    gen, _ = await run(pool, script)
    assert gen.status == "complete" and gen.scenes[0].status == "ready"
    fix = next(r for r in script.calls if r.label == "scene 1 attempt 2")
    assert fix.schema_name == "ScenePatch" and "only what changes" in fix.messages[0].parts[-1].text  # type: ignore[union-attr]
    assert len(stages_for(script, 0)) == 2


async def test_patch_corrections_can_be_turned_off(pool: EngineWorkerPool) -> None:
    def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        return broken(SCENES[i]) if i == 0 and attempt == 1 else copy.deepcopy(SCENES[i])

    script = Script(scene)
    gen, _ = await run(pool, script, settings(animator_patch_corrections=False))
    fix = next(r for r in script.calls if r.label == "scene 1 attempt 2")
    assert gen.status == "complete" and fix.schema_name == "Scene"


# ---------------------------------------------------------------------- hedged drafts


async def test_the_first_valid_draft_wins_and_the_other_is_still_counted(pool: EngineWorkerPool) -> None:
    release = asyncio.Event()
    calls: dict[int, int] = {}

    async def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        k = calls[i] = calls.get(i, 0) + 1
        if k == 1:  # the first draft of each scene is slow (and valid)
            await release.wait()
            return copy.deepcopy(SCENES[i])
        return copy.deepcopy(SCENES[i]) if i != 1 else broken(SCENES[i])

    script = Script(scene)
    s = settings(animator_scene_drafts=2)
    runner = GenerationRunner(MemoryStore(), Pipeline(EngineClient(pool), FakeLLM(script, cost=0.01), s), s)
    gen = await runner.start(GenerationRequest(topic="Archimedes' principle"))
    for _ in range(400):
        g = await runner.get(gen.id)
        if g and g.scenes and g.scenes[0].status == "ready":
            break
        await asyncio.sleep(0.02)
    g = await runner.get(gen.id)
    assert g is not None and g.scenes[0].status == "ready"  # the fast draft won; the slow one still runs
    release.set()
    await asyncio.wait_for(runner._tasks[gen.id], timeout=60)
    done = await runner.get(gen.id)
    assert done is not None and done.status == "complete" and all(x.status == "ready" for x in done.scenes)
    # Scene 2's fast draft was broken: its correction started at once (and won, being instant here).
    assert [a.kind for a in done.scenes[1].attempts] == ["plan", "correction"]
    drafts = [r for r in script.calls if r.label.endswith("attempt 1")]
    assert len(drafts) == 8 and done.metrics.llm_calls == 1 + 2 + len(drafts) + 1  # every call counted


async def test_without_a_valid_draft_the_best_one_is_corrected(pool: EngineWorkerPool) -> None:
    def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        return broken(SCENES[i]) if i == 0 and attempt == 1 else copy.deepcopy(SCENES[i])

    script = Script(scene)
    gen, _ = await run(pool, script, settings(animator_scene_drafts=2, animator_patch_corrections=False))
    assert gen.status == "complete" and [a.kind for a in gen.scenes[0].attempts] == ["plan", "correction"]
    assert len([r for r in script.calls if r.label == "scene 1 attempt 1"]) == 2


async def test_a_fatal_error_in_a_draft_stops_the_generation(pool: EngineWorkerPool) -> None:
    def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        return LLMAuthError("OpenAI rejected the API key") if i == 0 else copy.deepcopy(SCENES[i])

    gen, _ = await run(pool, Script(scene), settings(animator_scene_drafts=2))
    assert gen.status == "failed" and "API key" in (gen.error or "")


def test_a_deleted_action_hands_its_start_to_the_actions_timed_after_it() -> None:
    scene: dict[str, Any] = {
        "timeline": [
            {"id": "x", "target": "a", "start": {"at": 1.0, "after": None, "with": None, "delay": None}},
            {"id": "y", "target": "b", "start": {"at": None, "after": "x", "with": None, "delay": 0.5}},
            {"id": "z", "target": "c", "start": {"at": None, "after": None, "with": "y", "delay": None}},
        ]
    }
    one = apply_patch(scene, {"remove_actions": ["x"]})
    assert [a["id"] for a in one["timeline"]] == ["y", "z"]
    assert one["timeline"][0]["start"] == {"at": 1.0, "after": None, "with": None, "delay": 0.5}
    assert one["timeline"][1]["start"]["with"] == "y"  # y is still there
    both = apply_patch(scene, {"remove_objects": ["b"], "remove_actions": ["x"]})  # y goes with b
    assert both["timeline"][0]["start"] == {"at": 1.0, "after": None, "with": None, "delay": 0.5}


class Race(Script):
    """Scene 1: the first draft is broken and fast, the second is valid but held back by `slow`;
    corrections are held back by `fix`. Other scenes: valid at once."""

    def __init__(self) -> None:
        super().__init__(self.scene)
        self.slow = asyncio.Event()
        self.fix = asyncio.Event()
        self.fix_started = asyncio.Event()
        self.drafts = 0

    async def scene(self, i: int, attempt: int, req: LLMRequest) -> Any:
        if i != 0:
            return copy.deepcopy(SCENES[i])
        if attempt == 1:
            self.drafts += 1
            if self.drafts == 1:
                return broken(SCENES[0])
            await self.slow.wait()
            return copy.deepcopy(SCENES[0])
        self.fix_started.set()
        await self.fix.wait()
        return {"timeline": [copy.deepcopy(SCENES[0])["timeline"][0]]}


async def race(pool: EngineWorkerPool, script: Race) -> tuple[GenerationRunner, str]:
    s = settings(animator_scene_drafts=2)
    runner = GenerationRunner(MemoryStore(), Pipeline(EngineClient(pool), FakeLLM(script), s), s)
    gen = await runner.start(GenerationRequest(topic="Archimedes' principle"))
    return runner, gen.id


async def scene_one(runner: GenerationRunner, gen_id: str) -> Any:
    for _ in range(500):
        g = await runner.get(gen_id)
        if g and g.scenes and g.scenes[0].status == "ready":
            return g.scenes[0]
        await asyncio.sleep(0.02)
    raise AssertionError("scene 1 never became ready")


async def test_a_correction_starts_while_the_other_draft_is_still_running(pool: EngineWorkerPool) -> None:
    script = Race()
    runner, gen_id = await race(pool, script)
    await asyncio.wait_for(script.fix_started.wait(), timeout=10)  # the slow draft has not finished
    script.fix.set()
    first = await scene_one(runner, gen_id)
    assert [a.kind for a in first.attempts] == ["plan", "correction"]
    script.slow.set()
    await asyncio.wait_for(runner._tasks[gen_id], timeout=60)


async def test_the_other_draft_can_beat_the_correction(pool: EngineWorkerPool) -> None:
    script = Race()
    runner, gen_id = await race(pool, script)
    await asyncio.wait_for(script.fix_started.wait(), timeout=10)
    script.slow.set()  # the valid draft finishes while the correction is still being written
    first = await scene_one(runner, gen_id)
    assert first.dsl is not None and first.dsl["timeline"] == SCENES[0]["timeline"]
    script.fix.set()
    await asyncio.wait_for(runner._tasks[gen_id], timeout=60)
    done = await runner.get(gen_id)
    assert done is not None and done.status == "complete"
    assert done.metrics.llm_calls == len(script.calls)  # the correction that lost was counted too
