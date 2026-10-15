"""The planned layout flow (ADR-047): the scene writer's stage plan, solved precisely in the fixed
frame by the engine-measured planner. The geometry, the planner on real scenes (things stay what
they were and move where they went), and the pipeline switch: planned lessons get their own prompts,
one draft, the planner and a critic that judges meaning; classic lessons are untouched."""

from __future__ import annotations

import asyncio
import copy
import json
from itertools import combinations, pairwise
from typing import Any

import pytest

from animator.engine_client import EngineClient, EngineWorkerPool
from animator.llm import FakeLLM, LLMRequest, Usage
from animator.pipeline.generator import Pipeline, glitches_of
from animator.pipeline.layout_plan import LayoutPlanner, parse_plan, planned_scene_schema
from animator.pipeline.layout_plan.arrange import EDGE_MARGIN, FILL_H, FILL_W, GAP, Group, Item, solve_region
from animator.pipeline.layout_plan.regions import Box, regions
from tests.test_pipeline import text_of
from tests.test_teaching import Teaching, teaching

STAGE = regions("stage_with_caption")["stage"]


def item(
    iid: str, w: float, h: float, x: float = 0, y: float = 0, label: float = 0, scalable: bool = True
) -> Item:
    core = Box(x, y, w, h)
    foot = Box(x, y, w, h + label)  # a label of height `label` below it
    return Item(iid, core, foot, scalable)


def feet(group: Group, placed: dict[str, Any]) -> dict[str, Box]:
    """Where each item's footprint ended up."""
    out = {}
    for it in group.items:
        p = placed[it.id]
        ml, mr, mt, mb = it.margins
        w, h = p.k * it.core.w, p.k * it.core.h
        out[it.id] = Box(p.cx - w / 2 - ml, p.cy - h / 2 - mt, w + ml + mr, h + mt + mb)
    return out


def inside(b: Box, room: Box) -> bool:
    return (
        b.x >= room.x - 0.5
        and b.y >= room.y - 0.5
        and b.right <= room.right + 0.5
        and b.bottom <= room.bottom + 0.5
    )


# ---------------------------------------------------------------------- the geometry


def test_a_row_fills_the_stage_in_order_with_no_overlaps() -> None:
    g = Group(
        "flow", "row", [item("a", 80, 60, label=40), item("b", 120, 90, label=40), item("c", 60, 60)], "stage"
    )
    solved = solve_region(STAGE, [g])
    boxes = feet(g, solved.placed)
    assert solved.fits
    for a, b in combinations(boxes.values(), 2):
        assert a.overlap(b) == 0
    assert all(inside(b, STAGE) for b in boxes.values())
    xs = [solved.placed[i].cx for i in "abc"]
    assert xs == sorted(xs)  # in plan order
    assert solved.placed["a"].cy == pytest.approx(solved.placed["b"].cy)  # one centre line
    left, right = min(b.x for b in boxes.values()), max(b.right for b in boxes.values())
    assert (left + right) / 2 == pytest.approx(STAGE.cx, abs=1)  # centred, not left in a corner
    ordered = sorted(boxes.values(), key=lambda b: b.x)
    assert all(
        GAP - 0.5 <= b.x - a.right <= 3 * GAP + 0.5 for a, b in pairwise(ordered)
    )  # spread, still together
    assert solved.scale["flow"] == pytest.approx(1.6)  # small drawings are enlarged, up to the limit


def test_items_too_wide_for_a_row_are_shrunk_to_fit() -> None:
    g = Group("flow", "row", [item(k, 500, 200) for k in "abcd"], "stage")
    solved = solve_region(STAGE, [g])
    boxes = feet(g, solved.placed)
    assert solved.fits and solved.scale["flow"] < 1
    assert all(inside(b, STAGE) for b in boxes.values())
    gaps = sorted(boxes.values(), key=lambda b: b.x)
    assert all(b.x - a.right >= GAP - 0.5 for a, b in pairwise(gaps))


def test_a_grid_centres_its_short_last_row() -> None:
    g = Group("cells", "grid", [item(f"c{k}", 100, 100) for k in range(5)], "stage")
    solved = solve_region(STAGE, [g])
    rows = sorted({round(solved.placed[f"c{k}"].cy) for k in range(5)})
    assert len(rows) == 2
    last = [solved.placed[f"c{k}"].cx for k in (3, 4)]
    assert (last[0] + last[1]) / 2 == pytest.approx(STAGE.cx, abs=1)
    for a, b in combinations(feet(g, solved.placed).values(), 2):
        assert a.overlap(b) == 0


def test_a_cycle_goes_round_from_the_top_without_overlaps() -> None:
    g = Group("loop", "cycle", [item(f"s{k}", 160, 120, label=30) for k in range(4)], "stage")
    solved = solve_region(STAGE, [g])
    top = min(solved.placed.values(), key=lambda p: p.cy)
    assert top is solved.placed["s0"]
    boxes = feet(g, solved.placed)
    for a, b in combinations(boxes.values(), 2):
        assert a.overlap(b) == 0
    assert all(inside(b, STAGE) for b in boxes.values())


def test_a_free_group_keeps_its_drawing_as_drawn() -> None:
    ball = item("ball", 40, 40, x=300, y=200)
    ramp = item("ramp", 300, 150, x=200, y=260)
    g = Group("setup", "free", [ball, ramp], "stage")
    solved = solve_region(STAGE, [g])
    pb, pr = solved.placed["ball"], solved.placed["ramp"]
    k = solved.scale["setup"]
    assert pb.cx - pr.cx == pytest.approx(k * (ball.core.cx - ramp.core.cx))
    assert pb.cy - pr.cy == pytest.approx(k * (ball.core.cy - ramp.core.cy))


def test_groups_on_stage_together_share_it_and_later_ones_reuse_it() -> None:
    main = Group("main", "row", [item("a", 200, 150), item("b", 200, 150)], "stage", t0=0, t1=10)
    key = Group("key", "free", [item("key", 400, 40, scalable=False)], "stage", t0=2, t1=10)
    later = Group("later", "free", [item("graph", 300, 200)], "stage", t0=10.2, t1=20)
    solved = solve_region(STAGE, [main, key, later])
    # main and key are on stage together: separate lanes, main above the key phrase.
    assert solved.lanes["main"].bottom <= solved.lanes["key"].y
    assert solved.placed["key"].k == 1  # text keeps its size
    # The graph comes after both are gone: it reuses the first lane, at the same scale limit.
    assert solved.lanes["later"] == solved.lanes["main"]


def test_text_is_never_shrunk_below_its_readable_size() -> None:
    big = Item("card", Box(0, 0, 3000, 400), Box(0, 0, 3000, 400), True, 1.35, 0.9)
    solved = solve_region(STAGE, [Group("cards", "free", [big], "stage")])
    assert solved.scale["cards"] == pytest.approx(0.9)
    assert not solved.fits  # reported, for the engine to judge


def test_arranged_groups_leave_room_around_them_and_drawings_keep_clear_of_the_edge() -> None:
    row = Group("row", "row", [item("a", 1000, 1000), item("b", 1000, 1000)], "stage")
    box = feet(row, solve_region(STAGE, [row]).placed)["a"]
    assert box.h == pytest.approx(STAGE.h * FILL_H, rel=1e-3)
    one = Group("one", "free", [item("big", 1000, 1000)], "stage")
    box = feet(one, solve_region(STAGE, [one]).placed)["big"]
    assert box.h == pytest.approx(STAGE.h - 2 * EDGE_MARGIN, rel=1e-3)  # kept as drawn: up to the edge margin
    assert box.w < STAGE.w * FILL_W


def test_a_long_row_that_does_not_fit_wraps_into_two_rows() -> None:
    g = Group("route", "row", [item(f"s{k}", 300, 200) for k in range(7)], "stage", connected=True)
    solved = solve_region(STAGE, [g])
    assert g.kind == "grid" and g.columns == 4
    rows = sorted({round(solved.placed[f"s{k}"].cy) for k in range(7)})
    assert len(rows) == 2 and solved.fits
    xs = [solved.placed[f"s{k}"].cx for k in range(4)]
    assert xs == sorted(xs)  # in order, left to right
    back = [solved.placed[f"s{k}"].cx for k in range(4, 7)]
    assert back == sorted(back, reverse=True)  # then back, right to left: each step next to the last
    assert solved.placed["s4"].cx == pytest.approx(solved.placed["s3"].cx)  # the turn is straight down


# ---------------------------------------------------------------------- the plan


def test_a_plan_is_read_leniently() -> None:
    assert parse_plan(None) is None and parse_plan({"groups": "x"}) is None
    plan = parse_plan(
        {"groups": [{"id": "a", "arrange": "spiral", "items": ["farm", ""]}, {"id": "b", "items": []}]}
    )
    assert plan is not None and len(plan.groups) == 1
    assert plan.groups[0].arrange == "free" and plan.groups[0].items == ["farm"]


def test_the_plan_comes_before_the_objects_in_the_reply() -> None:
    base = {
        "type": "object",
        "properties": {"id": {}, "narration": {}, "objects": {}, "timeline": {}},
        "required": [],
    }
    schema = planned_scene_schema(base)
    keys = list(schema["properties"])
    assert keys.index("stage") == keys.index("objects") - 1
    assert "stage" in schema["required"]
    assert "stage" not in base["properties"]  # the classic schema is untouched


def test_a_drafts_plain_slips_are_repaired() -> None:
    from animator.pipeline.layout_plan import repair_draft

    draft: dict[str, Any] = {
        "objects": [
            {"id": "press", "type": "group", "place": {"x": 800, "y": 400}},
            {
                "id": "press_top",
                "type": "rectangle",
                "parent": "press",
                "place": {"at": "press.center", "follow": True},
            },
            {"id": "vat", "type": "polygon"},
            {"id": "vat_lid", "type": "rectangle", "parent": "vat", "place": {"x": 1, "y": 2}},
        ],
        "timeline": [
            {"id": "a1", "action": "create", "target": "press", "start": {"at": "beat:b1"}},
            {"id": "a2", "action": "create", "target": "vat", "start": {"at": "after:a1", "after": None}},
            {"id": "a3", "action": "create", "target": "vat_lid", "start": {"at": "a2", "after": None}},
            {"id": "a4", "action": "highlight", "target": "vat", "start": {"at": "with:a3", "with": None}},
        ],
    }
    fixed, done = repair_draft(draft)
    objects = {o["id"]: o for o in fixed["objects"]}
    starts = {a["id"]: a["start"] for a in fixed["timeline"]}
    assert objects["press_top"]["place"]["follow"] is False  # placed once inside its group
    assert "parent" not in objects["vat_lid"]  # a polygon is not a group
    assert starts["a1"] == {"at": "beat:b1"}  # a real time reference is left alone
    assert starts["a2"]["after"] == "a1" and "at" not in starts["a2"]
    assert starts["a3"]["after"] == "a2" and starts["a4"]["with"] == "a3"
    assert len(done) == 5
    assert draft["timeline"][1]["start"]["at"] == "after:a1"  # the draft itself is not changed
    assert repair_draft({"objects": [], "timeline": []}) == ({"objects": [], "timeline": []}, [])


def test_parts_given_as_offsets_from_their_group_are_placed_around_it() -> None:
    from animator.pipeline.layout_plan import repair_draft

    draft: dict[str, Any] = {
        "layout": "stage_with_caption",
        "objects": [
            {"id": "grapes", "type": "group", "place": {"x": 370, "y": 430}},
            {"id": "grapes_1", "type": "circle", "parent": "grapes", "place": {"x": -42, "y": -23}},
            {"id": "grapes_2", "type": "circle", "parent": "grapes", "place": {"x": 39, "y": 43}},
            {"id": "grapes_stem", "type": "line", "parent": "grapes", "params": {"from": {"x": 0, "y": -60}}},
            {"id": "tank", "type": "group", "place": {"x": 1000, "y": 400}},
            {"id": "tank_body", "type": "rectangle", "parent": "tank", "place": {"x": 1000, "y": 420}},
        ],
        "timeline": [
            {"id": "m", "action": "move", "target": "grapes_1", "to": {"x": 0, "y": 30}},
        ],
    }
    fixed, done = repair_draft(draft)
    objects = {o["id"]: o for o in fixed["objects"]}
    assert objects["grapes_1"]["place"] == {"x": 328, "y": 407}
    assert objects["grapes_2"]["place"] == {"x": 409, "y": 473}
    assert objects["grapes_stem"]["params"]["from"] == {"x": 0, "y": -60}  # endpoints are local already
    assert objects["tank_body"]["place"] == {"x": 1000, "y": 420}  # given on the canvas: left alone
    assert fixed["timeline"][0]["to"] == {"x": 370, "y": 460}  # its move, too
    assert done == ["grapes: its parts' coordinates are offsets from its centre"]


def test_a_slot_of_another_layout_becomes_its_point() -> None:
    from animator.pipeline.layout_plan import repair_draft

    draft: dict[str, Any] = {
        "layout": "split",
        "objects": [
            {"id": "a", "type": "circle", "place": {"slot": "stage.center", "anchor": "bottom"}},
            {"id": "b", "type": "circle", "place": {"slot": "left.center"}},
        ],
        "timeline": [],
    }
    fixed, done = repair_draft(draft)
    assert fixed["objects"][0]["place"] == {"x": 800.0, "y": 426.0, "anchor": "bottom"}
    assert fixed["objects"][1]["place"] == {"slot": "left.center"}
    assert len(done) == 1


# ---------------------------------------------------------------------- the planner on scenes


def crowded(stage: bool = True) -> dict[str, Any]:
    """A valid scene drawn small and cramped in the top-left: a farm, a mill and a shop in a row with
    their labels, an arrow from the farm to the mill, and a seed that travels from the farm into the
    mill. With `stage`, it carries the stage plan the planned flow asks for."""
    boxes = [("farm", 150), ("mill", 260), ("shop", 370)]
    objects: list[dict[str, Any]] = [
        {
            "id": "title_s01",
            "type": "text",
            "params": {"text": "From farm to shop", "size": "heading", "weight": "bold"},
            "place": {"slot": "title_bar"},
            "layer": "overlay",
        }
    ]
    timeline: list[dict[str, Any]] = [
        {"id": "t0", "action": "write", "target": "title_s01", "start": {"at": 0.2}}
    ]
    for name, x in boxes:
        objects.append(
            {
                "id": name,
                "type": "rectangle",
                "params": {"width": 80, "height": 60},
                "place": {"x": x, "y": 220},
            }
        )
        objects.append(
            {
                "id": f"{name}_label",
                "type": "label",
                "params": {"text": name.capitalize()},
                "place": {"relativeTo": name, "side": "below", "gap": 12},
            }
        )
        timeline.append({"id": f"c_{name}", "action": "create", "target": name, "start": {"at": "beat:b1"}})
        timeline.append(
            {
                "id": f"l_{name}",
                "action": "create",
                "target": f"{name}_label",
                "start": {"after": f"c_{name}"},
            }
        )
    objects.append(
        {
            "id": "link",
            "type": "arrow",
            "params": {"from": "farm.right", "to": "mill.left", "startGap": 4, "endGap": 4},
        }
    )
    objects.append(
        {"id": "seed", "type": "circle", "params": {"radius": 8}, "place": {"x": 150, "y": 220}, "z": 3}
    )
    timeline += [
        {"id": "d_link", "action": "draw", "target": "link", "start": {"at": "beat:b2"}},
        {"id": "c_seed", "action": "create", "target": "seed", "start": {"after": "d_link"}},
        {
            "id": "m_seed",
            "action": "move",
            "target": "seed",
            "to": {"x": 260, "y": 220},
            "start": {"after": "c_seed"},
            "duration": 1,
        },
    ]
    scene: dict[str, Any] = {
        "dsl_version": "1.0",
        "id": "x",
        "title": "From farm to shop",
        "duration": 8,
        "layout": "stage_with_caption",
        "narration": {
            "beats": [
                {"id": "b1", "text": "A **farm**, a mill and a shop."},
                {"id": "b2", "text": "The seed goes to the mill."},
            ]
        },
        "objects": objects,
        "timeline": timeline,
    }
    if stage:
        scene["stage"] = {
            "direction": None,
            "groups": [
                {
                    "id": "route",
                    "region": None,
                    "arrange": "row",
                    "items": ["farm", "mill", "shop"],
                    "align": None,
                    "columns": None,
                    "connected": True,
                }
            ],
        }
    return scene


async def test_the_planner_lays_a_cramped_row_out_across_the_stage(pool: EngineWorkerPool) -> None:
    engine = EngineClient(pool)
    scene = crowded()
    plan = parse_plan(scene.pop("stage"))
    assert plan is not None
    res = await engine.compile(scene, None)
    assert res.ok
    planned = await LayoutPlanner(engine).plan(scene, plan, res, None)
    best = planned.candidates[0]
    again = await engine.compile(best.dsl, None)
    assert again.ok and len(glitches_of(again)) <= len(glitches_of(res))
    sample = await engine.sample(best.dsl, [3.0, 7.9], None)
    boxes = {o["id"]: Box.of(o["bbox"]) for o in sample.frames[0]["objects"]}
    row = [boxes[n] for n in ("farm", "mill", "shop")]
    assert row[0].cx < row[1].cx < row[2].cx
    assert row[2].right - row[0].x > 0.6 * STAGE.w  # across the stage
    assert row[0].w == pytest.approx(80 * 1.6, rel=0.02)  # enlarged
    for n in ("farm", "mill", "shop"):  # labels went with their boxes, below them
        assert boxes[f"{n}_label"].y > boxes[n].bottom and abs(boxes[f"{n}_label"].cx - boxes[n].cx) < 1
    for a, b in combinations([boxes[n] for n in ("farm", "mill", "shop")], 2):
        assert a.overlap(b) == 0
    # The seed still ends its journey in the mill, wherever the mill now is.
    end = {o["id"]: Box.of(o["bbox"]) for o in sample.frames[1]["objects"]}
    assert end["mill"].contains(end["seed"].cx, end["seed"].cy)
    assert planned.report["items"]["farm"]["attached"] == ["farm_label"]


async def test_a_scene_with_physics_is_left_as_drawn(pool: EngineWorkerPool) -> None:
    engine = EngineClient(pool)
    scene = crowded(stage=False)
    scene["timeline"].append({"id": "go", "action": "run_solver", "target": "fall", "start": {"at": 1}})
    plan = parse_plan(crowded()["stage"])
    assert plan is not None
    planned = await LayoutPlanner(engine).plan(
        scene, plan, await engine.compile(crowded(stage=False), None), None
    )
    assert not planned.candidates and "run_solver" in planned.report["skipped"]


# ---------------------------------------------------------------------- the pipeline


async def run_lesson(pool: EngineWorkerPool, script: Teaching, layout: str | None = None, **kw: Any) -> Any:
    from animator.jobs.runner import GenerationRunner
    from animator.jobs.store import MemoryStore
    from animator.pipeline.models import GenerationRequest

    s = teaching(**kw)
    fake = FakeLLM(script, usage=Usage(1000, 0, 500), cost=0.01)
    runner = GenerationRunner(MemoryStore(), Pipeline(EngineClient(pool), fake, s), s)
    gen = await runner.start(GenerationRequest(topic="Farms", duration_min=0.5, layout=layout))
    await asyncio.wait_for(runner._tasks[gen.id], timeout=120)
    return await runner.get(gen.id)


def planned_script() -> Teaching:
    script = Teaching()
    script.scene = lambda i, a, r: crowded()
    return script


async def test_a_planned_lesson_is_drawn_once_and_laid_out_by_the_planner(pool: EngineWorkerPool) -> None:
    script = planned_script()
    gen = await run_lesson(
        pool, script, animator_layout_mode="planned", animator_scene_drafts=2, animator_correction_drafts=2
    )
    assert gen.status == "complete", gen.error
    first = gen.scenes[0]
    assert first.dsl is not None and "stage" not in first.dsl  # the plan is never played
    assert first.layout_plan is not None and first.layout_plan["applied"] == "plan"
    drafts = [r for r in script.calls if r.label == "scene 1 attempt 1"]
    assert len(drafts) == 1  # one draft, not a race of two
    assert drafts[0].prompt_version == "animation-planned.v5"
    assert "stage" in drafts[0].schema["properties"]
    assert "The stage plan" in drafts[0].instructions and "Sizes (canvas px" in drafts[0].instructions


async def test_a_planned_lessons_critic_knows_the_layout_is_planned(pool: EngineWorkerPool) -> None:
    from tests.test_teaching import Frames, run_teaching

    script = planned_script()
    gen, _ = await run_teaching(pool, script, Frames(), animator_layout_mode="planned")
    assert gen.status == "complete", gen.error
    critic = next(r for r in script.calls if r.label == "critic scene 1")
    assert critic.prompt_version == "critic-planned.v2"
    assert "Composition, measured by the engine" in text_of(critic)  # composition is still judged
    assert "Do not ask to fill room that is kept for what comes next" in critic.instructions


async def test_classic_lessons_are_untouched(pool: EngineWorkerPool) -> None:
    script = planned_script()
    script.scene = lambda i, a, r: crowded(stage=False)
    gen = await run_lesson(pool, script)
    assert gen.status == "complete", gen.error
    assert gen.scenes[0].layout_plan is None
    draft = next(r for r in script.calls if r.label == "scene 1 attempt 1")
    assert draft.prompt_version == "animation.v8" and "stage" not in draft.schema["properties"]


async def test_a_lesson_can_ask_for_the_planned_flow(pool: EngineWorkerPool) -> None:
    script = planned_script()
    gen = await run_lesson(pool, script, layout="planned")  # the server default stays classic
    assert gen.status == "complete", gen.error
    assert gen.request.layout == "planned" and gen.scenes[0].layout_plan["applied"] == "plan"
    script2 = planned_script()
    script2.scene = lambda i, a, r: crowded(stage=False)
    gen2 = await run_lesson(pool, script2, layout="classic", animator_layout_mode="planned")
    assert gen2.scenes[0].layout_plan is None


async def test_a_correction_keeps_the_plan(pool: EngineWorkerPool) -> None:
    from tests.test_teaching import Frames, run_teaching

    script = Teaching(
        **{
            "critic scene 1": {
                "verdict": "revise",
                "issues": [{"severity": "major", "problem": "The shop is blue.", "fix": "Make it green."}],
            }
        }
    )

    def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        if i == 0 and attempt > 1:
            assert '"stage":' in text_of(req)  # the model sees its plan with its scene
            shop = copy.deepcopy(next(o for o in crowded()["objects"] if o["id"] == "shop"))
            shop["style"] = {"fill": "green"}
            return {
                "objects": [shop],
                "remove_objects": None,
                "timeline": [],
                "remove_actions": None,
                "solvers": [],
                "values": [],
                "duration": None,
                "narration": None,
                "camera": None,
                "stage": None,
            }
        return crowded()

    script.scene = scene
    gen, _ = await run_teaching(pool, script, Frames(), animator_layout_mode="planned")
    assert gen.status == "complete", gen.error
    first = gen.scenes[0]
    assert len(first.attempts) == 2 and first.layout_plan["applied"] == "plan"
    shop = next(o for o in first.dsl["objects"] if o["id"] == "shop")
    assert shop["style"]["fill"] == "green" and shop["place"]["x"] != 370  # fixed, and still laid out
    json.dumps(first.layout_plan)  # the report is plain data (stored with the lesson)
