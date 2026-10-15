"""Measuring a scene for the planner (ADR-047): which objects make up each planned item, where each
item sits over its whole time on stage, and what moves or connects between items.

Footprints come from the engine itself: the scene is sampled with its motion frozen (every move
replaced by a pause of the same length), so an item's footprint is where it rests, while what it
does over time (appearing, growing, changing) is included. Travel between items is not part of any
footprint; it is mapped point by point to wherever the items end up.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from animator.pipeline.layout_plan.arrange import Group, Item
from animator.pipeline.layout_plan.plan import StagePlan
from animator.pipeline.layout_plan.regions import Box, content_regions, default_region

# Components that are text: never enlarged, shrunk only as far as they stay readable.
TEXT_TYPES = {
    "badge",
    "callout",
    "caption",
    "card",
    "equation",
    "label",
    "note",
    "readout",
    "speech_bubble",
    "text",
    "title_card",
}
# Components drawn from their endpoints (geometry in world coordinates).
WORLD_SPACE = {
    "arrow",
    "bracket",
    "dimension",
    "flow_particles",
    "line",
    "marker",
    "rope",
    "spring",
    "vector_arrow",
}
# Params holding a point (raw coordinates or an anchor reference).
POINT_PARAMS: dict[str, tuple[str, ...]] = {
    "arrow": ("from", "to"),
    "bracket": ("from", "to"),
    "dimension": ("from", "to"),
    "line": ("from", "to"),
    "rope": ("from", "to"),
    "spring": ("from", "to"),
    "vector_arrow": ("from",),
    "label": ("pointTo",),
    "speech_bubble": ("pointTo",),
    "callout": ("target",),
}
MOTION = {"move", "animate"}
SAMPLE_STEP = 0.2
MAX_SAMPLES = 240  # the engine's limit per request
CONTAINED = 0.8  # an unplanned object this much inside an item's drawing is part of it
SCALE_MAX = 1.6  # the most a drawing is enlarged
SCALE_MAX_TEXT = 1.35  # ... when it has text written on it
UNPLANNED_SHARE = 0.25  # more unplanned drawing than this (by area) and the plan does not describe the scene
SATELLITE_NEAR = 220.0  # an unplanned thing this close to an item keeps its place beside it
SHRINK_TEXT = 0.85  # the least an item with text is scaled
SHRINK_DRAWING = 0.6  # the least a drawing without text is scaled
# Text sizes of the theme (canvas px) and the smallest the engine accepts (22 px at 1080p), with a
# little room.
TEXT_SIZES = {"title": 60, "heading": 44, "body": 32, "label": 28, "caption": 30, "small": 24}
# Drawings with text of their own (a diagram's labels, ADR-061): scaled like a drawing, but no smaller
# than that text stays readable, and enlarged no more than text. The smallest text each diagram size
# draws (its notes and edge labels).
WITH_TEXT = {"diagram"}
DIAGRAM_TEXT = {"small": 20, "medium": 22, "large": 24}
MIN_TEXT = 22 * 900 / 1080 + 1


@dataclass
class Figure:
    """One planned item: its objects, the labels attached to it, and its measured boxes."""

    id: str
    members: set[str] = field(default_factory=set)
    attached: set[str] = field(default_factory=set)
    core: Box | None = None
    foot: Box | None = None
    t0: float = 0.0
    t1: float = 0.0
    scalable: bool = True
    smax: float = SCALE_MAX
    smin: float = 0.5

    def item(self) -> Item:
        assert self.core is not None and self.foot is not None
        return Item(self.id, self.core, self.foot, self.scalable, self.smax, self.smin)


@dataclass
class Measured:
    """A scene ready for the solver: figures, groups per region, and what everything else is."""

    layout: str
    figures: dict[str, Figure]
    groups: dict[str, list[Group]]  # region -> groups, in plan order
    direction: str
    member_of: dict[str, str]  # object id -> figure id
    attached_to: dict[str, str]
    fixed: set[str]
    home: dict[str, Box]
    unplanned: list[str]
    objects: dict[str, dict[str, Any]]


def _id(o: dict[str, Any]) -> str:
    return str(o.get("id"))


def fixed_objects(dsl: dict[str, Any]) -> set[str]:
    """Objects the planner never touches: the title and anything pinned to the screen or placed in
    the title or caption bar."""
    out = set()
    for o in dsl.get("objects") or []:
        place = o.get("place") or {}
        if (
            o.get("layer") == "overlay"
            or (isinstance(place, dict) and place.get("slot") in ("title_bar", "caption_bar"))
            or _id(o).startswith("title_s")
        ):
            out.add(_id(o))
    return out


def rigid_reason(dsl: dict[str, Any]) -> str | None:
    """Why a scene is left as written: its motion is computed (physics, bindings), groups are
    formed on the fly, or the camera is directed."""
    for a in dsl.get("timeline") or []:
        kind = a.get("action")
        if kind == "bind" and str(a.get("property")) in ("x", "y", "scale", "rotation"):
            return "an object's position is bound to a formula"
        if kind in ("run_solver", "group", "ungroup", "camera_move", "cut"):
            return f"the scene uses '{kind}'"
    if dsl.get("solvers"):
        return "the scene runs a physics solver"
    return None


def frozen(dsl: dict[str, Any], compiled: dict[str, Any] | None) -> dict[str, Any]:
    """The scene with every move replaced by a pause of the same length (same id and start, so
    nothing timed from it shifts): everything stays where it was placed."""
    times = (compiled or {}).get("actions") or {}
    out = copy.deepcopy(dsl)
    timeline = []
    for a in out.get("timeline") or []:
        kind = a.get("action")
        moving = kind == "move" or (kind == "animate" and str(a.get("property")) in ("x", "y", "scale"))
        if moving and a.get("id"):
            t = times.get(a["id"]) or {}
            span = max(0.0, float(t.get("end", 0)) - float(t.get("start", 0)))
            a = {"id": a["id"], "action": "wait", "start": a.get("start"), "duration": round(span, 4)}
        timeline.append(a)
    out["timeline"] = timeline
    return out


def sample_times(duration: float, compiled: dict[str, Any] | None) -> list[float]:
    """Times to sample a scene at: a steady grid plus just after every action ends, within the
    engine's limit per request."""
    ends = sorted(
        {round(float(t.get("end", 0)) + 0.02, 3) for t in ((compiled or {}).get("actions") or {}).values()}
    )
    ends = [t for t in ends if 0 < t < duration][: MAX_SAMPLES // 3]
    step = max(SAMPLE_STEP, duration / max(1, MAX_SAMPLES - len(ends)))
    grid = {round(k * step + 0.05, 3) for k in range(int(duration / step) + 1)}
    times = sorted({t for t in grid if 0 < t < duration} | set(ends))
    return times[:MAX_SAMPLES]


def boxes_over_time(frames: list[dict[str, Any]]) -> tuple[dict[str, Box], dict[str, tuple[float, float]]]:
    """Each object's box over the frames it is fully on stage, and when it is visible at all."""
    home: dict[str, Box] = {}
    life: dict[str, tuple[float, float]] = {}
    for frame in frames:
        t = float(frame.get("t", 0))
        for o in frame.get("objects") or []:
            if (o.get("alpha") or 0) <= 0.05 or o.get("type") == "group":
                continue
            oid = str(o.get("id"))
            t0, t1 = life.get(oid, (t, t))
            life[oid] = (min(t0, t), max(t1, t))
            b = o.get("bbox")
            if not isinstance(b, dict) or (o.get("presence") or 0) < 0.95:
                continue
            box = Box.of(b)
            if box.w <= 0 and box.h <= 0:
                continue
            home[oid] = home[oid].union(box) if oid in home else box
    return home, life


def _drawn_in(regions: dict[str, Box], figures: list[Figure], fallback: str) -> str:
    """The region a group was drawn in (the most of its items' drawing), when the plan names none."""
    if len(regions) < 2:
        return fallback
    share = {
        name: sum(r.overlap(f.core) for f in figures if f.core is not None) for name, r in regions.items()
    }
    best = max(share, key=lambda k: share[k])
    return best if share[best] > 0 else fallback


def text_px(objects: dict[str, dict[str, Any]], oid: str) -> float | None:
    """The size an object's text is drawn at on the canvas (its size role or number, times its own
    scale and its groups'), or None for an object without text."""
    o = objects.get(oid) or {}
    params = o.get("params") or {}
    if o.get("type") in WITH_TEXT:
        px = float(DIAGRAM_TEXT.get(str(params.get("size") or "medium"), DIAGRAM_TEXT["medium"]))
    elif o.get("type") not in TEXT_TYPES:
        return None
    else:
        # Without a size, the component may draw its smaller text at the smallest role: assume that.
        size = params.get("size", params.get("titleSize", "small"))
        px = (
            float(size)
            if isinstance(size, (int, float))
            else float(TEXT_SIZES.get(str(size), TEXT_SIZES["small"]))
        )
    seen: set[str] = set()
    while o and oid not in seen:
        seen.add(oid)
        scale = o.get("scale")
        px *= float(scale) if isinstance(scale, (int, float)) else 1.0
        oid = str(o.get("parent") or "")
        o = objects.get(oid) or {}
    return px


def _touched(o: dict[str, Any], figures: dict[str, Figure], home: dict[str, Box]) -> set[str]:
    """The items an object drawn from its endpoints touches with its raw ends."""
    out: set[str] = set()
    for key in POINT_PARAMS.get(str(o.get("type")), ()):
        p = (o.get("params") or {}).get(key)
        if isinstance(p, dict) and p.get("x") is not None and p.get("y") is not None:
            for fid, fig in figures.items():
                if fig.foot is not None and fig.foot.contains(float(p["x"]), float(p["y"]), 32.0):
                    out.add(fid)
    return out


def _nearest(
    box: Box, figures: dict[str, Figure], region_of: dict[str, str], regions: dict[str, Box]
) -> str | None:
    """The item nearest to a box, among the items of the region it lies in (all when none)."""
    region = next((n for n, r in regions.items() if r.contains(box.cx, box.cy)), None)
    pool = [f for f in figures.values() if f.foot is not None and region_of.get(f.id) == region]
    pool = pool or [f for f in figures.values() if f.foot is not None]
    best = min(pool, key=lambda f: f.foot.distance(box.cx, box.cy) if f.foot else 1e9, default=None)
    return best.id if best is not None else None


def _drawn_direction(groups: dict[str, list[Group]]) -> str:
    """How groups shown together were drawn relative to each other, when the plan does not say: side
    by side (row) when they were laid out more across than down, else stacked (column)."""
    across = down = 0.0
    for gs in groups.values():
        for i, a in enumerate(gs):
            for b in gs[i + 1 :]:
                if not a.coexists(b):
                    continue
                ua, ub = a.union().foot, b.union().foot
                across += abs(ua.cx - ub.cx)
                down += abs(ua.cy - ub.cy)
    return "row" if across > down else "column"


def _unplaced(fid: str, objects: dict[str, dict[str, Any]], member_of: dict[str, str]) -> bool:
    """An item whose objects were never given a place: it sits in the middle of the stage, wherever
    the scene meant it ("as drawn" means nothing for it)."""
    roots = [
        o
        for oid, o in objects.items()
        if member_of.get(oid) == fid and not o.get("parent") and o.get("type") not in WORLD_SPACE
    ]
    return bool(roots) and all(not o.get("place") for o in roots)


def _matches(oid: str, item: str) -> bool:
    return oid == item or oid.startswith(item + "_")


def _beside(o: dict[str, Any]) -> bool:
    """Text placed beside another object (a label below a box, a badge above it): it goes with that
    object at its own size, whatever its name. Text placed on or inside a shape is part of it."""
    place = o.get("place")
    if o.get("type") not in TEXT_TYPES or not isinstance(place, dict) or o.get("parent"):
        return False
    if place.get("relativeTo"):
        return not str(place.get("side", "")).startswith(("center", "inside"))
    return bool(place.get("at"))


def measure(
    dsl: dict[str, Any], plan: StagePlan, frames: list[dict[str, Any]]
) -> tuple[Measured | None, str]:
    """Figures and groups from the plan and the frozen scene's frames, or None and why not."""
    layout = str(dsl.get("layout") or "stage_with_caption")
    objects = {_id(o): o for o in dsl.get("objects") or []}
    fixed = fixed_objects(dsl)
    home, life = boxes_over_time(frames)
    items = [i for g in plan.groups for i in g.items]

    member_of: dict[str, str] = {}
    for oid, o in objects.items():
        if oid in fixed or _beside(o):
            continue
        best = max((i for i in items if _matches(oid, i)), key=len, default=None)
        if best is not None:
            member_of[oid] = best
    # A member's children belong to its item, whatever their names.
    changed = True
    while changed:
        changed = False
        for oid, o in objects.items():
            parent = o.get("parent")
            if oid in fixed or not parent or parent not in member_of:
                continue
            if member_of.get(oid) != member_of[parent]:
                member_of[oid] = member_of[parent]
                changed = True

    figures = {i: Figure(i) for i in dict.fromkeys(items)}
    for oid, fid in member_of.items():
        figures[fid].members.add(oid)

    def ref_of(o: dict[str, Any]) -> str | None:
        place = o.get("place")
        if not isinstance(place, dict):
            return None
        if place.get("relativeTo"):
            return str(place["relativeTo"])
        if isinstance(place.get("at"), str):
            return str(place["at"]).split(".")[0]
        return None

    # Labels and badges placed against an item's objects move with it (they keep their size).
    attached_to: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for oid, o in objects.items():
            if oid in fixed or oid in member_of or oid in attached_to:
                continue
            ref = ref_of(o)
            holder = member_of.get(ref or "") or attached_to.get(ref or "")
            if holder is not None:
                attached_to[oid] = holder
                changed = True
    for a in dsl.get("timeline") or []:
        if a.get("action") == "label" and a.get("target") in member_of:
            attached_to[str(a.get("labelId") or f"{a.get('id')}_label")] = member_of[str(a["target"])]
    for oid, fid in attached_to.items():
        figures[fid].attached.add(oid)

    def core_of(fig: Figure) -> Box | None:
        boxes = [home[m] for m in fig.members if m in home]
        if not boxes:
            return None
        out = boxes[0]
        for b in boxes[1:]:
            out = out.union(b)
        return out

    moving = {str(a.get("target")) for a in dsl.get("timeline") or [] if a.get("action") in MOTION}
    # Something drawn inside an item (a liquid in its glass, a word on its box) is part of it.
    cores = {fid: core_of(f) for fid, f in figures.items()}
    for oid, o in objects.items():
        if oid in fixed or oid in member_of or oid in attached_to or oid not in home:
            continue
        if o.get("type") in WORLD_SPACE or oid in moving or o.get("parent"):
            continue
        box = home[oid]
        inside = [
            (c.area, fid)
            for fid, c in cores.items()
            if c is not None and box.overlap(c) >= CONTAINED * max(1.0, box.area)
        ]
        if inside:
            fid = min(inside)[1]
            member_of[oid] = fid
            figures[fid].members.add(oid)

    for fid, fig in list(figures.items()):
        fig.core = core_of(fig)
        if fig.core is None:
            del figures[fid]  # an item the scene never shows
            continue
        foot = fig.core
        for a in fig.attached:
            if a in home:
                foot = foot.union(home[a])
        fig.foot = foot
        spans = [life[o] for o in fig.members | fig.attached if o in life]
        fig.t0 = min(s for s, _ in spans)
        fig.t1 = max(e for _, e in spans)
        kinds = {str(objects[m].get("type")) for m in fig.members if m in objects}
        drawn = kinds - TEXT_TYPES - {"group"}
        fig.scalable = bool(drawn)
        fig.smax = SCALE_MAX_TEXT if kinds & (TEXT_TYPES | WITH_TEXT) else SCALE_MAX
        # How far it may shrink: text keeps nearly its size (and stays readable); a drawing alone
        # may shrink more.
        sizes = [px for px in (text_px(objects, m) for m in fig.members) if px]
        smallest = min(sizes, default=None)
        fig.smin = SHRINK_DRAWING
        if smallest:
            fig.smin = min(fig.smax, max(SHRINK_TEXT, MIN_TEXT / smallest))
    if not figures:
        return None, "the plan names no item the scene shows"

    unplanned = [
        oid
        for oid in objects
        if oid not in fixed and oid not in member_of and oid not in attached_to and oid in home
    ]
    loose = sum(
        home[o].area
        for o in unplanned
        if objects[o].get("type") not in WORLD_SPACE | TEXT_TYPES and o not in moving
    )
    planned = sum(f.core.area for f in figures.values() if f.core is not None)
    if loose > UNPLANNED_SHARE * max(1.0, planned):
        return None, "much of the drawing is in no planned item"

    regions = content_regions(layout)
    fallback = default_region(layout)
    chosen: list[tuple[int, Any, list[str], str]] = []
    seen: set[str] = set()
    for n, g in enumerate(plan.groups):
        fids = [i for i in dict.fromkeys(g.items) if i in figures and i not in seen]
        if not fids:
            continue
        seen.update(fids)
        region = g.region if g.region in regions else _drawn_in(regions, [figures[f] for f in fids], fallback)
        chosen.append((n, g, fids, region))
    region_of = {f: region for _, _, fids, region in chosen for f in fids}

    # Things beside an item that the plan does not name (an arrow pointing at it, the number on that
    # arrow, a question mark next to it) stay beside it: room is kept for them in its footprint.
    # Links between two items and things that travel are not counted (they go where the items go).
    satellites: dict[str, str] = {}
    for oid in unplanned:
        o = objects[oid]
        if oid in moving or str(o.get("parent") or "") in objects:
            continue
        ends = _touched(o, figures, home)
        if len(ends) > 1:
            continue
        owner = next(iter(ends)) if ends else _nearest(home[oid], figures, region_of, regions)
        near = figures.get(owner or "")
        if (
            near is None
            or near.foot is None
            or near.foot.distance(home[oid].cx, home[oid].cy) > SATELLITE_NEAR
        ):
            continue
        satellites[oid] = near.id
        near.foot = near.foot.union(home[oid])
        t0, t1 = life.get(oid, (near.t0, near.t1))
        near.t0, near.t1 = min(near.t0, t0), max(near.t1, t1)

    groups: dict[str, list[Group]] = {}
    for n, g, fids, region in chosen:
        kind = g.arrange if len(fids) > 1 or g.arrange in ("free",) else "free"
        if kind == "free" and sum(1 for f in fids if _unplaced(f, objects, member_of)) >= 2:
            kind = "row"  # items the scene never placed (all in the middle): side by side, in plan order
        group = Group(
            id=f"{g.id or 'g'}_{n}",
            kind=kind,
            items=[figures[f].item() for f in fids],
            region=region,
            t0=min(figures[f].t0 for f in fids),
            t1=max(figures[f].t1 for f in fids),
            align=g.align or "center",
            columns=g.columns,
            connected=bool(g.connected),
        )
        groups.setdefault(region, []).append(group)
    return (
        Measured(
            layout=layout,
            figures=figures,
            groups=groups,
            direction=plan.direction or _drawn_direction(groups),
            member_of=member_of,
            attached_to=attached_to,
            fixed=fixed,
            home=home,
            unplanned=unplanned,
            objects=objects,
        ),
        "",
    )
