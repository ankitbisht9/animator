"""Relations between things (ADR-048): what sits on, in or along what, checked with the engine's own
geometry at every moment it should hold, and snapped right without a model call.

The scene writer names them in its stage plan ("car_1 on road", "ball inside basket", "truck along
route"); positions alone lose that intent (a car drawn near the road, not on it). The verifier samples
the scene, takes every object's outline as the engine draws it (layout_plan/geometry.py; a round thing
as its circle, whatever its turn) and measures each relation's error in pixels, and for a thing along a
road in degrees too (a car lying across its road). The snapper then moves the subject with all its
parts by exactly that much (its labels follow), tilts a thing standing on a slope to the slope, turns a
thing along a line to lie along it, snaps where moves end, and turns a move along a curved road or
surface into keyframes that follow it. The engine judges the result like every other change; what
cannot be snapped is reported precisely.

Relations:
- `on`: the subject rests on the object's top (a car on a road seen from the side, a book on a table, a
  ball on a ramp): its outline touches the surface, neither floating nor sunk; it tilts with a slope.
- `along`: the subject is on the object's line, within its width (a car in its lane of a road seen
  from above, a bead on a wire, a boat on a river, a truck on a route), lying along it; a move along it
  follows the line.
- `inside`: the subject lies within the object (a ball in a tank, beans in a jar).
- `under`: the subject's top touches the object's bottom (a lamp under a ceiling).
- `above` / `below`: the subject is wholly above (below) the object.
- `near`: the subject is within reach of the object (60 px).
`when`: "always" (whenever both are on stage, the default) or "end" (at the end of the scene: a ball
thrown into a basket ends inside it).

A subject named by a prefix that covers several separate things (`cars_blue`: the north and the south
car) stands for each of them. Vehicles drawn on or right beside a road the plan does not mention are
on (or along) it all the same (`implied`).
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any, Literal

from animator.pipeline.layout_plan.figures import TEXT_TYPES, WORLD_SPACE, fixed_objects
from animator.pipeline.layout_plan.geometry import (
    Point,
    Shape,
    apply,
    arms,
    between,
    edge_at,
    half_width,
    middle_line,
    nearest,
    offset_line,
    shape_of,
    side_of,
    stretch,
)
from animator.pipeline.layout_plan.plan import PlanRelation
from animator.pipeline.layout_plan.regions import Box, default_region, regions, slot_box

TOLERANCE = 1.5  # px: closer than this is right
NEAR = 60.0
CLEAR = 8.0  # above/below: at least this apart
TILT_FROM = 4.0  # degrees: a slope gentler than this is level
HEADING_FROM = 12.0  # degrees: a thing along a line turned further than this from it is turned to it
CROSSING_TURN = 46.0  # degrees: in a crossing (two roads at once) a thing may be turning between them
CORNER = 60.0  # px: the radius of a turn from one road into another
TURN_PX = 0.5  # a wrong heading weighs this many px per degree
LANE_MARGIN = 2.0  # px kept between a thing and the edge of the road it is along
FOLLOW_FROM = 6.0  # px: a straight move that strays this far from its line follows the line instead
FOLLOW_STEP = 36.0  # px between the keyframes of a followed path
ROUND = {"ball", "circle", "pulley"}  # turning them does not change their outline
ROUND_POINTS = 36
SINK_REPORT = 0.5  # a thing sunk into its surface by up to this share of its height may be meant
IMPLY_NEAR = 48.0  # px: a vehicle this close to a road (or closer than its own size) is on it
MOTION = {"move", "animate"}
# A thing appearing or leaving is not where (or the size) it will be: a pop overshoots its size, a road
# draws itself on. Relations are not judged then.
SETTLING = {"create", "show", "draw", "write", "remove", "hide"}
CONTACT = {"on", "along", "inside", "under"}  # touching or overlapping is the point of these
VEHICLES = {
    "ambulance", "bicycle", "bike", "bikes", "boat", "boats", "bus", "buses", "car", "cars", "cycle",
    "lorry", "motorbike", "scooter", "ship", "taxi", "tractor", "train", "tram", "truck", "trucks",
    "van", "vehicle", "vehicles",
}  # fmt: skip
VEHICLE_ICONS = {"bike", "car", "ship"}
ROADS = {
    "crossroad", "crossroads", "highway", "intersection", "junction", "lane", "lanes", "rail",
    "rails", "railway", "river", "road", "roads", "route", "street", "track",
}  # fmt: skip
_QUOTED = re.compile(r"'([A-Za-z0-9_]+)'")
_WORDS = re.compile(r"[a-z]+")


@dataclass
class Check:
    """One relation's error at one moment: how far (and which way) the subject must go, the slope it
    should stand at (`on` a sloped surface), and how far it must turn to lie along its line."""

    relation: PlanRelation
    t: float
    dx: float
    dy: float
    tilt: float | None = None
    note: str = ""
    excess: float = 0.0  # what no move can fix: the subject is bigger than the room it must fit
    turn: float = 0.0  # degrees (`along`): lying across its line
    heading: float | None = None  # the subject's turn on the canvas when measured
    height: float = 0.0  # the subject's height when measured

    @property
    def size(self) -> float:
        return math.hypot(self.dx, self.dy) + self.excess + abs(self.turn) * TURN_PX


@dataclass
class Verdict:
    """Every relation's worst error over the scene (none: all hold)."""

    worst: dict[int, Check] = field(default_factory=dict)  # relation index -> its worst check
    checked: dict[int, int] = field(default_factory=dict)  # relation index -> moments checked
    first: dict[int, Check] = field(default_factory=dict)  # relation index -> first wrong moment

    @property
    def total(self) -> float:
        return sum(c.size for c in self.worst.values())

    def broken(self, at_least: float = TOLERANCE) -> list[Check]:
        return [c for c in self.worst.values() if c.size > at_least]

    def only(self, n: int) -> Verdict:
        """This verdict for one relation alone (to snap one subject at a time)."""
        pick = {n} & set(self.worst)
        return Verdict(
            {k: self.worst[k] for k in pick},
            {k: self.checked.get(k, 0) for k in pick},
            {k: self.first[k] for k in pick & set(self.first)},
        )


def members_of(name: str, objects: dict[str, dict[str, Any]], fixed: set[str]) -> set[str]:
    """The objects a relation names: the object itself, or every part of the item with that prefix,
    and their children."""
    found = {oid for oid in objects if oid not in fixed and (oid == name or oid.startswith(name + "_"))}
    changed = True
    while changed:
        changed = False
        for oid, o in objects.items():
            if oid not in found and o.get("parent") in found:
                found.add(oid)
                changed = True
    return found


def _body(name: str, members: set[str], objects: dict[str, dict[str, Any]]) -> set[str]:
    """What is measured of a subject: its drawing, not the labels written beside it (they move with it
    but do not sit on the road)."""
    body = {m for m in members if m == name or objects.get(m, {}).get("type") not in TEXT_TYPES}
    return body or members


def _descendants(root: str, objects: dict[str, dict[str, Any]]) -> set[str]:
    found = {root}
    changed = True
    while changed:
        changed = False
        for oid, o in objects.items():
            if oid not in found and o.get("parent") in found:
                found.add(oid)
                changed = True
    return found


def _visible(o: dict[str, Any]) -> bool:
    return (o.get("alpha") or 0) > 0.05 and (o.get("presence") or 0) >= 0.95


def _matrix(o: dict[str, Any]) -> tuple[float, float, float, float, float, float] | None:
    m = o.get("matrix")
    if isinstance(m, list) and len(m) == 6:
        m0, m1, m2, m3, m4, m5 = (float(v) for v in m)
        return m0, m1, m2, m3, m4, m5
    return None


def outline(o: dict[str, Any]) -> list[Point]:
    """A frame object's outline (world points): a round thing as its circle whatever its turn (its
    turned box would be larger), anything else as the engine draws it."""
    m = _matrix(o)
    lb = o.get("localBBox")
    if o.get("type") in ROUND and m is not None and isinstance(lb, dict) and float(lb.get("w") or 0) > 0:
        x, y, w, h = float(lb["x"]), float(lb["y"]), float(lb["w"]), float(lb["h"])
        cx, cy = apply(m, (x + w / 2, y + h / 2))
        r = max(w, h) / 2 * math.hypot(m[0], m[1])
        return [
            (
                cx + r * math.cos(2 * math.pi * k / ROUND_POINTS),
                cy + r * math.sin(2 * math.pi * k / ROUND_POINTS),
            )
            for k in range(ROUND_POINTS)
        ]
    s = shape_of(o)
    return list(s.points) if s is not None else []


def _box_of(pts: list[Point]) -> Box:
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return Box(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _touch(a: Box, b: Box, gap: float = 4.0) -> bool:
    return a.x - gap <= b.right and b.x <= a.right + gap and a.y - gap <= b.bottom and b.y <= a.bottom + gap


def subject_outline(frame: dict[str, Any], members: set[str]) -> tuple[Box, list[Point]] | None:
    """The subject's box and outline points at a moment (None when it is not on stage): its body, the
    largest part and everything joined to it; a marker or an arrow drawn apart from it is not what
    rests on the road."""
    parts = [
        outline(o)
        for o in frame.get("objects") or []
        if str(o.get("id")) in members and o.get("type") != "group" and _visible(o)
    ]
    parts = [q for q in parts if q]
    if not parts:
        return None
    boxes = [_box_of(q) for q in parts]
    main = max(range(len(parts)), key=lambda i: (boxes[i].area, boxes[i].w + boxes[i].h))
    keep = {main}
    grew = True
    while grew:
        grew = False
        for i in range(len(parts)):
            if i not in keep and any(_touch(boxes[i], boxes[k]) for k in keep):
                keep.add(i)
                grew = True
    pts = [pt for i in sorted(keep) for pt in parts[i]]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    box = Box(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
    if box.w <= 0 and box.h <= 0:
        return None
    return box, pts


def subject_box(frame: dict[str, Any], members: set[str]) -> Box | None:
    got = subject_outline(frame, members)
    return got[0] if got is not None else None


def _parts(frame: dict[str, Any], name: str, members: set[str], split: bool = False) -> list[Shape]:
    """The object's outlines; with `split` (roads), a road drawn as one outline of several arms (a
    plus-shaped junction) as its arms."""
    objs = [
        o
        for o in frame.get("objects") or []
        if str(o.get("id")) in members
        and _visible(o)
        and o.get("type") != "group"
        and (o.get("type") not in TEXT_TYPES or str(o.get("id")) == name)  # its label is not it
    ]
    shapes = [s for s in (shape_of(o) for o in objs) if s is not None]
    if not split:
        return shapes
    shapes = [a for s in shapes for a in arms(s)]
    lines = sorted((s for s in shapes if not s.closed), key=_weight, reverse=True)[:6]
    track: Shape | None = None
    for i, a in enumerate(lines):
        for b in lines[i + 1 :]:
            mid = between(a, b, f"{name}#track")
            if mid is not None and (track is None or mid.width > track.width):
                track = mid
    # Two lines side by side (a track's rails, a road's two edges) are one way between them.
    return [s for s in shapes if s.closed] + [track] if track is not None else shapes


def _weight(s: Shape) -> float:
    return s.box.area if s.closed else s.box.w + s.box.h


def _substantial(shapes: list[Shape], name: str) -> list[Shape]:
    """The parts a thing can be on: not the markings painted on them."""
    closed = [s for s in shapes if s.closed and s.box.area > 0]
    pool = closed or shapes
    top = max(_weight(s) for s in pool)
    return [s for s in pool if _weight(s) >= 0.2 * top or s.id == name]


def _containing(shapes: list[Shape], c: Point) -> list[Shape]:
    return [s for s in shapes if s.closed and s.box.contains(*c)]


def _delta(heading: float, direction: float) -> float:
    """The shorter turn (degrees) from lying at `heading` to lying along `direction`."""
    return (direction - heading + 90.0) % 180.0 - 90.0


def object_shape(
    frame: dict[str, Any],
    name: str,
    members: set[str],
    near: Box | None = None,
    heading: float | None = None,
    split: bool = False,
) -> Shape | None:
    """The outline a relation is checked against: the named object's own outline, or (an item of
    several parts) the outline of its largest part. With `near` (on, along): the substantial part
    nearest to the subject (a car on one arm of a junction is on that arm); in a crossing (inside two
    roads at once), the one it lies along (`heading`). `split`: roads drawn as one outline of several
    arms count as their arms (`along`)."""
    shapes = _parts(frame, name, members, split)
    if not shapes:
        return None
    if near is None:
        own = next((s for s in shapes if s.id == name), None)
        return own if own is not None else max(shapes, key=_weight)
    big = _substantial(shapes, name)
    c = (near.cx, near.cy)
    crossing = _containing(big, c)
    if len(crossing) >= 2 and heading is not None:
        return min(crossing, key=lambda s: abs(_delta(heading, nearest(middle_line(s), c)[2])))

    def rank(s: Shape) -> tuple[float, float]:
        q, _, _ = nearest(middle_line(s), c)
        return round(s.box.distance(*c)), math.dist(q, c)

    return min(big, key=rank)


def surfaces(frame: dict[str, Any], name: str, members: set[str]) -> list[Shape]:
    """What a thing can rest on (or hang under): the tops of all the object's parts together (a wheelset
    on two rails, a tabletop over its legs), not the markings on them."""
    shapes = _parts(frame, name, members)
    return _substantial(shapes, name) if shapes else []


def _edge(shapes: list[Shape], x: float, top: bool) -> tuple[float, float] | None:
    hits = [h for h in (edge_at(s, x, top) for s in shapes) if h is not None]
    if not hits:
        return None
    return min(hits) if top else max(hits)


def in_crossing(frame: dict[str, Any], name: str, members: set[str], near: Box) -> bool:
    """Whether a thing is where two of the object's roads cross (the middle of a junction)."""
    shapes = _parts(frame, name, members, split=True)
    return bool(shapes) and len(_containing(_substantial(shapes, name), (near.cx, near.cy))) >= 2


def measure(
    rel: PlanRelation,
    s: Box,
    shape: Shape,
    t: float,
    *,
    points: list[Point] | None = None,
    heading: float | None = None,
    band: float | None = None,
    slack: float = HEADING_FROM,
    under: list[Shape] | None = None,
) -> Check:
    """The relation's error for a subject (its box, and its outline when known) against an object
    outline. `heading`: which way the subject lies (`along`), right within `slack` degrees; `band`:
    half the width of a line drawn thick (a road drawn as a wide stroke); `under` (on, under): all
    the surfaces it may rest on."""
    obox = shape.box
    kind = rel.relation
    pts = points or [(s.x, s.y), (s.right, s.y), (s.right, s.bottom), (s.x, s.bottom)]
    if kind in ("on", "under"):
        tops = under or [shape]
        span = tops[0].box
        for o in tops[1:]:
            span = span.union(o.box)
        lo, hi = span.x + min(s.w / 2, span.w / 2), span.right - min(s.w / 2, span.w / 2)
        cx2 = min(max(s.cx, lo), hi)
        dx = cx2 - s.cx
        gaps: list[float] = []
        for px, py in pts:
            hit = _edge(tops, px + dx, kind == "on")
            if hit is not None:
                gaps.append(hit[0] - py if kind == "on" else py - hit[0])
        centre = _edge(tops, cx2, kind == "on") or next(
            (h for h in (_edge(tops, px + dx, kind == "on") for px, _ in pts) if h is not None), None
        )
        if centre is None or not gaps:
            return Check(rel, t, dx, 0.0, note="beside it", height=s.h)
        if kind == "on":
            # The point of the outline nearest the surface touches it: a ball on a slope touches it
            # off its centre, a block's lowest corner, never floating and never sunk.
            slope = centre[1]
            return Check(rel, t, dx, min(gaps), slope if abs(slope) >= TILT_FROM else None, height=s.h)
        return Check(rel, t, dx, -min(gaps), height=s.h)
    if kind == "along":
        line = middle_line(shape)
        q, _, direction = nearest(line, (s.cx, s.cy))
        # Anywhere across the road's width is on it (a lane), not only its middle line.
        room = max(0.0, (half_width(shape) if band is None else band) - min(s.w, s.h) / 2 - LANE_MARGIN)
        ox, oy = s.cx - q[0], s.cy - q[1]
        off = math.hypot(ox, oy)
        k = 0.0 if off <= room else (off - room) / off
        return Check(rel, t, -ox * k, -oy * k, turn=_turn(heading, direction, slack), heading=heading)
    if kind == "inside":
        inner = obox.inset(min(4.0, obox.w / 4), min(4.0, obox.h / 4))
        dx = _into(s.x, s.right, inner.x, inner.right)
        dy = _into(s.y, s.bottom, inner.y, inner.bottom)
        excess = max(0.0, s.w - inner.w) + max(0.0, s.h - inner.h)
        return Check(rel, t, dx, dy, excess=excess)
    if kind == "above":
        return Check(rel, t, 0.0, min(0.0, (obox.y - CLEAR) - s.bottom))
    if kind == "below":
        return Check(rel, t, 0.0, max(0.0, (obox.bottom + CLEAR) - s.y))
    # near
    gx = max(obox.x - s.right, 0.0, s.x - obox.right)
    gy = max(obox.y - s.bottom, 0.0, s.y - obox.bottom)
    gap = math.hypot(gx, gy)
    if gap <= NEAR:
        return Check(rel, t, 0.0, 0.0)
    vx, vy = obox.cx - s.cx, obox.cy - s.cy
    d = math.hypot(vx, vy) or 1.0
    k = (gap - NEAR * 0.75) / d
    return Check(rel, t, vx * k, vy * k)


def _turn(heading: float | None, direction: float, slack: float = HEADING_FROM) -> float:
    """How far (degrees, the shorter way) a thing lying at `heading` must turn to lie along a line
    heading `direction`; 0 when it nearly does."""
    if heading is None:
        return 0.0
    delta = _delta(heading, direction)
    return delta if abs(delta) > slack else 0.0


def _wrap(a: float) -> float:
    """An angle in (-180, 180]."""
    a = (a + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


def _into(a: float, b: float, lo: float, hi: float) -> float:
    """How far to move the span a..b so it lies within lo..hi (centred when it is too long)."""
    if b - a > hi - lo:
        return (lo + hi) / 2 - (a + b) / 2
    if a < lo:
        return lo - a
    if b > hi:
        return hi - b
    return 0.0


def _find(frame: dict[str, Any] | None, oid: str) -> dict[str, Any] | None:
    return next((o for o in (frame or {}).get("objects") or [] if str(o.get("id")) == oid), None)


def _words(oid: str) -> set[str]:
    return set(_WORDS.findall(oid.lower()))


def heading_of(
    frame: dict[str, Any] | None, root: str, objects: dict[str, dict[str, Any]], s: Box
) -> float | None:
    """Which way a thing lies on the canvas (its turn, degrees), for things that turn to follow a line;
    None for those that do not: text, lines, round things, upright figures (a person, a bottle)."""
    o = objects.get(root) or {}
    kind = o.get("type")
    if kind in TEXT_TYPES or kind in WORLD_SPACE or kind in ROUND:
        return None
    if kind == "icon" and (o.get("params") or {}).get("name") not in VEHICLE_ICONS:
        return None  # icons are drawn upright (a person, a house); vehicles lie along their way
    fo = _find(frame, root)
    m = _matrix(fo) if fo is not None else None
    if m is None:
        return None
    rot = math.degrees(math.atan2(m[1], m[0]))
    lb = (fo or {}).get("localBBox")
    w = float(lb.get("w") or 0) if isinstance(lb, dict) else 0.0
    h = float(lb.get("h") or 0) if isinstance(lb, dict) else 0.0
    if w <= 0 or h <= 0:  # a group: judged by what it covers
        across = abs(math.sin(math.radians(rot))) > 0.7
        w, h = (s.h, s.w) if across else (s.w, s.h)
    if h > 1.25 * w:
        return None
    return rot


def moving_spans(
    dsl: dict[str, Any], compiled: dict[str, Any] | None, members: set[str]
) -> list[tuple[float, float]]:
    times = (compiled or {}).get("actions") or {}
    out = []
    for a in dsl.get("timeline") or []:
        if a.get("action") in MOTION and a.get("target") in members and a.get("id") in times:
            t = times[a["id"]]
            out.append((float(t.get("start", 0)), float(t.get("end", 0))))
    return out


def settling_spans(
    dsl: dict[str, Any], compiled: dict[str, Any] | None, members: set[str]
) -> list[tuple[float, float]]:
    """When any of these things is appearing or leaving."""
    times = (compiled or {}).get("actions") or {}
    out = []
    for a in dsl.get("timeline") or []:
        if a.get("action") in SETTLING and a.get("target") in members and a.get("id") in times:
            t = times[a["id"]]
            out.append((float(t.get("start", 0)) - 0.01, float(t.get("end", 0)) + 0.01))
    return out


def _within(t: float, spans: list[tuple[float, float]]) -> bool:
    return any(a <= t <= b for a, b in spans)


def _band(shape: Shape, objects: dict[str, dict[str, Any]]) -> float | None:
    """Half the width of a line drawn thick (a road as a wide stroke); None for a band (measured)."""
    if shape.closed:
        return None
    if shape.width > 0:
        return shape.width
    sw = ((objects.get(shape.id) or {}).get("style") or {}).get("strokeWidth")
    return float(sw) / 2 if isinstance(sw, (int, float)) else 0.0


def verify(
    dsl: dict[str, Any],
    relations: list[PlanRelation],
    frames: list[dict[str, Any]],
    compiled: dict[str, Any] | None,
) -> Verdict:
    """Each relation's error at every moment it should hold. `on` and `along` hold while the subject
    moves too (a car stays on the road); the others are checked when it is at rest."""
    objects = {str(o.get("id")): o for o in dsl.get("objects") or []}
    fixed = fixed_objects(dsl)
    verdict = Verdict()
    for n, rel in enumerate(relations):
        subj = members_of(rel.subject, objects, fixed)
        obj = members_of(rel.object, objects, fixed)
        if not subj or not obj or subj & obj:
            continue
        body = _body(rel.subject, subj, objects)
        roots = _roots(body, objects)
        spans = moving_spans(dsl, compiled, subj)
        settling = settling_spans(dsl, compiled, subj | obj)
        moments: list[tuple[float, Box, list[Point], Shape, float | None, float, list[Shape] | None]] = []
        for frame in frames:
            t = float(frame.get("t", 0))
            if rel.relation not in ("on", "along") and any(a - 0.01 <= t <= b + 0.01 for a, b in spans):
                continue
            if _within(t, settling):
                continue
            got = subject_outline(frame, body)
            if got is None:
                continue
            s, pts = got
            heading = (
                heading_of(frame, roots[0], objects, s)
                if rel.relation == "along" and len(roots) == 1
                else None
            )
            near = s if rel.relation in ("on", "along") else None
            shape = object_shape(frame, rel.object, obj, near, heading, split=rel.relation == "along")
            if shape is None:
                continue
            # In a crossing a car may be turning from one road into the other.
            slack = (
                CROSSING_TURN
                if heading is not None and in_crossing(frame, rel.object, obj, s)
                else HEADING_FROM
            )
            tops = surfaces(frame, rel.object, obj) if rel.relation in ("on", "under") else None
            moments.append((t, s, pts, shape, heading, slack, tops))
        if rel.when == "end" and moments:
            moments = moments[-1:]
        verdict.checked[n] = len(moments)
        for t, s, pts, shape, heading, slack, tops in moments:
            c = measure(
                rel,
                s,
                shape,
                t,
                points=pts,
                heading=heading,
                band=_band(shape, objects),
                slack=slack,
                under=tops,
            )
            if c.size > TOLERANCE and n not in verdict.first:
                verdict.first[n] = c
            if n not in verdict.worst or c.size > verdict.worst[n].size:
                verdict.worst[n] = c
    return verdict


def describe(c: Check) -> str:
    """A broken relation, said precisely (for a correction round and the reviewer)."""
    r = c.relation
    where = []
    if abs(c.dy) > TOLERANCE:
        where.append(f"{abs(c.dy):.0f} px {'down' if c.dy > 0 else 'up'}")
    if abs(c.dx) > TOLERANCE:
        where.append(f"{abs(c.dx):.0f} px {'right' if c.dx > 0 else 'left'}")
    head = f"'{r.subject}' should be {r.relation} '{r.object}' but is not at {c.t:.1f}s: "
    if c.excess > TOLERANCE:
        return head + f"it is {c.excess:.0f} px too big to fit (make it smaller or the other larger)."
    if c.turn and not where:
        return head + f"it lies {abs(c.turn):.0f} degrees across it (turn it to lie along it)."
    moves = " and ".join(where) or "a little"
    turn = f", and turn {abs(c.turn):.0f} degrees to lie along it" if c.turn else ""
    return head + f"it needs to go {moves}{turn}."


def worth_reporting(c: Check) -> bool:
    """Whether a relation left broken is plainly wrong to a viewer (for the reviewer). A thing resting
    partly in its surface may be meant: a boat in the water, a wheel's flange beside its rail, feet in
    the grass; it is snapped when that helps, but not sent back unless it is mostly sunk."""
    if c.relation.relation == "on" and c.dy < 0 and abs(c.dx) <= TOLERANCE and not c.turn:
        return -c.dy > SINK_REPORT * c.height
    return True


def expand(
    dsl: dict[str, Any], relations: list[PlanRelation], frames: list[dict[str, Any]]
) -> list[PlanRelation]:
    """Relations whose subject names several separate things (`cars_blue` for the north and the south
    car) stand for one relation per thing: each car sits on the road, not the box around them all.
    Parts of one drawing (they touch) stay one subject."""
    objects = {str(o.get("id")): o for o in dsl.get("objects") or []}
    fixed = fixed_objects(dsl)
    out: list[PlanRelation] = []
    for rel in relations:
        body = _body(rel.subject, members_of(rel.subject, objects, fixed), objects)
        roots = sorted(_roots(body, objects))
        if len(roots) < 2:
            out.append(rel)
            continue
        boxes: dict[str, Box] = {}
        for r in roots:
            parts = _descendants(r, objects) & body
            for f in frames:
                b = subject_box(f, parts)
                if b is not None:
                    boxes[r] = b
                    break
        apart = len(boxes) == len(roots) and all(
            a.inset(-4, -4).overlap(b) == 0 for a, b in _pairs(list(boxes.values()))
        )
        if apart:
            out.extend(rel.model_copy(update={"subject": r}) for r in roots)
        else:
            out.append(rel)
    return out


def _pairs(items: list[Box]) -> list[tuple[Box, Box]]:
    return [(a, b) for i, a in enumerate(items) for b in items[i + 1 :]]


def _is_vehicle(o: dict[str, Any]) -> bool:
    kind = o.get("type")
    if kind in TEXT_TYPES or kind in WORLD_SPACE:
        return False
    if kind == "icon":
        return (o.get("params") or {}).get("name") in VEHICLE_ICONS
    return bool(_words(str(o.get("id"))) & VEHICLES)


def _is_road(o: dict[str, Any]) -> bool:
    kind = o.get("type")
    return kind not in TEXT_TYPES and kind != "icon" and bool(_words(str(o.get("id"))) & ROADS)


def implied(
    dsl: dict[str, Any], relations: list[PlanRelation], frames: list[dict[str, Any]]
) -> list[PlanRelation]:
    """Vehicles drawn on or right beside a road (a rail, a river, a route) that the plan does not
    place: on it seen from the side (cars above a level road), along it seen from above or on a map.
    Only when the vehicle never leaves it: a car driving off to the garage is not implied anywhere."""
    objects = {str(o.get("id")): o for o in dsl.get("objects") or []}
    fixed = fixed_objects(dsl)
    named = [r.subject for r in relations]

    def placed(oid: str) -> bool:
        return any(oid == n or oid.startswith(n + "_") for n in named)

    cands = {oid for oid, o in objects.items() if oid not in fixed and _is_vehicle(o)}
    vehicles = [
        oid
        for oid in sorted(cands)
        if not placed(oid)
        and objects[oid].get("parent") not in cands
        and not any(oid != v and oid.startswith(v + "_") for v in cands)
    ]
    road_ids = {oid for oid, o in objects.items() if oid not in fixed and _is_road(o)}
    roads = [
        oid
        for oid in sorted(road_ids)
        if objects[oid].get("parent") not in road_ids
        and not any(oid != r and oid.startswith(r + "_") for r in road_ids)
    ]
    if not vehicles or not roads:
        return []
    found: list[tuple[str, str, Box, Shape]] = []  # vehicle, road, and how they first meet
    for v in vehicles:
        subj = members_of(v, objects, fixed)
        body = _body(v, subj, objects)
        best: tuple[float, str, Box, Shape] | None = None
        for r in roads:
            obj = members_of(r, objects, fixed)
            if subj & obj:
                continue
            far = 0.0
            first: tuple[Box, Shape] | None = None
            for f in frames:
                s = subject_box(f, body)
                shape = object_shape(f, r, obj, s, split=True) if s is not None else None
                if s is None or shape is None:
                    continue
                if math.hypot(s.w, s.h) < 1:
                    continue
                far = max(far, shape.box.distance(s.cx, s.cy) / max(IMPLY_NEAR, 0.75 * max(s.w, s.h)))
                first = first or (s, shape)
            if first is not None and far <= 1.0 and (best is None or far < best[0]):
                best = (far, r, *first)
        if best is not None:
            found.append((v, best[1], best[2], best[3]))
    # A scene seen from above has a vehicle inside a road; from the side they stand above it.
    above_view = any(shape.closed and shape.box.contains(s.cx, s.cy) for _, _, s, shape in found)
    out: list[PlanRelation] = []
    for v, r, s, shape in found:
        b = shape.box
        kind: Literal["on", "along"]
        if not shape.closed:
            # A line: a route on a map (centred on it), or the ground in a side view (above it).
            hit = edge_at(shape, s.cx, top=True)
            kind = "on" if hit is not None and b.w >= b.h and hit[0] - s.cy > 0.25 * s.h else "along"
        elif b.contains(s.cx, s.cy) or above_view or b.h > b.w:
            kind = "along"
        elif b.w >= 2 * b.h and s.cy < b.y:
            kind = "on"
        else:
            continue
        out.append(PlanRelation(subject=v, relation=kind, object=r, when=None))
    return out


# ---------------------------------------------------------------- snapping


@dataclass
class _Frames:
    """Per-object facts at a moment: box, origin (where its place puts it) and visibility."""

    by_id: dict[str, dict[str, Any]]

    def origin(self, oid: str) -> Point | None:
        o = self.by_id.get(oid)
        m = (o or {}).get("matrix")
        return (float(m[4]), float(m[5])) if isinstance(m, list) and len(m) == 6 else None

    def box(self, oid: str) -> Box | None:
        b = (self.by_id.get(oid) or {}).get("bbox")
        return Box.of(b) if isinstance(b, dict) else None

    def scale(self, oid: str) -> float | None:
        m = (self.by_id.get(oid) or {}).get("matrix")
        return math.hypot(float(m[0]), float(m[1])) if isinstance(m, list) and len(m) == 6 else None


def _frame_at(frames: list[dict[str, Any]], t: float) -> dict[str, Any] | None:
    return min(frames, key=lambda f: abs(float(f.get("t", 0)) - t), default=None)


def _near(frames: list[dict[str, Any]], t: float) -> list[dict[str, Any]]:
    """The frames nearest to `t` first."""
    return sorted(frames, key=lambda f: abs(float(f.get("t", 0)) - t))


def _shape_near(
    frames: list[dict[str, Any]],
    t: float,
    name: str,
    members: set[str],
    near: Box | None = None,
    settling: list[tuple[float, float]] | None = None,
    split: bool = False,
) -> Shape | None:
    """The object's outline at the moment nearest to `t` it is fully drawn (a road still drawing itself
    on is where it will be), settled when it ever is."""
    for skip in (settling or [], []):
        for f in _near(frames, t):
            if _within(float(f.get("t", 0)), skip):
                continue
            shape = object_shape(f, name, members, near, split=split)
            if shape is not None:
                return shape
    return None


def _surfaces_near(
    frames: list[dict[str, Any]],
    t: float,
    name: str,
    members: set[str],
    settling: list[tuple[float, float]] | None = None,
) -> list[Shape]:
    """What a thing rests on at the moment nearest to `t` it is fully drawn, settled when it ever is."""
    for skip in (settling or [], []):
        for f in _near(frames, t):
            if _within(float(f.get("t", 0)), skip):
                continue
            tops = surfaces(f, name, members)
            if tops:
                return tops
    return []


def _seen_near(
    frames: list[dict[str, Any]],
    t: float,
    members: set[str],
    before: bool,
    settling: list[tuple[float, float]] | None = None,
    settled_only: bool = False,
) -> tuple[Box, list[Point], dict[str, Any]] | None:
    """The subject at the moment nearest to `t` (on or before it, or on or after it) it is seen: settled
    (not appearing or leaving) when it ever is there, else as it is."""
    for skip in (settling or [],) if settled_only else (settling or [], []):
        for f in _near(frames, t):
            ft = float(f.get("t", 0))
            if (before and ft > t + 0.045) or (not before and ft < t - 0.03) or _within(ft, skip):
                continue
            got = subject_outline(f, members)
            if got is not None:
                return got[0], got[1], f
    return None


def _start_seen(
    frames: list[dict[str, Any]], t: float, members: set[str], settling: list[tuple[float, float]]
) -> tuple[Box, list[Point], dict[str, Any]] | None:
    """Where a subject is as a move starts: settled before it; else (still popping in as it sets off)
    where it is then, at the size it settles to."""
    got = _seen_near(frames, t - 0.02, members, True, settling, settled_only=True)
    if got is not None:
        return got
    got = _seen_near(frames, t - 0.02, members, True)
    later = _seen_near(frames, t, members, False, settling, settled_only=True)
    if got is None or later is None:
        return got or later
    pick = sorted(members)
    now, then = _Frames(_by_id(got[2])), _Frames(_by_id(later[2]))
    for oid in pick:
        k0, k1, origin = now.scale(oid), then.scale(oid), now.origin(oid)
        if k0 and k1 and origin is not None:
            k = k1 / k0
            if abs(k - 1) < 0.005:
                return got
            ox, oy = origin
            pts = [(ox + (x - ox) * k, oy + (y - oy) * k) for x, y in got[1]]
            xs, ys = [q[0] for q in pts], [q[1] for q in pts]
            return Box(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)), pts, got[2]
    return got


def _by_id(frame: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {str(o.get("id")): o for o in (frame or {}).get("objects") or []}


def _roots(members: set[str], objects: dict[str, dict[str, Any]]) -> list[str]:
    return [m for m in members if objects.get(m, {}).get("parent") not in members]


def _point(layout: str | None, place: Any) -> Point:
    if isinstance(place, dict) and place.get("x") is not None and place.get("y") is not None:
        return float(place["x"]), float(place["y"])
    if isinstance(place, dict) and place.get("slot"):
        b = slot_box(layout, str(place["slot"]))
        off = place.get("offset") or {}
        return b.cx + float(off.get("x") or 0), b.cy + float(off.get("y") or 0)
    b = regions(layout)[default_region(layout)]
    return b.cx, b.cy


def shift(
    dsl: dict[str, Any],
    members: set[str],
    dx: float,
    dy: float,
    frames: _Frames | None = None,
    motion: bool = True,
) -> None:
    """Move a subject and all its parts by (dx, dy), in place: places, endpoints and (with `motion`) where
    its moves go. Things placed against one of its parts follow by themselves."""
    layout = dsl.get("layout")
    objects = {str(o.get("id")): o for o in dsl.get("objects") or []}
    for oid in members:
        o = objects.get(oid)
        if o is None:
            continue
        child = o.get("parent") in objects
        place = o.get("place")
        if o.get("type") in WORLD_SPACE:
            if child:
                continue  # drawn in its parent's frame: moves with it
            params = o.get("params") or {}
            for key in ("from", "to"):
                p = params.get(key)
                if isinstance(p, dict) and p.get("x") is not None:
                    params[key] = {"x": round(float(p["x"]) + dx, 1), "y": round(float(p["y"]) + dy, 1)}
            continue
        if isinstance(place, dict) and (place.get("relativeTo") or place.get("at")):
            ref = place.get("relativeTo") or str(place.get("at")).split(".")[0]
            if ref in members:
                continue  # placed against a part that moves: follows it
            if place.get("at"):
                off = place.get("offset") or {}
                place["offset"] = {
                    "x": round(float(off.get("x") or 0) + dx, 1),
                    "y": round(float(off.get("y") or 0) + dy, 1),
                }
                continue
            # Against something else: pinned where it is now, moved by the correction.
            b = frames.box(oid) if frames else None
            if b is None:
                continue
            o["place"] = {"x": round(b.cx + dx, 1), "y": round(b.cy + dy, 1)}
            continue
        x, y = _point(layout, place)
        anchor = place.get("anchor") if isinstance(place, dict) else None
        o["place"] = {"x": round(x + dx, 1), "y": round(y + dy, 1), **({"anchor": anchor} if anchor else {})}
    if not motion:
        return
    for a in dsl.get("timeline") or []:
        if a.get("target") not in members:
            continue
        child = objects.get(str(a.get("target")), {}).get("parent") in objects
        to = a.get("to")
        if a.get("action") == "move" and isinstance(to, dict) and to.get("x") is not None:
            to["x"], to["y"] = round(float(to["x"]) + dx, 1), round(float(to["y"]) + dy, 1)
        elif a.get("action") == "animate" and not child and a.get("property") in ("x", "y"):
            d = dx if a["property"] == "x" else dy
            for kf in a.get("keyframes") or []:
                if isinstance(kf.get("value"), (int, float)):
                    kf["value"] = round(float(kf["value"]) + d, 1)


def _follow(
    dsl: dict[str, Any],
    action: dict[str, Any],
    root: str,
    path: list[Point],
    offset: Point,
    tilts: list[float] | None,
    duration: float,
    heading: float | None = None,
) -> bool:
    """Replace a straight move by keyframes that follow `path` (the subject's centre on it), at an even
    speed; with `tilts`, the subject turns with the surface; with `heading`, it turns to keep lying
    along its way (its rotation is `heading` plus the way's direction). True when replaced."""
    if len(path) < 2:
        return False
    total = sum(math.dist(a, b) for a, b in pairwise(path))
    if total < 1:
        return False
    # Resample evenly.
    pts: list[Point] = [path[0]]
    run = 0.0
    want = FOLLOW_STEP
    for a, b in pairwise(path):
        seg = math.dist(a, b)
        while seg > 0 and run + seg >= want:
            t = (want - run) / seg
            pts.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
            want += FOLLOW_STEP
        run += seg
    if math.dist(pts[-1], path[-1]) > 1:
        pts.append(path[-1])
    pts = pts[:40] if len(pts) <= 40 else [pts[round(i * (len(pts) - 1) / 39)] for i in range(40)]
    duration = max(0.1, duration)
    lengths = [0.0]
    for a, b in pairwise(pts):
        lengths.append(lengths[-1] + math.dist(a, b))
    times = [round(duration * s / lengths[-1], 3) for s in lengths]
    base = {k: v for k, v in action.items() if k in ("id", "start", "duration")}
    base["duration"] = duration
    keys_x = [
        {"time": t, "value": round(p[0] + offset[0], 1), "easing": "linear"}
        for t, p in zip(times, pts, strict=True)
    ]
    keys_y = [
        {"time": t, "value": round(p[1] + offset[1], 1), "easing": "linear"}
        for t, p in zip(times, pts, strict=True)
    ]
    action.clear()
    action.update({**base, "action": "animate", "target": root, "property": "x", "keyframes": keys_x})
    follow_y = {
        "id": f"{base.get('id')}_y",
        "action": "animate",
        "target": root,
        "property": "y",
        "keyframes": keys_y,
        "start": {"with": base.get("id")},
    }
    timeline = dsl["timeline"]
    timeline.insert(timeline.index(action) + 1, follow_y)
    turns: list[float] | None = None
    if tilts:
        step = max(1, len(tilts) // len(pts))
        turns = [tilts[min(i * step, len(tilts) - 1)] for i in range(len(pts))]
    elif heading is not None:
        dirs = [math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) for a, b in pairwise(pts)]
        dirs.append(dirs[-1])
        for i in range(1, len(dirs)):  # no spin the long way round
            dirs[i] = dirs[i - 1] + _wrap(dirs[i] - dirs[i - 1])
        turns = [heading + d for d in dirs]
        obj = next((o for o in dsl.get("objects") or [] if o.get("id") == root), None)
        if obj is not None:
            obj["rotation"] = round(turns[0], 1)  # already lying along its way when it sets off
    if turns:
        turn = [
            {"time": t, "value": round(v, 1), "easing": "linear"} for t, v in zip(times, turns, strict=True)
        ]
        timeline.insert(
            timeline.index(follow_y) + 1,
            {
                "id": f"{base.get('id')}_turn",
                "action": "animate",
                "target": root,
                "property": "rotation",
                "keyframes": turn,
                "start": {"with": base.get("id")},
            },
        )
    return True


def _turns_itself(dsl: dict[str, Any], root: str) -> bool:
    """Whether the scene turns the thing itself (a wheel rolling, a car the writer steers)."""
    return any(
        a.get("target") == root
        and (
            a.get("action") == "rotate" or (a.get("action") == "animate" and a.get("property") == "rotation")
        )
        for a in dsl.get("timeline") or []
    )


def _lie_along(own: float, heading: float, turn: float) -> float:
    """The rotation that turns a thing at rest by `turn` to lie along its line, the shorter way; when
    both ways are as short (it lies straight across), the way that keeps it upright."""
    if abs(abs(turn) - 90) < 1:
        other = turn - 180 if turn > 0 else turn + 180
        if math.cos(math.radians(heading + other)) > math.cos(math.radians(heading + turn)) + 1e-6:
            turn = other
    return round(own + turn, 1)


def _facing(heading: float, direction: float) -> float:
    """0 when a thing lying at `heading` faces the way `direction` (or across it), 180 when it faces back
    (a truck drawn facing left, driving left)."""
    return 180.0 if math.cos(math.radians(heading - direction)) < -0.1 else 0.0


def snap(
    dsl: dict[str, Any],
    relations: list[PlanRelation],
    verdict: Verdict,
    frames: list[dict[str, Any]],
    compiled: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """The scene with its broken relations snapped right, and what was done. A subject at rest moves by
    its correction (tilts to a slope it stands on, turns to lie along its line); a moving subject's
    start and the ends of its moves are snapped, and a move along a curved line or surface follows it."""
    out = copy.deepcopy(dsl)
    objects = {str(o.get("id")): o for o in out.get("objects") or []}
    fixed = fixed_objects(out)
    times = (compiled or {}).get("actions") or {}
    done: list[str] = []
    moved: set[str] = set()
    for n, rel in enumerate(relations):
        first = verdict.first.get(n)
        if first is None:
            continue
        subj = members_of(rel.subject, objects, fixed)
        obj = members_of(rel.object, objects, fixed)
        if not subj or subj & moved:
            continue  # one correction per subject per pass
        body = _body(rel.subject, subj, objects)
        roots = _roots(body, objects)
        root = roots[0] if len(roots) == 1 else None
        motions = [
            a
            for a in out.get("timeline") or []
            if a.get("action") == "move" and a.get("target") in _roots(subj, objects) and a.get("id") in times
        ]
        steer = root is not None and rel.relation == "along" and not _turns_itself(out, root)
        if not motions:
            frame = _frame_at(frames, first.t)
            shift(
                out,
                subj,
                first.dx,
                first.dy,
                _Frames({str(o.get("id")): o for o in (frame or {}).get("objects") or []}),
            )
            what = f"moved {first.dx:+.0f}, {first.dy:+.0f}"
            if first.tilt is not None and root is not None:
                objects[root]["rotation"] = round(first.tilt, 1)
            worst = verdict.worst.get(n)
            turned = first if first.turn else worst
            if (
                steer
                and root is not None
                and turned is not None
                and turned.turn
                and turned.heading is not None
            ):
                own = float(objects[root].get("rotation") or 0)
                objects[root]["rotation"] = _lie_along(own, turned.heading, turned.turn)
                what += f", turned {objects[root]['rotation'] - own:+.0f} degrees"
            moved |= subj
            done.append(f"{rel.subject} {rel.relation} {rel.object}: {what}")
            continue
        # A moving subject: where it starts, and where each move ends, on the line or surface.
        settling = settling_spans(out, compiled, subj | obj)
        shifted: dict[str, Point] = {}  # per moving part: how far it stands from where it was drawn
        t_start = min(float(times[a["id"]]["start"]) for a in motions)
        seen = _start_seen(frames, t_start, body, settling)
        start_heading: float | None = None
        if seen is not None and rel.when != "end":
            s, pts, start_frame = seen
            shape0 = _shape_near(
                frames,
                t_start,
                rel.object,
                obj,
                s if rel.relation in ("on", "along") else None,
                settling,
                split=rel.relation == "along",
            )
            if root is not None and rel.relation == "along":
                start_heading = heading_of(start_frame, root, objects, s)
            if shape0 is not None:
                c = measure(
                    rel,
                    s,
                    shape0,
                    float(start_frame.get("t", 0)),
                    points=pts,
                    band=_band(shape0, objects),
                    under=_surfaces_near(frames, t_start, rel.object, obj, settling)
                    if rel.relation in ("on", "under")
                    else None,
                )
                if math.hypot(c.dx, c.dy) > TOLERANCE:
                    fr = _Frames({str(o.get("id")): o for o in start_frame.get("objects") or []})
                    shift(out, subj, c.dx, c.dy, fr, motion=False)
                    shifted = {r: (c.dx, c.dy) for r in _roots(subj, objects)}
        followed = False
        way: tuple[Point, Point] | None = None
        for a in sorted(motions, key=lambda a: float(times[a["id"]]["start"])):
            span = times[a["id"]]
            end_seen = _seen_near(frames, float(span["end"]) + 0.02, body, False, settling)
            beg_seen = _start_seen(frames, float(span["start"]), body, settling)
            if end_seen is None or beg_seen is None:
                continue
            (s1, pts1, end_frame), (s0, _, _) = end_seen, beg_seen
            near = s1 if rel.relation in ("on", "along") else None
            shape = _shape_near(
                frames, float(span["end"]), rel.object, obj, near, settling, split=rel.relation == "along"
            )
            if shape is None:
                continue
            c1 = measure(
                rel,
                s1,
                shape,
                float(span["end"]),
                points=pts1,
                band=_band(shape, objects),
                under=_surfaces_near(frames, float(span["end"]), rel.object, obj, settling)
                if rel.relation in ("on", "under")
                else None,
            )
            target = a["target"]
            fr = _Frames({str(o.get("id")): o for o in end_frame.get("objects") or []})
            origin = fr.origin(target)
            to, by = a.get("to"), a.get("by")
            before = shifted.get(target, (0.0, 0.0))
            if isinstance(to, dict) and to.get("x") is not None:
                to["x"], to["y"] = round(float(to["x"]) + c1.dx, 1), round(float(to["y"]) + c1.dy, 1)
            elif isinstance(by, dict) and by.get("x") is not None and by.get("y") is not None:
                # A move by an amount: where it starts has moved already.
                by["x"] = round(float(by["x"]) + c1.dx - before[0], 1)
                by["y"] = round(float(by["y"]) + c1.dy - before[1], 1)
            elif math.hypot(c1.dx, c1.dy) > TOLERANCE:
                shifted[target] = (0.0, 0.0)
                continue  # moved against another object: it lands where that object is
            shifted[target] = (c1.dx, c1.dy)
            if rel.relation not in ("on", "along") or origin is None:
                continue
            # The way between: follow the line (or the surface) when it bends away from straight.
            p0 = (s0.cx, s0.cy)
            p1 = (s1.cx + c1.dx, s1.cy + c1.dy)
            way = way or (p0, p1)
            path, tilts = _way(rel.relation, shape, p0, p1, s1, _band(shape, objects))
            if rel.relation == "along":
                first_road = _shape_near(
                    frames, float(span["start"]), rel.object, obj, s0, settling, split=True
                )
                if first_road is not None and first_road.id != shape.id:
                    # From one road into another (a car turning at a junction).
                    turn_path = _corner(first_road, shape, p0, p1, s1, objects)
                    path = turn_path or path
            if path and _bends(path, FOLLOW_FROM):
                offset = (origin[0] - s1.cx, origin[1] - s1.cy)
                span_s = float(span["end"]) - float(span["start"])
                heading = None
                if steer and target == root and start_heading is not None and root is not None:
                    d0 = math.degrees(math.atan2(path[1][1] - path[0][1], path[1][0] - path[0][0]))
                    own = float(objects[root].get("rotation") or 0)
                    heading = own - start_heading + _facing(start_heading, d0)
                if _follow(
                    out, a, target, path, offset, tilts if rel.relation == "on" else None, span_s, heading
                ):
                    followed = True
                    done.append(f"{rel.subject} {rel.relation} {rel.object}: follows it on '{a.get('id')}'")
        worst = verdict.worst.get(n)
        crossing = (worst is not None and worst.turn) or first.turn
        if steer and not followed and crossing and start_heading is not None and way is not None and root:
            # Lying across its road while it drives along it: turned once to face the way it goes.
            d0 = math.degrees(math.atan2(way[1][1] - way[0][1], way[1][0] - way[0][0]))
            own = float(objects[root].get("rotation") or 0)
            want = d0 + _facing(start_heading, d0)
            objects[root]["rotation"] = round(own + _wrap(want - start_heading), 1)
            done.append(f"{rel.subject} {rel.relation} {rel.object}: turned to face its way")
        moved |= subj
        done.append(f"{rel.subject} {rel.relation} {rel.object}: moves snapped")
    return out, done


def _way(
    kind: str, shape: Shape, p0: Point, p1: Point, s: Box, band: float | None = None
) -> tuple[list[Point], list[float]]:
    """The centre's way from p0 to p1: along the object's line (in the lane it starts in), or riding
    its top surface."""
    if kind == "along":
        line = middle_line(shape)
        q0, a, d0 = nearest(line, p0)
        _, b, _ = nearest(line, p1)
        path = stretch(line, a, b)
        room = max(0.0, (half_width(shape) if band is None else band) - min(s.w, s.h) / 2 - LANE_MARGIN)
        lane = max(-room, min(room, side_of(q0, d0, p0)))
        if abs(lane) > 0.5 and len(path) >= 2:
            # The lane is to the right (or left) of the line's own direction; driving against it
            # keeps the same side of the road.
            path = offset_line(path, lane if a <= b else -lane)
        return path, []
    # How far the centre rides off the surface, measured where the move ends (snapped to touch it),
    # along the surface's normal: a ball keeps touching where the slope changes.
    end = edge_at(shape, p1[0], top=True)
    lift = s.h / 2 if end is None else max(1.0, (end[0] - p1[1]) * math.cos(math.radians(end[1])))

    def slope_at(x: float) -> float:
        hit = edge_at(shape, x, top=True)
        return math.radians(hit[1]) if hit is not None else 0.0

    # The points of contact under the centre's start and end.
    a = p0[0] - lift * math.sin(slope_at(p0[0]))
    b = p1[0] - lift * math.sin(slope_at(p1[0]))
    pts: list[Point] = []
    tilts: list[float] = []
    n = max(2, int(abs(b - a) / 12))
    for i in range(n + 1):
        x = a + (b - a) * i / n
        hit = edge_at(shape, x, top=True)
        if hit is None:
            continue
        y, slope = hit
        r = math.radians(slope)
        pts.append((x + lift * math.sin(r), y - lift * math.cos(r)))
        tilts.append(slope if abs(slope) >= TILT_FROM else 0.0)
    return pts, tilts


def _room(shape: Shape, band: float | None, s: Box) -> float:
    """How far off a road's middle line a thing may be and still be on it (its lane)."""
    return max(0.0, (half_width(shape) if band is None else band) - min(s.w, s.h) / 2 - LANE_MARGIN)


def _aside(q: Point, direction: float, off: float) -> Point:
    r = math.radians(direction)
    return q[0] - off * math.sin(r), q[1] + off * math.cos(r)


def _meet(a: Point, da: float, b: Point, db: float) -> Point | None:
    """Where the line through `a` heading `da` meets the line through `b` heading `db`."""
    ax, ay = math.cos(math.radians(da)), math.sin(math.radians(da))
    bx, by = math.cos(math.radians(db)), math.sin(math.radians(db))
    den = ax * by - ay * bx
    if abs(den) < 0.2:  # (nearly) parallel: no crossing
        return None
    t = ((b[0] - a[0]) * by - (b[1] - a[1]) * bx) / den
    return a[0] + t * ax, a[1] + t * ay


def _corner(
    first: Shape, second: Shape, p0: Point, p1: Point, s: Box, objects: dict[str, dict[str, Any]]
) -> list[Point] | None:
    """The way from one road into another (a car turning at a junction): along the first in its lane to
    where they cross, round the corner, and on along the second in its lane. None when the roads do not
    cross ahead of it."""
    qa, _, da = nearest(middle_line(first), p0)
    qb, _, db = nearest(middle_line(second), p1)
    ra, rb = _room(first, _band(first, objects), s), _room(second, _band(second, objects), s)
    start = _aside(qa, da, max(-ra, min(ra, side_of(qa, da, p0))))
    end = _aside(qb, db, max(-rb, min(rb, side_of(qb, db, p1))))
    x = _meet(start, da, end, db)
    if x is None:
        return None
    la, lb = math.dist(start, x), math.dist(x, end)
    if la < 1 or lb < 1 or math.dist(start, end) < 1:
        return None
    ua = ((x[0] - start[0]) / la, (x[1] - start[1]) / la)
    ub = ((end[0] - x[0]) / lb, (end[1] - x[1]) / lb)
    if ua[0] * ub[0] + ua[1] * ub[1] < -0.5:
        return None  # it would have to turn back: not a turn at a crossing
    r = min(CORNER, la / 2, lb / 2)
    c0 = (x[0] - ua[0] * r, x[1] - ua[1] * r)
    c1 = (x[0] + ub[0] * r, x[1] + ub[1] * r)
    bend = [
        (
            (1 - k) ** 2 * c0[0] + 2 * (1 - k) * k * x[0] + k * k * c1[0],
            (1 - k) ** 2 * c0[1] + 2 * (1 - k) * k * x[1] + k * k * c1[1],
        )
        for k in (i / 8 for i in range(9))
    ]
    return [start, *bend, end]


def resolve_kinds(
    dsl: dict[str, Any], relations: list[PlanRelation], frames: list[dict[str, Any]]
) -> list[PlanRelation]:
    """A thing `on` a road seen from above (in it, or on a road running up the page) is along it: in its
    lane, not on its top edge."""
    objects = {str(o.get("id")): o for o in dsl.get("objects") or []}
    fixed = fixed_objects(dsl)
    out: list[PlanRelation] = []
    for rel in relations:
        if rel.relation == "on" and _is_road(objects.get(rel.object) or {"id": rel.object}):
            body = _body(rel.subject, members_of(rel.subject, objects, fixed), objects)
            obj = members_of(rel.object, objects, fixed)
            for f in frames:
                sb = subject_box(f, body)
                shape = object_shape(f, rel.object, obj, sb, split=True) if sb is not None else None
                if sb is None or shape is None:
                    continue
                b = shape.box
                if shape.closed and (b.contains(sb.cx, sb.cy) or b.h > b.w):
                    rel = rel.model_copy(update={"relation": "along"})
                break
        out.append(rel)
    return out


def _bends(path: list[Point], by: float) -> bool:
    """Whether a path strays from the straight line between its ends by more than `by`."""
    (x0, y0), (x1, y1) = path[0], path[-1]
    length = math.hypot(x1 - x0, y1 - y0) or 1.0
    return any(abs((x1 - x0) * (y0 - y) - (x0 - x) * (y1 - y0)) / length > by for x, y in path)


def intended(issue_message: str, relations: list[PlanRelation]) -> bool:
    """Whether an overlap the engine reports is one the scene means (a car on its road, juice in its
    glass): it names a part of a subject and a part of the object of a touching relation."""
    for r in relations:
        if r.relation not in CONTACT:
            continue
        ids = set(_QUOTED.findall(issue_message))
        hit_s = any(i == r.subject or i.startswith(r.subject + "_") for i in ids)
        hit_o = any(i == r.object or i.startswith(r.object + "_") for i in ids)
        if hit_s and hit_o:
            return True
    return False
