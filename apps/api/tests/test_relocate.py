"""Label relocation (ADR-046): a label that still covers, hides under or is crossed by something after the
polish is tried in other places around what it names, and the engine judges every try. Labels meant to
be inside something stay there, and the fast pipeline keeps its polish only."""

from __future__ import annotations

import copy
from typing import Any

from animator.engine_client import EngineClient, EngineWorkerPool
from animator.pipeline.generator import Pipeline, _drawing_of, _inside_a_frame, troubled_labels
from tests.test_pipeline import Script, run, settings
from tests.test_teaching import Teaching, run_teaching


def scene(objects: list[dict[str, Any]], timeline: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "dsl_version": "1.0",
        "id": "x",
        "title": "Two boxes",
        "duration": 6,
        "layout": "stage_with_caption",
        "narration": {"beats": [{"id": "b1", "text": "Look at the **boxes**."}]},
        "objects": [
            {
                "id": "title_s01",
                "type": "text",
                "params": {"text": "Two boxes", "size": "heading", "weight": "bold"},
                "place": {"slot": "title_bar"},
                "layer": "overlay",
            },
            *objects,
        ],
        "timeline": [{"id": "t0", "action": "write", "target": "title_s01", "start": {"at": 0.2}}, *timeline],
    }


def squeezed() -> dict[str, Any]:
    """A label to the right of one box, where a second box is drawn after it: covered, hidden."""
    return scene(
        [
            {
                "id": "left_box",
                "type": "rectangle",
                "params": {"width": 220, "height": 160},
                "place": {"x": 560, "y": 430},
            },
            {
                "id": "left_name",
                "type": "label",
                "params": {"text": "Left box"},
                "place": {"relativeTo": "left_box", "side": "right", "gap": 12},
            },
            {
                "id": "right_box",
                "type": "rectangle",
                "params": {"width": 220, "height": 160},
                "place": {"x": 830, "y": 430},
            },
        ],
        [
            {"id": "a1", "action": "create", "target": "left_box", "start": {"at": 0.5}},
            {"id": "a2", "action": "create", "target": "left_name", "start": {"after": "a1"}},
            {"id": "a3", "action": "create", "target": "right_box", "start": {"after": "a2"}},
        ],
    )


async def test_the_engine_reports_the_squeezed_label(pool: EngineWorkerPool) -> None:
    res = await EngineClient(pool).compile(squeezed(), None)
    assert res.ok and "left_name" in troubled_labels(res)


async def test_a_squeezed_label_is_moved_where_it_is_clear(pool: EngineWorkerPool) -> None:
    script = Teaching()
    script.scene = lambda i, a, r: squeezed()
    gen, _ = await run_teaching(pool, script, None, animator_auto_frame=False)
    assert gen.status == "complete", gen.error
    first = gen.scenes[0]
    assert first.dsl is not None
    name = next(o for o in first.dsl["objects"] if o["id"] == "left_name")
    assert name["place"] != {"relativeTo": "left_box", "side": "right", "gap": 12}
    res = await EngineClient(pool).compile(first.dsl, None)
    assert res.ok and "left_name" not in troubled_labels(res)


async def test_the_fast_pipeline_and_the_switch_leave_labels_alone(pool: EngineWorkerPool) -> None:
    script = Script(lambda i, a, r: squeezed())
    gen, _ = await run(pool, script, settings(animator_scene_mode="parallel"), duration_min=0.5)
    assert gen.status == "complete" and gen.teaching is None
    name = next(o for o in (gen.scenes[0].dsl or {})["objects"] if o["id"] == "left_name")
    assert name["place"]["relativeTo"] == "left_box"  # the polish may only flip its side
    off = Teaching()
    off.scene = lambda i, a, r: squeezed()
    gen2, _ = await run_teaching(pool, off, None, animator_auto_frame=False, animator_label_relocation=False)
    name2 = next(o for o in (gen2.scenes[0].dsl or {})["objects"] if o["id"] == "left_name")
    assert name2["place"]["relativeTo"] == "left_box"


async def test_a_text_inside_a_card_stays_inside_it(pool: EngineWorkerPool) -> None:
    card = scene(
        [
            {
                "id": "card_bg",
                "type": "rectangle",
                "params": {"width": 520, "height": 300},
                "place": {"x": 800, "y": 430},
            },
            {"id": "card_dot", "type": "circle", "params": {"radius": 30}, "place": {"x": 800, "y": 430}},
            {
                "id": "card_value",
                "type": "text",
                "params": {"text": "Yes, found"},
                "place": {"x": 800, "y": 440},
            },
        ],
        [
            {"id": "a1", "action": "create", "target": "card_bg", "start": {"at": 0.5}},
            {"id": "a2", "action": "create", "target": "card_value", "start": {"after": "a1"}},
            {"id": "a3", "action": "create", "target": "card_dot", "start": {"after": "a2"}},
        ],
    )
    s = settings()
    pipe = Pipeline(EngineClient(pool), None, s)  # type: ignore[arg-type]
    res = await pipe.engine.compile(card, None)
    assert "card_value" in troubled_labels(res)  # the dot drawn after it covers it
    dsl, _, _ = await pipe._relocate(copy.deepcopy(card), res, None)
    value = next(o for o in dsl["objects"] if o["id"] == "card_value")
    assert value["place"] == {"x": 800, "y": 440}  # meant to be inside its card: left there


def test_a_frame_and_a_drawing_are_recognised() -> None:
    label = {"x": 100.0, "y": 100.0, "w": 80.0, "h": 30.0}
    assert _inside_a_frame("t", label, {"t": label, "card": {"x": 50.0, "y": 50.0, "w": 400.0, "h": 300.0}})
    assert not _inside_a_frame("t", label, {"t": label, "dot": {"x": 90.0, "y": 90.0, "w": 60.0, "h": 60.0}})
    boxes = {
        "grape_cluster_berry_1": {"x": 0.0, "y": 0.0, "w": 50.0, "h": 50.0},
        "grape_cluster_berry_2": {"x": 40.0, "y": 0.0, "w": 50.0, "h": 50.0},
        "grape_cluster_leaf": {"x": 20.0, "y": -30.0, "w": 40.0, "h": 30.0},
        "label_grapes": {"x": 200.0, "y": 0.0, "w": 80.0, "h": 30.0},
    }
    assert _drawing_of("grape_cluster_berry_2", boxes) == (0.0, -30.0, 90.0, 50.0)
    assert _drawing_of("label_grapes", boxes) is None and _drawing_of("solo", boxes) is None
