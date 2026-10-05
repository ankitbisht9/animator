"""Relations (ADR-048): what sits on, in or along what, checked with the engine's geometry at every
moment it should hold, and snapped right without a model call. Cars beside a road end up on it, a truck
driving a curved road follows the curve, a block on a ramp stands on the slope, a ball thrown into a
basket lands in it; contact the scene means is not counted as a glitch; what cannot be fixed reaches
the reviewer precisely."""

from __future__ import annotations

import math
from typing import Any

import pytest

from animator.engine_client import EngineClient, EngineWorkerPool
from animator.pipeline.generator import Pipeline, planned_glitches
from animator.pipeline.layout_plan.geometry import (
    Shape,
    _flatten_path,
    arms,
    between,
    edge_at,
    middle_line,
    nearest,
    shape_of,
)
from animator.pipeline.layout_plan.plan import PlanRelation, StagePlan, parse_plan
from animator.pipeline.layout_plan.regions import Box
from animator.pipeline.layout_plan.relations import describe, intended, measure
from tests.test_pipeline import settings

# ---------------------------------------------------------------------- geometry


def test_a_turned_rectangle_has_a_sloped_top() -> None:
    m = math.radians(-20)  # a ramp: 20 degrees, rising to the right
    obj = {
        "id": "ramp",
        "type": "rectangle",
        "matrix": [math.cos(m), math.sin(m), -math.sin(m), math.cos(m), 800, 500],
        "localBBox": {"x": -300, "y": -20, "w": 600, "h": 40},
        "params": {},
    }
    shape = shape_of(obj)
    assert shape is not None
    hit = edge_at(shape, 800)
    assert hit is not None
    y, slope = hit
    assert slope == pytest.approx(-20, abs=0.5)
    assert y < 500  # the top edge, above the centre


def test_a_curved_path_is_flattened_and_a_band_has_a_middle_line() -> None:
    pts, closed = _flatten_path("M0 0 C 100 -100 200 -100 300 0")
    assert not closed and pts[0] == (0, 0) and pts[-1] == pytest.approx((300, 0))
    assert min(p[1] for p in pts) < -60  # it bends up
    road = Shape("road", ((0, 600), (1000, 600), (1000, 680), (0, 680)), True)
    assert middle_line(road) == [(0, 640), (1000, 640)]
    q, s, angle = nearest([(0, 0), (100, 0), (100, 100)], (130, 60))
    assert q == (100, 60) and s == pytest.approx(160) and angle == pytest.approx(90)


def test_a_junction_drawn_as_one_outline_is_its_roads() -> None:
    plus = [(-330, -78), (-78, -78), (-78, -220), (78, -220), (78, -78), (330, -78)]
    plus += [(330, 78), (78, 78), (78, 220), (-78, 220), (-78, 78), (-330, 78), (-330, -78)]
    roads = arms(Shape("junction", tuple(plus), True))
    assert sorted(middle_line(r)[0] for r in roads) == [(-330, 0), (0, -220)]  # across, and up the page
    corner = [(0, 0), (400, 0), (400, 80), (80, 80), (80, 300), (0, 300)]  # an L
    assert len(arms(Shape("bend", tuple(corner), True))) == 2
    box = Shape("road", ((0, 0), (900, 0), (900, 80), (0, 80)), True)
    assert arms(box) == [box]


def test_a_track_of_two_rails_is_the_way_between_them() -> None:
    near = Shape("rails_near", ((0, 500), (400, 520), (800, 500)), False)
    far = Shape("rails_far", ((0, 440), (400, 460), (800, 440)), False)
    mid = between(near, far, "rails#track")
    assert mid is not None and mid.width == pytest.approx(30, abs=1.5)
    assert nearest(list(mid.points), (400, 300))[0][1] == pytest.approx(490, abs=2)
    assert between(near, Shape("post", ((100, 100), (100, 400)), False), "x") is None  # not side by side


def test_each_relation_says_how_far_off_it_is() -> None:
    road = Shape("road", ((100, 600), (1500, 600), (1500, 680), (100, 680)), True)
    car = Box(400, 450, 120, 60)  # floating 90 px above the road
    on = measure(PlanRelation(subject="car", relation="on", object="road"), car, road, 1.0)
    assert (on.dx, on.dy) == pytest.approx((0, 90))
    along = measure(PlanRelation(subject="car", relation="along", object="road"), car, road, 1.0)
    assert along.dy == pytest.approx(640 - 8 - 480)  # into the near lane (room 40 - 30 - 2 px)
    tank = Shape("tank", ((600, 300), (900, 300), (900, 600), (600, 600)), True)
    ball = Box(560, 400, 60, 60)
    inside = measure(PlanRelation(subject="ball", relation="inside", object="tank"), ball, tank, 1.0)
    assert inside.dx == pytest.approx(604 - 560) and inside.dy == 0
    assert (
        measure(PlanRelation(subject="b", relation="above", object="t"), Box(600, 200, 50, 50), tank, 0).size
        == 0
    )


def test_a_car_in_its_lane_is_on_the_road_and_one_lying_across_it_is_turned() -> None:
    road = Shape("road", ((100, 600), (1500, 600), (1500, 700), (100, 700)), True)  # middle at y 650
    rel = PlanRelation(subject="car", relation="along", object="road")
    lane = measure(rel, Box(400, 605, 80, 40), road, 1.0, heading=0.0)  # the upper lane
    assert lane.size == 0
    across = measure(rel, Box(400, 610, 40, 80), road, 1.0, heading=90.0)
    assert abs(across.turn) == 90 and "degrees across" in describe(across)
    assert measure(rel, Box(400, 620, 80, 40), road, 1.0, heading=8.0).size == 0  # nearly along


def test_a_ball_touches_a_slope_off_its_centre() -> None:
    ramp = Shape("ramp", ((200, 600), (800, 300), (800, 700), (200, 700)), True)
    rel = PlanRelation(subject="ball", relation="on", object="ramp")
    r, theta = 50.0, math.atan2(-300, 600)
    cx, cy = 500 + r * math.sin(theta), 450 - r * math.cos(theta)  # resting on the slope at (500, 450)
    circle = [(cx + r * math.cos(k * math.pi / 36), cy + r * math.sin(k * math.pi / 36)) for k in range(72)]
    box = Box(cx - r, cy - r, 2 * r, 2 * r)
    assert abs(measure(rel, box, ramp, 1.0, points=circle).dy) < 1  # touching: neither floating nor sunk
    assert measure(rel, box, ramp, 1.0).dy < -3  # its box's corner would be sunk into the slope


def test_contact_a_relation_means_is_not_a_glitch() -> None:
    rels = [PlanRelation(subject="car", relation="on", object="road")]
    assert intended("'car_body' covers part of 'road_surface' at 2s.", rels)
    assert not intended("'car_body' covers part of 'house' at 2s.", rels)
    assert not intended(
        "'car_body' covers part of 'road' at 2s.",
        [PlanRelation(subject="car", relation="near", object="road")],
    )


def test_relations_are_read_from_the_plan() -> None:
    plan = parse_plan(
        {
            "groups": [],
            "relations": [
                {"subject": "car_1", "relation": "on", "object": "road", "when": None},
                {"subject": "x", "relation": "orbiting", "object": "y"},  # not a relation: dropped
                {"subject": "road", "relation": "on", "object": "road"},  # itself: dropped
            ],
        }
    )
    assert plan is not None and [r.subject for r in plan.relations] == ["car_1"]


# ---------------------------------------------------------------------- on scenes


def scene(
    objects: list[dict[str, Any]], timeline: list[dict[str, Any]], duration: float = 6
) -> dict[str, Any]:
    return {
        "dsl_version": "1.0",
        "id": "x",
        "title": "Roads",
        "duration": duration,
        "layout": "stage_with_caption",
        "narration": {"beats": [{"id": "b1", "text": "Cars drive on the **road**."}]},
        "objects": [
            {
                "id": "title_s01",
                "type": "text",
                "params": {"text": "Roads", "size": "heading", "weight": "bold"},
                "place": {"slot": "title_bar"},
                "layer": "overlay",
            },
            *objects,
        ],
        "timeline": [{"id": "t0", "action": "write", "target": "title_s01", "start": {"at": 0.2}}, *timeline],
    }


def created(*ids: str) -> list[dict[str, Any]]:
    return [{"id": f"c_{i}", "action": "create", "target": i, "start": {"at": 0.4}} for i in ids]


async def boxes_at(engine: EngineClient, dsl: dict[str, Any], t: float) -> dict[str, dict[str, Any]]:
    sample = await engine.sample(dsl, [t], None)
    return {o["id"]: o for o in sample.frames[0]["objects"]}


async def test_cars_drawn_beside_the_road_end_up_on_it(pool: EngineWorkerPool) -> None:
    engine = EngineClient(pool)
    road = {
        "id": "road",
        "type": "rectangle",
        "params": {"width": 1200, "height": 70},
        "place": {"x": 800, "y": 600},
    }
    cars = [
        {
            "id": "car_1",
            "type": "rectangle",
            "params": {"width": 120, "height": 50},
            "place": {"x": 500, "y": 480},
        },
        {
            "id": "car_2",
            "type": "rectangle",
            "params": {"width": 120, "height": 50},
            "place": {"x": 1000, "y": 660},
        },
    ]
    dsl = scene([road, *cars], created("road", "car_1", "car_2"))
    plan = parse_plan(
        {
            "groups": [],
            "relations": [
                {"subject": "car_1", "relation": "on", "object": "road"},
                {"subject": "car_2", "relation": "on", "object": "road"},
            ],
        }
    )
    assert plan is not None
    pipe = Pipeline(engine, None, settings())  # type: ignore[arg-type]
    report: dict[str, Any] = {}
    fixed, res, _ = await pipe._check(dsl, None, False, plan, report)
    assert res.ok, [i.message for i in res.issues if i.severity == "error"]
    assert len(report["relations"]["broken"]) == 2 and report["relations"]["unresolved"] == []
    now = await boxes_at(engine, fixed, 3.0)
    road_top = now["road"]["bbox"]["y"]
    for car in ("car_1", "car_2"):
        b = now[car]["bbox"]
        assert b["y"] + b["h"] == pytest.approx(road_top, abs=1.5)  # standing on the road


async def test_a_truck_driving_a_curved_road_follows_it(pool: EngineWorkerPool) -> None:
    engine = EngineClient(pool)
    road = {
        "id": "road",
        "type": "path",
        "params": {"d": "M 0 0 C 300 -260 600 -260 900 0"},
        "place": {"x": 800, "y": 560},  # a hill: ends at (350, 657) and (1250, 657), top at y 462
    }
    truck = {
        "id": "truck",
        "type": "rectangle",
        "params": {"width": 60, "height": 40},
        "place": {"x": 330, "y": 600},  # drawn near the road's start, not on it
    }
    move = {
        "id": "drive",
        "action": "move",
        "target": "truck",
        "to": {"x": 1250, "y": 640},
        "start": {"at": 1},
        "duration": 3,
    }
    dsl = scene([road, truck], [*created("road", "truck"), move])
    plan = parse_plan(
        {"groups": [], "relations": [{"subject": "truck", "relation": "along", "object": "road"}]}
    )
    assert plan is not None
    pipe = Pipeline(engine, None, settings())  # type: ignore[arg-type]
    report: dict[str, Any] = {}
    fixed, res, _ = await pipe._check(dsl, None, False, plan, report)
    assert res.ok
    assert any("follows it" in s for s in report["relations"].get("snapped", []))
    times = [0.95 + 0.1 * k for k in range(34)]  # just before it drives, then all the way along
    sample = await engine.sample(fixed, times, None)
    road_shape = shape_of(next(o for o in sample.frames[0]["objects"] if o["id"] == "road"))
    assert road_shape is not None
    line = middle_line(road_shape)
    off = []
    for frame in sample.frames:
        tb = next(o for o in frame["objects"] if o["id"] == "truck")["bbox"]
        centre = (tb["x"] + tb["w"] / 2, tb["y"] + tb["h"] / 2)
        q, _, _ = nearest(line, centre)
        off.append(math.dist(q, centre))
    assert max(off) < 6  # on the road the whole way, over the hill, not along the straight chord
    turn = next(a for a in fixed["timeline"] if a["id"] == "drive_turn")["keyframes"]
    assert turn[0]["value"] < -25 and turn[-1]["value"] > 25  # it turns with the road: up, then down
    heights = [next(o for o in f["objects"] if o["id"] == "truck")["bbox"] for f in sample.frames]
    assert min(h["y"] + h["h"] / 2 for h in heights) < 480  # it goes over the hill (the chord stays at 600+)


async def test_a_block_on_a_ramp_stands_on_the_slope(pool: EngineWorkerPool) -> None:
    engine = EngineClient(pool)
    ramp = {
        "id": "ramp",
        "type": "polygon",
        "params": {"points": [{"x": -300, "y": 100}, {"x": 300, "y": 100}, {"x": 300, "y": -120}]},
        "place": {"x": 800, "y": 520},
    }
    block = {
        "id": "block",
        "type": "rectangle",
        "params": {"width": 80, "height": 60},
        "place": {"x": 800, "y": 380},
    }
    dsl = scene([ramp, block], created("ramp", "block"))
    plan = parse_plan({"groups": [], "relations": [{"subject": "block", "relation": "on", "object": "ramp"}]})
    assert plan is not None
    pipe = Pipeline(engine, None, settings())  # type: ignore[arg-type]
    report: dict[str, Any] = {}
    fixed, res, _ = await pipe._check(dsl, None, False, plan, report)
    assert res.ok
    block_now = next(o for o in fixed["objects"] if o["id"] == "block")
    slope = math.degrees(math.atan2(-220, 600))
    assert block_now["rotation"] == pytest.approx(slope, abs=1)  # tilted with the ramp
    assert report["relations"]["unresolved"] == []


async def test_a_ball_thrown_into_a_basket_lands_in_it(pool: EngineWorkerPool) -> None:
    engine = EngineClient(pool)
    basket = {
        "id": "basket",
        "type": "rectangle",
        "params": {"width": 200, "height": 140},
        "place": {"x": 1100, "y": 560},
    }
    ball = {"id": "ball", "type": "circle", "params": {"radius": 25}, "place": {"x": 400, "y": 560}}
    throw = {
        "id": "throw",
        "action": "move",
        "target": "ball",
        "to": {"x": 1260, "y": 520},
        "start": {"at": 1},
        "duration": 1.5,
    }
    dsl = scene([basket, ball], [*created("basket", "ball"), throw])
    plan = parse_plan(
        {
            "groups": [],
            "relations": [{"subject": "ball", "relation": "inside", "object": "basket", "when": "end"}],
        }
    )
    assert plan is not None
    pipe = Pipeline(engine, None, settings())  # type: ignore[arg-type]
    report: dict[str, Any] = {}
    fixed, res, _ = await pipe._check(dsl, None, False, plan, report)
    assert res.ok
    end = await boxes_at(engine, fixed, 5.5)
    ball_box = Box.of(end["ball"]["bbox"])
    basket_box = Box.of(end["basket"]["bbox"])
    assert basket_box.contains(ball_box.x, ball_box.y) and basket_box.contains(
        ball_box.right, ball_box.bottom
    )
    assert planned_glitches(res, plan) == 0


async def test_a_relation_that_cannot_be_kept_goes_to_the_reviewer(pool: EngineWorkerPool) -> None:
    from tests.test_pipeline import text_of
    from tests.test_teaching import Frames, Teaching, run_teaching

    def drawn(i: int, attempt: int, req: Any) -> dict[str, Any]:
        box = {
            "id": "box",
            "type": "rectangle",
            "params": {"width": 60, "height": 60},
            "place": {"x": 800, "y": 420},
        }
        ball = {"id": "ball", "type": "circle", "params": {"radius": 100}, "place": {"x": 500, "y": 420}}
        dsl = scene([box, ball], created("box", "ball"))
        dsl["stage"] = {
            "direction": None,
            "groups": [],
            "relations": [{"subject": "ball", "relation": "inside", "object": "box", "when": None}],
        }
        return dsl

    script = Teaching()
    script.scene = drawn
    gen, _ = await run_teaching(pool, script, Frames(), animator_layout_mode="planned")
    assert gen.status == "complete", gen.error
    fix = next(r for r in script.calls if r.label == "scene 1 attempt 2")
    note = text_of(fix)
    assert "Things are not where the scene's plan says" in note and "'ball' should be inside 'box'" in note


def icon_car(oid: str, x: float, y: float, rotation: float = 0) -> dict[str, Any]:
    return {
        "id": oid,
        "type": "icon",
        "params": {"name": "car", "size": 60},
        "place": {"x": x, "y": y},
        "rotation": rotation,
    }


async def test_cars_at_a_junction_lie_along_their_roads_in_their_lanes(pool: EngineWorkerPool) -> None:
    """Seen from above, with no relation written: a car lying across its road and one drawn beside the
    junction end up on their own arm of it, in a lane, lying along it (and upright when either way
    would do)."""
    engine = EngineClient(pool)
    junction = [
        {"id": "junction", "type": "group", "params": {}, "place": {"x": 800, "y": 450}},
        {
            "id": "junction_ns",
            "type": "rectangle",
            "params": {"width": 160, "height": 640},
            "place": {"x": 800, "y": 450},
            "parent": "junction",
        },
        {
            "id": "junction_ew",
            "type": "rectangle",
            "params": {"width": 900, "height": 160},
            "place": {"x": 800, "y": 450},
            "parent": "junction",
        },
    ]
    cars = [icon_car("car_north", 800, 230), icon_car("car_west", 450, 560, rotation=90)]
    shown = created("junction", "junction_ns", "junction_ew", "car_north", "car_west")
    dsl = scene([*junction, *cars], shown)
    plan = StagePlan(groups=[], relations=[])
    pipe = Pipeline(engine, None, settings())  # type: ignore[arg-type]
    report: dict[str, Any] = {}
    fixed, res, _ = await pipe._check(dsl, None, False, plan, report)
    assert res.ok, [i.message for i in res.issues if i.severity == "error"]
    assert sorted(report["relations"]["implied"]) == ["car_north along junction", "car_west along junction"]
    rotation = {o["id"]: float(o.get("rotation") or 0) for o in fixed["objects"]}
    assert abs(rotation["car_north"]) == pytest.approx(90)  # along the north-south road
    assert rotation["car_west"] == pytest.approx(0)  # along the east-west road, upright
    now = await boxes_at(engine, fixed, 3.0)
    ns, ew = Box.of(now["junction_ns"]["bbox"]), Box.of(now["junction_ew"]["bbox"])
    north, west = Box.of(now["car_north"]["bbox"]), Box.of(now["car_west"]["bbox"])
    assert ns.x <= north.x and north.right <= ns.right  # within its road's width
    assert ew.y <= west.y and west.bottom <= ew.bottom
    assert report["relations"]["unresolved"] == []


async def test_a_name_for_two_separate_cars_puts_each_on_the_road(pool: EngineWorkerPool) -> None:
    engine = EngineClient(pool)
    road = {
        "id": "road",
        "type": "rectangle",
        "params": {"width": 1200, "height": 120},
        "place": {"x": 800, "y": 500},
    }
    taxis = [icon_car("taxis_1", 400, 410), icon_car("taxis_2", 1200, 590)]  # one above it, one below
    dsl = scene([road, *taxis], created("road", "taxis_1", "taxis_2"))
    plan = parse_plan(
        {"groups": [], "relations": [{"subject": "taxis", "relation": "along", "object": "road"}]}
    )
    assert plan is not None
    pipe = Pipeline(engine, None, settings())  # type: ignore[arg-type]
    report: dict[str, Any] = {}
    fixed, res, _ = await pipe._check(dsl, None, False, plan, report)
    assert res.ok
    assert report["relations"]["checked"] == 2  # one relation per taxi
    now = await boxes_at(engine, fixed, 3.0)
    band = Box.of(now["road"]["bbox"])
    for taxi in ("taxis_1", "taxis_2"):
        b = Box.of(now[taxi]["bbox"])
        assert band.y <= b.y and b.bottom <= band.bottom


@pytest.mark.parametrize("dest", ["to", "by"])
async def test_a_ball_rolling_down_a_ramp_touches_it_all_the_way(pool: EngineWorkerPool, dest: str) -> None:
    """A ball that rolls (turns as it goes) is measured as the circle it is, not its turned box: its end,
    left floating above the ramp, is snapped down and it touches the ramp the whole way (a move to a
    point, or by an amount: then its start, snapped too, is allowed for)."""
    engine = EngineClient(pool)
    slope = [{"x": -300, "y": 150}, {"x": 300, "y": -150}, {"x": 300, "y": -80}, {"x": -300, "y": 220}]
    ramp = {"id": "ramp", "type": "polygon", "params": {"points": slope}, "place": {"x": 535, "y": 470}}
    ball = {"id": "ball", "type": "ball", "params": {"radius": 62}, "place": {"x": 724, "y": 268}}
    roll = [
        {
            "id": "roll",
            "action": "move",
            "target": "ball",
            dest: {"x": 570, "y": 318} if dest == "to" else {"x": -154, "y": 50},
            "start": {"at": 1},
            "duration": 2.8,
        },
        {"id": "spin", "action": "rotate", "target": "ball", "to": -360, "start": {"at": 1}, "duration": 2.8},
    ]
    dsl = scene([ramp, ball], [*created("ramp", "ball"), *roll])
    plan = parse_plan({"groups": [], "relations": [{"subject": "ball", "relation": "on", "object": "ramp"}]})
    assert plan is not None
    pipe = Pipeline(engine, None, settings())  # type: ignore[arg-type]
    report: dict[str, Any] = {}
    fixed, res, _ = await pipe._check(dsl, None, False, plan, report)
    assert res.ok
    assert report["relations"]["unresolved"] == []
    sample = await engine.sample(fixed, [1.1 + 0.25 * k for k in range(16)], None)  # popped in
    radius = 62
    for frame in sample.frames:
        objs = {o["id"]: o for o in frame["objects"]}
        top = shape_of(objs["ramp"])
        assert top is not None
        (x0, y0), (x1, y1) = top.points[0], top.points[1]  # the slope
        b = Box.of(objs["ball"]["bbox"])  # a turning ball's box is still centred on it
        line = abs((x1 - x0) * (y0 - b.cy) - (x0 - b.cx) * (y1 - y0)) / math.dist((x0, y0), (x1, y1))
        assert abs(line - radius) < 1.5, (frame["t"], line - radius)


def junction_parts() -> list[dict[str, Any]]:
    return [
        {"id": "junction", "type": "group", "params": {}, "place": {"x": 800, "y": 450}},
        {
            "id": "junction_ns",
            "type": "rectangle",
            "params": {"width": 160, "height": 640},
            "place": {"x": 800, "y": 450},
            "parent": "junction",
        },
        {
            "id": "junction_ew",
            "type": "rectangle",
            "params": {"width": 900, "height": 160},
            "place": {"x": 800, "y": 450},
            "parent": "junction",
        },
    ]


async def test_a_car_turning_at_a_junction_takes_the_corner(pool: EngineWorkerPool) -> None:
    """A car driving from the west road into the north road goes along the first in its lane, round the
    corner and up the second, turning as it goes; one driving straight through the crossing is not
    taken for lying across the other road. `on` a road seen from above means along it."""
    engine = EngineClient(pool)
    turning = icon_car("car_turning", 450, 480)
    through = icon_car("car_through", 780, 200, rotation=90)
    moves = [
        {
            "id": "turn_left",
            "action": "move",
            "target": "car_turning",
            "to": {"x": 780, "y": 180},
            "start": {"at": 1.2},
            "duration": 3,
        },
        {
            "id": "go_south",
            "action": "move",
            "target": "car_through",
            "to": {"x": 780, "y": 720},
            "start": {"at": 1.2},
            "duration": 3,
        },
    ]
    shown = created("junction", "junction_ns", "junction_ew", "car_turning", "car_through")
    dsl = scene([*junction_parts(), turning, through], [*shown, *moves], duration=6)
    plan = parse_plan(
        {
            "groups": [],
            "relations": [
                {"subject": "car_turning", "relation": "on", "object": "junction"},
                {"subject": "car_through", "relation": "along", "object": "junction"},
            ],
        }
    )
    assert plan is not None
    pipe = Pipeline(engine, None, settings())  # type: ignore[arg-type]
    report: dict[str, Any] = {}
    fixed, res, _ = await pipe._check(dsl, None, False, plan, report)
    assert res.ok
    assert report["relations"]["unresolved"] == [], report["relations"]
    assert not any("car_through" in b for b in report["relations"]["broken"])  # straight through: fine
    turn = next(a for a in fixed["timeline"] if a["id"] == "turn_left_turn")["keyframes"]
    assert turn[0]["value"] == pytest.approx(0, abs=5) and turn[-1]["value"] == pytest.approx(-90, abs=5)
    sample = await engine.sample(fixed, [1.2 + 0.15 * k for k in range(21)], None)
    for frame in sample.frames:
        objs = {o["id"]: o for o in frame["objects"]}
        car = Box.of(objs["car_turning"]["bbox"])
        on_road = Box.of(objs["junction_ns"]["bbox"]).contains(car.cx, car.cy) or Box.of(
            objs["junction_ew"]["bbox"]
        ).contains(car.cx, car.cy)
        assert on_road, frame["t"]  # never cutting across the corner off the road


async def test_a_wheelset_rests_on_both_rails_whatever_is_drawn_beside_it(pool: EngineWorkerPool) -> None:
    """Seen end on: two wheels on an axle, floating above two rails, with a contact marker drawn apart
    below them. The wheels come to rest on the rails' tops (both rails, not one); the marker, not part
    of the wheelset's body, does not decide where it rests."""
    engine = EngineClient(pool)
    rails = [
        {
            "id": "rails_left",
            "type": "rectangle",
            "params": {"width": 160, "height": 20},
            "place": {"x": 600, "y": 620},
        },
        {
            "id": "rails_right",
            "type": "rectangle",
            "params": {"width": 160, "height": 20},
            "place": {"x": 1000, "y": 620},
        },
    ]
    wheelset = [
        {"id": "wheelset", "type": "group", "params": {}, "place": {"x": 800, "y": 480}},
        {
            "id": "wheelset_axle",
            "type": "rectangle",
            "params": {"width": 420, "height": 24},
            "place": {"x": 800, "y": 480},
            "parent": "wheelset",
        },
        {
            "id": "wheelset_left",
            "type": "circle",
            "params": {"radius": 80},
            "place": {"x": 600, "y": 480},
            "parent": "wheelset",
        },
        {
            "id": "wheelset_right",
            "type": "circle",
            "params": {"radius": 80},
            "place": {"x": 1000, "y": 480},
            "parent": "wheelset",
        },
        {
            "id": "wheelset_marker",
            "type": "circle",
            "params": {"radius": 10},
            "place": {"x": 800, "y": 600},  # below the axle, apart from the wheels
            "parent": "wheelset",
        },
    ]
    shown = created(
        "rails_left",
        "rails_right",
        "wheelset",
        "wheelset_axle",
        "wheelset_left",
        "wheelset_right",
        "wheelset_marker",
    )
    dsl = scene([*rails, *wheelset], shown)
    plan = parse_plan(
        {"groups": [], "relations": [{"subject": "wheelset", "relation": "on", "object": "rails"}]}
    )
    assert plan is not None
    pipe = Pipeline(engine, None, settings())  # type: ignore[arg-type]
    report: dict[str, Any] = {}
    fixed, res, _ = await pipe._check(dsl, None, False, plan, report)
    assert res.ok
    assert report["relations"]["unresolved"] == [], report["relations"]
    now = await boxes_at(engine, fixed, 3.0)
    for wheel, rail in (("wheelset_left", "rails_left"), ("wheelset_right", "rails_right")):
        assert now[wheel]["bbox"]["y"] + now[wheel]["bbox"]["h"] == pytest.approx(
            now[rail]["bbox"]["y"], abs=1.5
        )
