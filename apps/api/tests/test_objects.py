"""Saved objects and cost-saver lessons (ADR-055): drawings, their checks, which ones a scene may use,
how a scene places them, the built-in library, the warehouse's harvest, and lessons that use them while
classic and planned lessons stay exactly as they were."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from animator import REPO_ROOT
from animator.engine_client import EngineClient, EngineWorkerPool
from animator.llm import LLMRequest
from animator.objects import art as A
from animator.objects import library
from animator.objects.harvest import Harvester, candidates, concept_of
from animator.objects.matching import SceneText, forms, offers_for
from animator.objects.models import COLOR_ROLES, Art, SavedObject, Shape
from animator.objects.prompting import offer_text, with_saved
from animator.objects.quality import check, to_art, with_anchors
from animator.objects.resolve import compact, fit_width, placed_refs, resolve
from animator.objects.store import ObjectStore
from animator.objects.svg import parse_svg, part_name
from tests.test_layout_plan import run_lesson
from tests.test_teaching import Teaching, direction, teacher_script

BUILT = {oid: b for oid, b in library.build().items()}
LIB = {oid: b.obj for oid, b in BUILT.items() if b.obj is not None}


def obj(oid: str) -> SavedObject:
    o = LIB[oid]
    assert o is not None
    return o


# ---------------------------------------------------------------------- geometry


def test_path_data_in_every_form_becomes_moves_lines_and_curves() -> None:
    segs = A.parse_path("m10 10 h80 v80 h-80 z")
    assert [s[0] for s in segs] == ["M", "L", "L", "L", "Z"]
    assert A.bounds(segs) == A.Box(10, 10, 90, 90)
    # Shorthand curves and arcs are exact curves; their bounds are the curve's, not its handles'.
    circle = A.parse_path("M0 50 A50 50 0 1 0 100 50 A50 50 0 1 0 0 50 Z")
    b = A.bounds(circle)
    assert b is not None and b.x0 == pytest.approx(0, abs=0.01) and b.y1 == pytest.approx(100, abs=0.2)
    assert {s[0] for s in A.parse_path("M0 0 Q 50 -50 100 0 T 200 0 S 250 50 300 0")} == {"M", "C"}
    # Compact arc flags ("1150 20": flags 1 and 1, then 50) are read as SVG allows.
    assert len(A.parse_path("M10 10 a20 30 30 1150 20")) > 1
    for bad in ("", "10 10", "M10", "M10 10 L oops", "M0 0 A10 10 0 2 0 5 5"):
        with pytest.raises(A.PathError):
            A.parse_path(bad)


def test_paths_are_written_back_compactly_and_exactly() -> None:
    segs = A.parse_path("M10.123 20 C 30 40 50 60 70.555 80 Z")
    text = A.serialize(segs)
    assert text == "M10.12 20C30 40 50 60 70.56 80Z"
    moved = A.transform(segs, (2, 0, 0, 2, 5, -5))
    b = A.bounds(moved)
    assert b is not None and b.x0 == pytest.approx(25.246, abs=0.01)


def test_a_point_inside_and_its_distance_to_the_edge() -> None:
    ring = A.flatten(A.parse_path("M0 0 L100 0 L100 100 L0 100 Z"))
    assert A.inside(50, 50, ring) and not A.inside(150, 50, ring)
    assert A.distance(50, 40, ring) == pytest.approx(40)
    donut = A.flatten(A.parse_path("M0 0 L100 0 L100 100 L0 100 Z M25 25 L75 25 L75 75 L25 75 Z"))
    assert not A.inside(50, 50, donut, evenodd=True) and A.inside(10, 10, donut, evenodd=True)


# ---------------------------------------------------------------------- SVG in


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">
  <title>a test</title>
  <g data-part="body" data-fill="red" stroke="#000" stroke-width="2" transform="translate(10 0)">
    <rect x="0" y="0" width="100" height="100" rx="10"/>
  </g>
  <circle id="LidTop" cx="160" cy="20" r="18" fill="rgb(0, 128, 255)"/>
  <ellipse cx="160" cy="70" rx="20" ry="10" style="fill: url(#g); opacity: .5"/>
  <defs><linearGradient id="g">
    <stop stop-color="#ff0000"/><stop stop-color="#00ff00"/>
  </linearGradient></defs>
  <text x="10" y="10">label</text>
  <path d="M0 0 L5 5" display="none"/>
  <circle data-anchor="tip" cx="190" cy="95" r="1"/>
</svg>"""


def test_an_svg_drawing_is_read_as_shapes_with_parts_and_colors() -> None:
    got = parse_svg(SVG)
    assert [s.part for s in got.shapes] == ["body", "lid_top", None]
    body, lid, oval = got.shapes
    assert body.fill == "red" and body.stroke == "#000000" and body.stroke_width == 2
    assert A.bounds(body.segs) == A.Box(10, 0, 110, 100)  # its group's transform applied
    assert lid.fill == "#0080ff"
    assert oval.fill == "#00ff00" and oval.opacity == pytest.approx(0.5)  # a gradient's middle color
    assert got.anchors == [("tip", 190.0, 95.0)]
    assert any("text" in w for w in got.warnings)


@pytest.mark.parametrize(
    "bad",
    [
        '<!DOCTYPE svg [<!ENTITY a "x">]><svg xmlns="http://www.w3.org/2000/svg"><rect width="5"/></svg>',
        "<html><body/></html>",
        "not xml at all",
        '<svg xmlns="http://www.w3.org/2000/svg"><text>only text</text></svg>',
    ],
)
def test_unsafe_or_empty_svg_files_are_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_svg(bad)


def test_part_names_come_from_meaningful_ids_only() -> None:
    assert part_name("LeftVentricle") == "left_ventricle"
    assert part_name("left-ventricle") == "left_ventricle"
    assert part_name("path123") is None and part_name("g45") is None
    assert part_name("center") is None  # a box anchor


# ---------------------------------------------------------------------- the stored drawing


def test_a_drawing_is_fitted_and_each_part_points_at_what_is_visible_of_it() -> None:
    got = parse_svg(
        """<svg xmlns="http://www.w3.org/2000/svg">
        <rect data-part="back" x="0" y="0" width="200" height="100" data-fill="blue"/>
        <rect data-part="front" x="0" y="0" width="150" height="100" data-fill="red"/>
        </svg>"""
    )
    art, found = with_anchors(to_art(got.shapes))
    assert max(art.w, art.h) == pytest.approx(400)
    # Only the back's right quarter shows: its anchor is there, not at its center.
    back = next(a for a in art.anchors if a.name == "back")
    assert back.x > 0.75 * art.w
    assert found["back"] is not None and found["back"].visible == pytest.approx(0.25, abs=0.06)


def test_a_part_hidden_under_others_fails_the_checks() -> None:
    got = parse_svg(
        """<svg xmlns="http://www.w3.org/2000/svg">
        <rect data-part="hidden" x="10" y="10" width="50" height="50" data-fill="blue"/>
        <rect data-part="cover" x="0" y="0" width="200" height="100" data-fill="red"/>
        <rect data-part="dot" x="150" y="40" width="20" height="20" data-fill="green"/>
        </svg>"""
    )
    art, found = with_anchors(to_art(got.shapes))
    o = SavedObject(
        id="covered", title="t", concept="t", aliases=["t"], art=art, parts={p: p for p in art.parts}
    )
    q = check(o, found)
    assert not next(c for c in q.checks if c.name == "parts_visible").ok


def test_a_saved_object_refuses_what_lessons_could_trip_on() -> None:
    art = Art(w=10, h=10, shapes=[Shape(d="M0 0 L10 10", stroke="red", part="line")])
    with pytest.raises(ValueError):
        Shape(d="M0 0", fill="rouge")
    with pytest.raises(ValueError):
        Shape(d="M0 0", part="top")  # a box anchor
    with pytest.raises(ValueError):
        SavedObject(id="x1", title="t", concept="t", aliases=[], art=art)
    with pytest.raises(ValueError):
        SavedObject(id="x1", title="t", concept="t", aliases=["t"], art=art, parts={"handle": "handle"})
    assert SavedObject(id="x1", title="t", concept="t", aliases=["t", "T"], art=art).aliases == ["t"]


def test_the_color_roles_are_the_engines() -> None:
    catalog = json.loads((REPO_ROOT / "contracts" / "catalog.json").read_text(encoding="utf-8"))
    assert list(COLOR_ROLES) == catalog["colorRoles"]


# ---------------------------------------------------------------------- the built-in library


def test_every_built_in_drawing_passes_every_check() -> None:
    assert len(LIB) >= 30
    for oid, b in BUILT.items():
        assert b.obj is not None and not b.problems, (oid, b.problems)
        assert b.obj.quality is not None and b.obj.quality.passed, oid
        assert set(b.obj.art.parts) <= set(b.obj.parts), oid  # every part has a label
        assert b.obj.context, oid  # every built-in drawing says what lessons it belongs to


def test_the_built_library_matches_its_sources() -> None:
    assert library.drift(BUILT) == []


# ---------------------------------------------------------------------- which drawings a scene gets


def offered(objects: list[str], body: list[str], lesson: list[str] | None = None) -> list[str]:
    scene = SceneText(objects, body, lesson or [])
    return [o.obj.id for o in offers_for(scene, list(LIB.values()))]


def test_a_scene_gets_the_drawings_its_brief_names_exactly() -> None:
    got = offered(
        ["heart: a large heart in the centre"], ["The heart pumps blood to the body."], ["Circulation"]
    )
    assert set(got[:2]) == {
        "heart_exterior",
        "heart_chambers",
    }  # both views; the model picks by what it shows
    chambers = offered(["heart: the heart cut open"], ["Blood fills the four chambers of the heart."])
    assert chambers[0] == "heart_chambers"  # the view the brief asks for first


@pytest.mark.parametrize(
    ("objects", "body", "lesson"),
    [
        (["heart: a red heart shape"], ["She draws a heart shape to show love."], ["Feelings"]),
        (["heart_rate_graph: a line graph"], ["The heart rate graph rises."], ["Exercise"]),
        (["train: a train"], ["The train leaves the station at noon."], ["Timetables"]),
        (["idea_bulb: a light bulb over her head"], ["She has a bright idea!"], ["Creativity"]),
        (["panel: a solar cell"], ["A solar cell turns light into electricity."], ["Energy"]),
        (["soil: brown earth"], ["The farmer digs the earth."], ["Farming"]),
        (["virus_alert: a computer virus warning"], ["A computer virus spreads by email."], ["Security"]),
        (["neural_net: a neural network"], ["Each artificial neuron adds its inputs."], ["Machine learning"]),
        (["atom: a sodium atom"], ["A sodium atom has eleven electrons."], ["Chemistry"]),
        (["flask: a round-bottom flask"], ["Heat the round-bottom flask gently."], ["Distillation"]),
    ],
)
def test_similar_is_never_enough(objects: list[str], body: list[str], lesson: list[str]) -> None:
    assert offered(objects, body, lesson) == []


def test_a_word_used_otherwise_offers_nothing_more() -> None:
    """Seen in a live heart lesson: "leaves" was a verb, and veins carry blood, not sap."""
    got = offered(
        ["heart_diagram: a four-chamber heart"], ["Blood leaves the heart through a vein."], ["The heart"]
    )
    assert "leaf" not in got and "heart_chambers" in got


def test_an_everyday_word_never_hides_a_drawing() -> None:
    """Seen live: "idea" on its own hid the bulb from a circuit scene about "the key idea"."""
    got = offered(
        ["bulb: the light bulb, now dark"], ["The key idea: a broken wire opens the circuit."], ["Circuits"]
    )
    assert got == ["light_bulb"]


def test_a_never_for_phrase_inside_the_drawings_own_name_does_not_hide_it() -> None:
    """Seen in review: "candle" as never-for on "red comparison candle" hid it wherever it was named."""
    base = obj("heart_exterior")
    candle = base.model_copy(
        update={
            "id": "red_candle_1",
            "origin": "harvest",
            "concept": "red comparison candle",
            "aliases": ["red comparison candle"],
            "context": ["stock", "price"],
            "not_for": ["candle", "green candle"],
        }
    )
    scene = SceneText(["chart: a red comparison candle"], ["The stock price fell."], [])
    assert [o.obj.id for o in offers_for(scene, [candle])] == ["red_candle_1"]
    lit = SceneText(["chart: a red comparison candle"], ["A wax candle burns.", "The stock price fell."], [])
    assert offers_for(lit, [candle]) == []  # a candle said on its own still means something else
    # and a drawing that is not built-in is never offered without context words
    bare = candle.model_copy(update={"context": []})
    assert offers_for(scene, [bare]) == []


def test_a_scene_comparing_two_things_gets_both() -> None:
    got = offered(
        ["plant_cell: a plant cell", "animal_cell: an animal cell"],
        ["Compare a plant cell with an animal cell."],
    )
    assert set(got) == {"plant_cell", "animal_cell"}


def test_the_longer_name_wins_and_plurals_count() -> None:
    assert offered(["cells: plant cells side by side"], ["Plant cells have walls."]) == ["plant_cell"]
    assert "chloroplast" in offered(["chloroplasts: chloroplasts glowing"], ["Photosynthesis in leaves."])
    assert forms("nuclei") >= {"nucleus"} and forms("leaves") >= {"leaf"}
    assert offered(["mito: mitochondria"], ["The cell makes energy in its mitochondria."]) == [
        "mitochondrion"
    ]


def test_the_offer_says_what_each_drawing_is_its_parts_and_sizes() -> None:
    scene = SceneText(["heart: the heart"], ["The heart pumps blood."], [])
    text = offer_text(offers_for(scene, [obj("heart_exterior")]))
    assert "`heart_exterior`" in text and "left_ventricle (left ventricle)" in text
    assert "The brief names it: `heart`" in text and "height = " in text
    assert "none match" in offer_text([])


# ---------------------------------------------------------------------- placing a drawing in a scene


def scene_with(*objects: dict[str, Any]) -> dict[str, Any]:
    return {"id": "s1", "objects": list(objects), "timeline": []}


def saved(oid: str = "heart", ref: str = "heart_exterior", **params: Any) -> dict[str, Any]:
    return {
        "id": oid,
        "type": "saved",
        "params": {"ref": ref, "width": None, "height": None, **params},
        "place": {"slot": "stage.center"},
        "style": {"fill": "red", "shadow": True},
        "metadata": None,
    }


def test_a_saved_reference_becomes_the_drawing_inline() -> None:
    heart = obj("heart_exterior")
    res = resolve(scene_with(saved(width=400)), {"heart_exterior": heart})
    assert res.ok and res.used == ["heart_exterior@1"]
    fig = res.dsl["objects"][0]
    assert fig["type"] == "figure" and fig["params"]["ref"] == "heart_exterior@1"
    assert fig["params"]["art"]["shapes"] == heart.art.engine()["shapes"]
    assert fig["params"]["width"] == 400
    assert fig["style"] == {"shadow": True}  # it has its own colors
    assert fig["metadata"]["concept"] == "human heart"
    assert placed_refs(res.dsl) == ["heart_exterior@1"]
    # A correction sees the short reference again, with the size it had.
    back = compact(res.dsl)["objects"][0]
    assert back["type"] == "saved" and back["params"] == {
        "ref": "heart_exterior",
        "width": 400,
        "height": None,
    }


def test_a_drawing_keeps_its_proportions_within_its_range_and_the_stage() -> None:
    heart = obj("heart_exterior")
    assert fit_width(heart) == heart.size.default
    assert fit_width(heart, 5000) == pytest.approx(min(heart.size.max, 620 / heart.aspect), abs=0.1)
    assert fit_width(heart, 10) == heart.size.min
    assert fit_width(heart, None, 400) == pytest.approx(400 / heart.aspect, abs=0.1)
    assert fit_width(heart, None, 100) == heart.size.min  # a height too small for its labels
    tube = obj("test_tube")  # tall and narrow: the stage's height decides
    assert fit_width(tube, 400) * tube.aspect <= 620 + 0.1


def test_a_drawing_appears_with_create_whatever_the_reply_says() -> None:
    scene = scene_with(saved())
    scene["timeline"] = [
        {"id": "a1", "action": "draw", "target": "heart", "start": {"at": 0}, "reverse": None},
        {"id": "a2", "action": "write", "target": "heart", "start": {"at": 1}, "terms": None, "by": "word"},
        {"id": "a3", "action": "draw", "target": "heart", "start": {"at": 3}, "reverse": True},
        {"id": "a4", "action": "draw", "target": "arrow_1", "start": {"at": 2}, "reverse": None},
    ]
    res = resolve(scene, {"heart_exterior": obj("heart_exterior")})
    assert [a["action"] for a in res.dsl["timeline"]] == ["create", "create", "remove", "draw"]
    assert "terms" not in res.dsl["timeline"][1] and "reverse" not in res.dsl["timeline"][2]
    assert res.adjusted == ["a1", "a2", "a3"]


def test_a_label_placed_by_a_part_goes_beside_the_drawing_pointing_at_it() -> None:
    label = {"id": "ra", "type": "label", "params": {"text": "Right atrium", "pointTo": None}}
    label["place"] = {"relativeTo": "heart.right_atrium", "side": "left", "gap": 30}
    badge = {
        "id": "b",
        "type": "badge",
        "params": {"text": "IN"},
        "place": {"relativeTo": "heart.aorta", "side": "above"},
    }
    other = {
        "id": "o",
        "type": "label",
        "params": {"text": "x", "pointTo": None},
        "place": {"relativeTo": "tank.top"},
    }
    res = resolve(scene_with(saved(), label, badge, other), {"heart_exterior": obj("heart_exterior")})
    ra, b, o = res.dsl["objects"][1:]
    assert ra["place"] == {"relativeTo": "heart", "side": "left", "gap": 30}
    assert ra["params"]["pointTo"] == "heart.right_atrium"
    assert b["place"]["relativeTo"] == "heart" and "pointTo" not in b["params"]
    assert o["place"]["relativeTo"] == "tank.top"  # not a drawing: the engine's to judge
    assert res.adjusted == ["ra.place", "b.place"]


def test_only_a_part_of_the_drawing_can_be_brought_into_focus() -> None:
    def focus(part: str) -> dict[str, Any]:
        scene = scene_with(saved())
        scene["timeline"] = [
            {"id": "a1", "action": "create", "target": "heart", "start": {"at": 0}},
            {
                "id": "a2",
                "action": "transform",
                "target": "heart",
                "start": {"at": 1},
                "changes": [{"param": "focus", "to": part}],
            },
        ]
        return scene

    offers = {"heart_exterior": obj("heart_exterior")}
    assert resolve(focus("left_ventricle"), offers).ok and resolve(focus(""), offers).ok
    bad = resolve(focus("left_chamber"), offers)
    assert [p["code"] for p in bad.problems] == ["SAVED_PART"]
    assert "left_ventricle" in bad.problems[0]["suggestion"]


def test_a_drawing_that_was_not_offered_is_an_error_never_a_substitute() -> None:
    res = resolve(scene_with(saved(ref="heart_love")), {"heart_exterior": obj("heart_exterior")})
    assert not res.ok and res.flagged == ["/objects/0"]
    assert res.problems[0]["code"] == "SAVED_NOT_OFFERED" and "`heart_exterior`" in res.problems[0]["message"]
    assert res.dsl["objects"][0]["type"] == "saved"  # left as written: the engine rejects it too


def test_the_model_schema_gains_saved_drawings_without_touching_the_original() -> None:
    schema = json.loads((REPO_ROOT / "contracts" / "dsl.llm.schema.json").read_text(encoding="utf-8"))
    before = copy.deepcopy(schema)
    extended = with_saved(schema)
    assert schema == before
    assert "Object_figure" not in schema["$defs"] and "Object_saved" not in schema["$defs"]
    assert {"$ref": "#/$defs/Object_saved"} in extended["$defs"]["Object"]["anyOf"]
    params = extended["$defs"]["Object_saved"]["properties"]["params"]
    assert params["required"] == ["ref", "width", "height"] and params["additionalProperties"] is False


async def test_the_engine_draws_a_placed_drawing_and_its_parts_are_anchors(pool: EngineWorkerPool) -> None:
    engine = EngineClient(pool)
    label = {
        "id": "lv",
        "type": "label",
        "params": {"text": "Left ventricle", "pointTo": "heart.left_ventricle"},
        "place": {"relativeTo": "heart", "side": "right", "gap": 40},
    }
    scene = {
        "dsl_version": "1.0",
        "id": "s1",
        "title": "Heart",
        "duration": 4,
        "narration": {"beats": [{"id": "b1", "text": "The heart."}]},
        "objects": [saved(width=340), label],
        "timeline": [
            {"id": "a1", "action": "create", "target": "heart", "start": {"at": 0}},
            {"id": "a2", "action": "create", "target": "lv", "start": {"at": 1}},
        ],
    }
    heart = obj("heart_exterior")
    res = resolve(scene, {"heart_exterior": heart})
    compiled = await engine.compile(res.dsl, None)
    assert compiled.ok, [i.message for i in compiled.issues if i.severity == "error"]
    frame = (await engine.sample(res.dsl, [3.0], None)).frames[0]
    shown = {o["id"]: o for o in frame["objects"]}
    box = shown["heart"]["bbox"]
    assert box["w"] == pytest.approx(340, abs=0.5) and box["h"] == pytest.approx(340 * heart.aspect, abs=0.5)
    # The leader line ends on the left ventricle as drawn (its stored point, on the canvas).
    lv = next(a for a in heart.art.anchors if a.name == "left_ventricle")
    k = 340 / heart.art.w
    end = shown["lv"]["params"]["pointTo"]
    assert end["x"] == pytest.approx(box["x"] + lv.x * k, abs=0.6)
    assert end["y"] == pytest.approx(box["y"] + lv.y * k, abs=0.6)
    # A part the drawing does not have is the engine's error.
    bad = copy.deepcopy(res.dsl)
    bad["objects"][1]["params"]["pointTo"] = "heart.handle"
    assert any(i.code == "UNKNOWN_ANCHOR" for i in (await engine.compile(bad, None)).issues)


# ---------------------------------------------------------------------- the store


def test_the_store_keeps_built_in_drawings_as_shipped_and_edits_beside_them(tmp_path: Path) -> None:
    store = ObjectStore(tmp_path)
    heart = store.get("heart_exterior")
    assert heart is not None and heart.status == "approved" and heart.origin == "library"
    store.save(heart.model_copy(update={"status": "retired", "aliases": ["heart", "cardiac pump"]}))
    again = store.get("heart_exterior")
    assert again is not None and again.status == "retired" and again.aliases == ["heart", "cardiac pump"]
    assert (tmp_path / "overrides" / "heart_exterior.json").exists()
    assert again.art == heart.art  # the drawing never changes
    assert "heart_exterior" not in {o.id for o in store.approved()}
    with pytest.raises(ValueError):
        store.delete("heart_exterior")
    store.reset("heart_exterior")
    reset = store.get("heart_exterior")
    assert reset is not None and reset.status == "approved"


def test_a_broken_file_never_stops_the_others(tmp_path: Path) -> None:
    (tmp_path / "warehouse").mkdir()
    (tmp_path / "warehouse" / "junk.json").write_text("{not json", encoding="utf-8")
    store = ObjectStore(tmp_path)
    assert store.get("heart_exterior") is not None
    assert any("junk.json" in e for e in store.errors)


def test_uses_count_each_scene_once(tmp_path: Path) -> None:
    store = ObjectStore(tmp_path)
    store.record_uses("g_1", "s01", ["heart_exterior@1", "lungs@1"])
    store.record_uses("g_1", "s01", ["heart_exterior@1"])  # the same scene again
    store.record_uses("g_2", "s03", ["heart_exterior@1"])
    assert store.use_counts() == {"heart_exterior": 2, "lungs": 1}


# ---------------------------------------------------------------------- cost-saver lessons


def heart_direction() -> dict[str, Any]:
    d = direction()
    for k, scene in enumerate(d["scenes"]):
        scene["objects_new"] = ["heart: the human heart, big in the centre", "lv_label: a label"]
        scene["visual_plan"] = f"the heart pumping blood ({k + 1})"
        scene["beats"] = ["the heart appears", "the left ventricle is labelled"]
    return d


def heart_scene(ref: str = "heart_exterior") -> dict[str, Any]:
    return {
        "dsl_version": "1.0",
        "id": "x",
        "title": "The heart",
        "duration": 8,
        "layout": "stage_with_caption",
        "narration": {
            "beats": [
                {"id": "b1", "text": "The **heart** pumps blood."},
                {"id": "b2", "text": "Its left side."},
            ]
        },
        "stage": {
            "direction": None,
            "groups": [
                {
                    "id": "main",
                    "region": None,
                    "arrange": "free",
                    "items": ["heart"],
                    "align": None,
                    "columns": None,
                    "connected": None,
                }
            ],
            "relations": [],
        },
        "objects": [
            {
                "id": "heart",
                "type": "saved",
                "params": {"ref": ref, "width": 340, "height": None},
                "place": {"slot": "stage.center"},
            },
            {
                "id": "lv_label",
                "type": "label",
                "params": {"text": "Left ventricle", "pointTo": "heart.left_ventricle"},
                "place": {"relativeTo": "heart", "side": "right", "gap": 40},
            },
        ],
        "timeline": [
            {"id": "a1", "action": "create", "target": "heart", "start": {"at": "beat:b1"}},
            {"id": "a2", "action": "create", "target": "lv_label", "start": {"at": "beat:b2"}},
        ],
    }


def heart_script(scene: Any = None) -> Teaching:
    script = Teaching(**{"visual direction": heart_direction()})
    script.scene = scene or (lambda i, a, r: heart_scene())
    return script


async def test_a_cost_saver_lesson_places_saved_drawings(pool: EngineWorkerPool) -> None:
    script = heart_script()
    gen = await run_lesson(pool, script, layout="costsaver")
    assert gen.status == "complete", gen.error
    first = gen.scenes[0]
    assert first.status == "ready" and first.dsl is not None
    heart = next(o for o in first.dsl["objects"] if o["id"] == "heart")
    assert heart["type"] == "figure" and heart["params"]["ref"] == "heart_exterior@1"
    assert heart["params"]["art"]["shapes"]  # the drawing travels with the lesson
    assert first.saved is not None and first.saved["used"] == ["heart_exterior@1"]
    assert "heart_exterior@1" in first.saved["offered"] and "heart_chambers@1" in first.saved["offered"]
    assert gen.metrics.saved_objects == len(gen.scenes)
    draft = next(r for r in script.calls if r.label == "scene 1 attempt 1")
    assert draft.prompt_version == "animation-costsaver.v2"
    assert "Object_saved" in draft.schema["$defs"] and "stage" in draft.schema["properties"]
    assert "## Saved drawings" in draft.instructions
    message = draft.messages[0].parts[1].text  # type: ignore[union-attr]
    assert "Saved drawings for this scene" in message and "`heart_exterior`" in message
    # The engine's record of the scene has no `saved` objects and no stage plan.
    assert not any(o["type"] == "saved" for o in first.dsl["objects"]) and "stage" not in first.dsl


async def test_a_drawing_the_scene_was_not_offered_goes_to_a_correction(pool: EngineWorkerPool) -> None:
    def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        if attempt == 1:
            return heart_scene(ref="heart_symbol")
        # The correction: the scene as the writer wrote it (short references, no art), fixed.
        previous = req.messages[0].parts[-2].text  # type: ignore[union-attr]
        assert '"type":"saved"' in previous and '"art"' not in previous
        assert "SAVED_NOT_OFFERED" in req.messages[0].parts[-1].text  # type: ignore[union-attr]
        fixed = heart_scene()["objects"][0]
        return {
            "objects": [fixed],
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

    script = heart_script(scene)
    gen = await run_lesson(pool, script, layout="costsaver")
    assert gen.status == "complete", gen.error
    rec = gen.scenes[0]
    assert rec.status == "ready" and rec.saved is not None and rec.saved["used"] == ["heart_exterior@1"]
    assert rec.attempts[0].errors[0]["code"] == "SAVED_NOT_OFFERED"


async def test_a_correction_sees_placed_drawings_as_short_references(pool: EngineWorkerPool) -> None:
    def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        if attempt == 1:
            broken = heart_scene()
            broken["objects"][1]["params"]["pointTo"] = "heart.handle"  # not a part of it
            return broken
        previous = req.messages[0].parts[-2].text  # type: ignore[union-attr]
        assert '"ref":"heart_exterior"' in previous and '"art"' not in previous
        return {
            "objects": [heart_scene()["objects"][1]],
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

    gen = await run_lesson(pool, heart_script(scene), layout="costsaver")
    assert gen.status == "complete", gen.error
    assert gen.scenes[0].status == "ready"


def heart_teacher() -> dict[str, Any]:
    script = teacher_script()
    script["title"] = "How the heart pumps blood"
    for sc in script["scenes"]:
        sc["beats"][0]["say"] = "The **heart** pumps blood around the body."
        sc["beats"][0]["show"] = "the heart beats in the centre"
    return script


async def test_a_cost_saver_lessons_director_hears_of_the_drawings(pool: EngineWorkerPool) -> None:
    for layout, told in (("costsaver", True), ("planned", False)):
        scene = heart_scene() if layout == "costsaver" else _drawn()
        script = heart_script(lambda i, a, r, scene=scene: copy.deepcopy(scene))
        script.overrides["lesson script"] = heart_teacher()
        script.overrides["script review"] = {"changes": [], "script": heart_teacher()}
        gen = await run_lesson(pool, script, layout=layout)
        assert gen.status == "complete", gen.error
        director = next(r for r in script.calls if r.label == "visual direction")
        text = director.messages[0].parts[0].text  # type: ignore[union-attr]
        assert ("Saved drawings this lesson can use" in text) is told, layout
        heart = next((line for line in text.splitlines() if line.startswith("- human heart: ")), "")
        assert ("outside, from the front" in heart and "cut open from the front" in heart) is told, layout


async def test_planned_and_classic_lessons_never_see_saved_drawings(pool: EngineWorkerPool) -> None:
    for layout in ("planned", "classic"):
        script = heart_script(lambda i, a, r: _drawn())
        gen = await run_lesson(pool, script, layout=layout)
        assert gen.status == "complete", gen.error
        draft = next(r for r in script.calls if r.label == "scene 1 attempt 1")
        assert "Object_saved" not in draft.schema["$defs"]
        assert "Saved drawings" not in draft.instructions and draft.prompt_version != "animation-costsaver.v2"
        assert all(s.saved is None for s in gen.scenes) and gen.metrics.saved_objects == 0


async def test_with_the_library_off_a_cost_saver_lesson_is_a_planned_one(pool: EngineWorkerPool) -> None:
    script = heart_script(lambda i, a, r: _drawn())
    gen = await run_lesson(pool, script, layout="costsaver", animator_objects_enabled=False)
    assert gen.status == "complete", gen.error
    draft = next(r for r in script.calls if r.label == "scene 1 attempt 1")
    assert draft.prompt_version == "animation-planned.v5" and "Object_saved" not in draft.schema["$defs"]


def _drawn() -> dict[str, Any]:
    """The heart scene drawn from shapes (what a planned or classic lesson writes)."""
    s = heart_scene()
    s["objects"][0] = {
        "id": "heart",
        "type": "ellipse",
        "params": {"rx": 120, "ry": 140},
        "place": {"slot": "stage.center"},
        "style": {"fill": "red"},
    }
    return s


# ---------------------------------------------------------------------- the warehouse's harvest


def drawn_scene() -> dict[str, Any]:
    """A scene whose writer drew a traffic light from shapes (a group of five), plus a chart."""
    return {
        "dsl_version": "1.0",
        "id": "s01",
        "title": "Signals",
        "duration": 6,
        "narration": {"beats": [{"id": "b1", "text": "Red means stop."}]},
        "objects": [
            {"id": "traffic_light", "type": "group", "place": {"x": 800, "y": 400}},
            {
                "id": "traffic_light_box",
                "type": "rectangle",
                "parent": "traffic_light",
                "params": {"width": 120, "height": 300, "cornerRadius": 20},
                "place": {"x": 800, "y": 400},
                "style": {"fill": "gray.dark"},
            },
            {
                "id": "traffic_light_red",
                "type": "circle",
                "parent": "traffic_light",
                "params": {"radius": 36},
                "place": {"x": 800, "y": 310},
                "style": {"fill": "red"},
            },
            {
                "id": "traffic_light_amber",
                "type": "circle",
                "parent": "traffic_light",
                "params": {"radius": 36},
                "place": {"x": 800, "y": 400},
                "style": {"fill": "yellow"},
            },
            {
                "id": "traffic_light_green",
                "type": "circle",
                "parent": "traffic_light",
                "params": {"radius": 36},
                "place": {"x": 800, "y": 490},
                "style": {"fill": "green"},
            },
            {
                "id": "sales_chart_a",
                "type": "rectangle",
                "params": {"width": 40, "height": 120},
                "place": {"x": 300, "y": 600},
            },
            {
                "id": "sales_chart_b",
                "type": "rectangle",
                "params": {"width": 40, "height": 160},
                "place": {"x": 360, "y": 580},
            },
            {
                "id": "sales_chart_c",
                "type": "rectangle",
                "params": {"width": 40, "height": 200},
                "place": {"x": 420, "y": 560},
            },
        ],
        "timeline": [
            {"id": "a1", "action": "create", "target": "traffic_light", "start": {"at": 0}},
            {"id": "a2", "action": "create", "target": "sales_chart_a", "start": {"at": 0}},
            {"id": "a3", "action": "create", "target": "sales_chart_b", "start": {"at": 0}},
            {"id": "a4", "action": "create", "target": "sales_chart_c", "start": {"at": 0}},
        ],
    }


def test_harvest_candidates_are_drawings_of_named_things_only() -> None:
    found = candidates(drawn_scene(), ["traffic_light: a traffic light", "sales_chart: a bar chart"])
    assert [c.key for c in found] == ["traffic_light"]  # a chart is not a thing
    assert found[0].members == [
        "traffic_light_box",
        "traffic_light_red",
        "traffic_light_amber",
        "traffic_light_green",
    ]
    assert candidates(drawn_scene(), []) == []  # nothing the brief names
    assert concept_of("grapes_cluster") == "grape cluster" and concept_of("nuclei") == "nucleus"


async def test_the_harvest_keeps_a_candidate_for_review_and_reads_each_lesson_once(
    pool: EngineWorkerPool, tmp_path: Path
) -> None:
    from animator.jobs.store import MemoryStore
    from animator.pipeline.models import Generation, GenerationRequest, SceneRecord, StoryboardScene
    from tests.test_pipeline import brief, settings

    b = brief("Signals")
    b["objects_new"] = ["traffic_light: a traffic light with red, amber and green lamps"]
    gen = Generation(request=GenerationRequest(topic="Road safety"), status="complete")
    gen.scenes.append(
        SceneRecord(
            index=0,
            id="s01",
            title="Signals",
            chapter_id="c1",
            brief=StoryboardScene.model_validate(b),
            status="ready",
            dsl=drawn_scene(),
        )
    )
    lessons = MemoryStore()
    await lessons.save(gen)
    store = ObjectStore(tmp_path)
    harvester = Harvester(store, EngineClient(pool), lessons, settings())
    report = await harvester.run(limit=5)
    assert report.lessons == 1 and len(report.added) == 1
    got = store.get(report.added[0])
    assert got is not None and got.status == "candidate" and got.origin == "harvest"
    assert got.concept == "traffic light" and set(got.art.parts) == {"box", "red", "amber", "green"}
    assert got.source.generation_id == gen.id and got.quality is not None and got.quality.passed
    assert {s.fill for s in got.art.shapes} == {"gray.dark", "red", "yellow", "green"}  # the theme's roles
    assert got not in store.approved()  # never used before an admin approves it
    again = await harvester.run(limit=5)
    assert again.lessons == 0 and again.added == []


# ---------------------------------------------------------------------- the admin API


def test_admins_review_import_and_try_saved_drawings(tmp_path: Path, worker_script: Path) -> None:
    from fastapi.testclient import TestClient

    from animator.api.app import create_app
    from animator.jobs.store import MemoryStore
    from animator.llm import FakeLLM
    from tests.test_pipeline import Script, settings
    from tests.test_platform import admin_client, csrf, platform_settings, register

    s = settings(animator_objects_dir=tmp_path / "objects", animator_objects_harvest=False)
    app = create_app(
        settings=s,
        llm=FakeLLM(Script()),
        store=MemoryStore(),
        resume_on_start=False,
        platform=platform_settings(tmp_path),
    )
    with TestClient(app) as c:
        register(c, "lena@example.org", "Lena")
        assert c.get("/api/v1/admin/objects").status_code == 403  # a learner
        a = admin_client(c)
        h = csrf(a)
        listed = a.get("/api/v1/admin/objects?view=library").json()
        assert listed["library"] >= 30 and listed["counts"]["approved"] >= 30
        assert all("art" in item for item in listed["items"])
        heart = a.get("/api/v1/admin/objects/heart_exterior").json()
        assert heart["approvable"] is True and "left_ventricle" in heart["anchors"]
        # Import an SVG: a candidate, checked; it needs context words before it can be approved.
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg"><rect data-part="body" width="120" height="300" '
            'rx="20" data-fill="gray.dark"/>'
            '<circle data-part="red_lamp" cx="60" cy="60" r="36" data-fill="red"/>'
            '<circle data-part="green_lamp" cx="60" cy="240" r="36" data-fill="green"/></svg>'
        )
        body = {
            "svg": svg,
            "title": "Traffic light",
            "concept": "traffic light",
            "aliases": ["traffic light"],
        }
        r = a.post("/api/v1/admin/objects/import", json=body, headers=h)
        assert r.status_code == 200, r.text
        new = r.json()
        assert new["status"] == "candidate" and new["approvable"] is False and "context" in new["why_not"]
        oid = new["id"]
        assert a.post("/api/v1/admin/objects/import", json=body, headers=h).status_code == 409
        assert (
            a.post(f"/api/v1/admin/objects/{oid}/review", json={"decision": "approve"}, headers=h).status_code
            == 422
        )
        r = a.patch(f"/api/v1/admin/objects/{oid}", json={"context": ["traffic", "road", "car"]}, headers=h)
        assert r.status_code == 200 and r.json()["approvable"] is True
        r = a.post(f"/api/v1/admin/objects/{oid}/review", json={"decision": "approve"}, headers=h)
        assert r.status_code == 200 and r.json()["status"] == "approved"
        brief = "light: a traffic light at the crossing" + chr(10) + "Cars stop at the red light."
        tried = a.post(
            "/api/v1/admin/objects/match", json={"brief": brief, "lesson": "Road safety"}, headers=h
        )
        assert [o["id"] for o in tried.json()["offered"]] == [oid]
        nerves = a.post("/api/v1/admin/objects/match", json={"brief": "A nerve sends a signal."}, headers=h)
        assert nerves.json()["offered"] == []
        # Built-in drawings are retired, never deleted; warehouse ones can go.
        assert a.delete("/api/v1/admin/objects/heart_exterior", headers=h).status_code == 422
        r = a.post("/api/v1/admin/objects/heart_exterior/review", json={"decision": "retire"}, headers=h)
        assert r.json()["status"] == "retired"
        assert a.post("/api/v1/admin/objects/heart_exterior/reset", headers=h).json()["status"] == "approved"
        assert a.delete(f"/api/v1/admin/objects/{oid}", headers=h).status_code == 200
        audit = [e["action"] for e in a.get("/api/v1/admin/audit").json()["items"]]
        assert {"objects.import", "objects.edit", "objects.approve", "objects.delete"} <= set(audit)
        assert c.get("/api/v1/public/info").json()["costsaver"] is True
        # A lesson can be made the cost-saver way.
        made = c.post(
            "/api/v1/lessons",
            json={"topic": "How the heart pumps blood", "layout": "costsaver"},
            headers=csrf(c),
        )
        assert made.status_code == 201, made.text


def test_admins_reject_many_drawings_waiting_for_review_at_once(tmp_path: Path, worker_script: Path) -> None:
    from fastapi.testclient import TestClient

    from animator.api.app import create_app
    from animator.jobs.store import MemoryStore
    from animator.llm import FakeLLM
    from tests.test_pipeline import Script, settings
    from tests.test_platform import admin_client, csrf, platform_settings, register

    s = settings(animator_objects_dir=tmp_path / "objects", animator_objects_harvest=False)
    app = create_app(
        settings=s,
        llm=FakeLLM(Script()),
        store=MemoryStore(),
        resume_on_start=False,
        platform=platform_settings(tmp_path),
    )
    shapes = [
        '<rect data-part="body" width="120" height="300" rx="20" data-fill="gray.dark"/>'
        '<circle data-part="lamp" cx="60" cy="60" r="36" data-fill="red"/>',
        '<rect data-part="box" width="200" height="140" rx="12" data-fill="blue"/>'
        '<rect data-part="lid" y="-30" width="200" height="30" rx="6" data-fill="blue.dark"/>',
    ]
    with TestClient(app) as c:
        a = admin_client(c)
        h = csrf(a)
        ids = []
        for i, inner in enumerate(shapes):
            body = {
                "svg": f'<svg xmlns="http://www.w3.org/2000/svg">{inner}</svg>',
                "title": f"Thing {i}",
                "concept": f"thing {i}",
            }
            r = a.post("/api/v1/admin/objects/import", json=body, headers=h)
            assert r.status_code == 200, r.text
            ids.append(r.json()["id"])
        waiting = a.get("/api/v1/admin/objects?view=candidate").json()["counts"]["candidate"]
        r = a.post(
            "/api/v1/admin/objects/review",
            json={
                "ids": [*ids, "heart_exterior", "no_such_thing", ids[0]],
                "decision": "reject",
                "note": "Not exact",
            },
            headers=h,
        )
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["changed"] == ids
        assert {x["id"]: x["reason"] for x in out["skipped"]} == {
            "heart_exterior": "A built-in drawing is retired, not rejected.",
            "no_such_thing": "No saved drawing with this id.",
        }
        after = a.get("/api/v1/admin/objects?view=rejected").json()
        assert after["counts"]["candidate"] == waiting - 2 and set(ids) <= {o["id"] for o in after["items"]}
        assert a.get(f"/api/v1/admin/objects/{ids[0]}").json()["note"] == "Not exact"
        again = a.post(
            "/api/v1/admin/objects/review", json={"ids": [ids[0]], "decision": "reject"}, headers=h
        )
        assert again.json()["skipped"] == [{"id": ids[0], "reason": "It is not waiting for review."}]
        assert (
            a.post(
                "/api/v1/admin/objects/review", json={"ids": [], "decision": "reject"}, headers=h
            ).status_code
            == 422
        )
        approve = a.post("/api/v1/admin/objects/review", json={"ids": ids, "decision": "approve"}, headers=h)
        assert approve.status_code == 422  # approving stays one at a time
        audit = a.get("/api/v1/admin/audit", params={"action": "objects.reject"}).json()["items"]
        assert sorted(e["target"] for e in audit) == sorted(ids) and all(e["detail"]["bulk"] for e in audit)
        register(c, "lena@example.org", "Lena")
        learner = c.post(
            "/api/v1/admin/objects/review", json={"ids": ids, "decision": "reject"}, headers=csrf(c)
        )
        assert learner.status_code == 403
