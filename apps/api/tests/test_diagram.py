"""Diagrams laid out by the engine (ADR-061), in the pipeline: a scene with one compiles clean, the critic
reads what it joins, and the planner moves and scales it as one item without shrinking its text past
readable."""

from __future__ import annotations

import copy
from itertools import combinations
from typing import Any

from animator.engine_client import EngineClient, EngineWorkerPool
from animator.pipeline.generator import describe_diagram, glitches_of, summarize_for_critic
from animator.pipeline.layout_plan import LayoutPlanner, parse_plan
from animator.pipeline.layout_plan.figures import text_px
from animator.pipeline.layout_plan.regions import Box, regions
from animator.pipeline.tidy import tidy

STAGE = regions("stage_with_caption")["stage"]

HISTORY: dict[str, Any] = {
    "layout": "lanes",
    "lanes": [{"id": "main", "label": "main"}, {"id": "feature", "label": "feature"}],
    "nodes": [
        {"id": "a", "label": "A", "lane": "main"},
        {"id": "b", "label": "B", "lane": "main"},
        {"id": "e", "label": "E", "lane": "main"},
        {"id": "c", "label": "C", "lane": "feature"},
        {"id": "d", "label": "D", "lane": "feature", "ghost": True},
    ],
    "edges": [
        {"from": "a", "to": "b"},
        {"from": "b", "to": "e"},
        {"from": "b", "to": "c"},
        {"from": "c", "to": "d"},
    ],
    "width": 700,
    "height": 320,
}


def scene(stage: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "dsl_version": "1.0",
        "id": "x",
        "title": "Two lines of work",
        "duration": 8,
        "layout": "stage_with_caption",
        "narration": {
            "beats": [
                {"id": "b1", "text": "Main and a feature branch."},
                {"id": "b2", "text": "Each keeps its own commits."},
            ]
        },
        "objects": [
            {"id": "history", "type": "diagram", "params": HISTORY, "place": {"x": 420, "y": 420}},
            {
                "id": "summary",
                "type": "card",
                "params": {
                    "title": "Two histories",
                    "items": ["main moves on", "feature branches off"],
                    "width": 420,
                },
                "place": {"x": 1180, "y": 420},
            },
        ],
        "timeline": [
            {"id": "c_history", "action": "create", "target": "history", "start": {"at": "beat:b1"}},
            {"id": "w_d", "action": "write", "target": "history", "terms": ["d"], "start": {"at": "beat:b2"}},
            {"id": "c_summary", "action": "create", "target": "summary", "start": {"after": "w_d"}},
        ],
    }
    if stage:
        out["stage"] = {
            "direction": None,
            "groups": [
                {
                    "id": "both",
                    "region": None,
                    "arrange": "row",
                    "items": ["history", "summary"],
                    "align": None,
                    "columns": None,
                    "connected": False,
                }
            ],
        }
    return out


async def test_a_diagram_compiles_clean_and_is_described_for_the_critic(pool: EngineWorkerPool) -> None:
    s = scene(stage=False)
    res = await EngineClient(pool).compile(s, None)
    assert res.ok, [i.message for i in res.issues if i.severity == "error"]
    assert glitches_of(res) == []
    text = summarize_for_critic(s, res)
    assert "- history (diagram): lanes diagram; nodes: a 'A', b 'B', e 'E', c 'C', d 'D' (ghost)" in text
    assert "edges: a->b, b->e, b->c, c->d" in text
    assert describe_diagram({"nodes": [{"id": "s"}]}) == "layered diagram; nodes: s; edges: none"


async def test_ids_a_model_writes_its_own_way_are_cleaned_and_compile(pool: EngineWorkerPool) -> None:
    s = scene(stage=False)
    history = s["objects"][0]["params"] = copy.deepcopy(HISTORY)
    for n in history["nodes"]:
        n["id"] = n["label"] + "'" if n["id"] == "c" else n["label"]  # 'A', 'B', "C'", 'E', 'D'
    history["lanes"][0]["id"] = "Main"
    history["nodes"][0]["lane"] = history["nodes"][1]["lane"] = history["nodes"][2]["lane"] = "Main"
    history["edges"] = [
        {"from": "A", "to": "B"},
        {"from": "B", "to": "E"},
        {"from": "B", "to": "C'"},
        {"from": "C'", "to": "D"},
    ]
    s["objects"].append(
        {
            "id": "copy_tag",
            "type": "label",
            "params": {"text": "a copy"},
            "place": {
                "relativeTo": "history.C'",
                "side": "below",
                "gap": 16,
                "align": "center",
                "follow": None,
            },
        }
    )
    s["timeline"][1] = {
        "id": "Write_D",
        "action": "write",
        "target": "history",
        "terms": ["D"],
        "start": {"at": "beat:b2"},
    }
    s["timeline"][2]["start"] = {"after": "Write_D"}
    res = await EngineClient(pool).compile(tidy(s), None)
    assert res.ok, [i.message for i in res.issues if i.severity == "error"]
    assert not [i for i in res.issues if i.code.startswith(("DIAGRAM_", "UNKNOWN_"))]


def test_the_planner_keeps_a_diagrams_text_readable() -> None:
    assert text_px({"h": {"type": "diagram", "params": {"size": "small"}}}, "h") == 20
    assert text_px({"h": {"type": "diagram", "params": {}, "scale": 0.5}}, "h") == 11
    assert text_px({"h": {"type": "circle", "params": {}}}, "h") is None


async def test_the_planner_lays_a_diagram_out_as_one_item(pool: EngineWorkerPool) -> None:
    engine = EngineClient(pool)
    s = scene()
    plan = parse_plan(s.pop("stage"))
    assert plan is not None
    res = await engine.compile(s, None)
    assert res.ok
    planned = await LayoutPlanner(engine).plan(s, plan, res, None)
    best = planned.candidates[0]
    again = await engine.compile(best.dsl, None)
    assert again.ok, [i.message for i in again.issues if i.severity == "error"]
    assert not any(i.code in ("TEXT_TOO_SMALL", "OUT_OF_STAGE", "OUT_OF_BOUNDS") for i in again.issues)
    sample = await engine.sample(best.dsl, [7.9], None)
    boxes = {o["id"]: Box.of(o["bbox"]) for o in sample.frames[0]["objects"]}
    for a, b in combinations([boxes["history"], boxes["summary"]], 2):
        assert a.overlap(b) == 0
    assert boxes["history"].cx < boxes["summary"].cx
    assert STAGE.x - 1 <= boxes["history"].x and boxes["summary"].right <= STAGE.right + 1
