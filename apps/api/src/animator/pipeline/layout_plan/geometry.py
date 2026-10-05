"""Outlines of objects as the engine draws them (ADR-048): from a sampled frame object (its params and
its local-to-world matrix), the shape a relation is checked against, in world coordinates.

- Boxes (a rectangle, a block, an icon, a card...): the four corners of the local box, transformed, so a
  rectangle turned into a ramp has a sloped top.
- Lines and arrows: the segment between their resolved endpoints.
- Polygons: their points (or the regular polygon of `sides` and `radius`).
- Paths: the SVG path data flattened into a polyline (M L H V C S Q T A Z; arcs as straight chords).

Then: a shape's top (or bottom) at a given x and its slope there, its middle line (a road seen from
above, a wire, a river), and the nearest point of a polyline to a point, with its direction.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from animator.pipeline.layout_plan.regions import Box

Point = tuple[float, float]
Matrix = tuple[float, float, float, float, float, float]

ENDPOINTS = {"arrow", "line", "dimension", "bracket", "rope", "spring"}
_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}
CURVE_STEPS = 12


@dataclass(frozen=True)
class Shape:
    """An outline in world coordinates: a closed ring (boxes, polygons, closed paths) or an open
    polyline (lines, arrows, open paths)."""

    id: str
    points: tuple[Point, ...]
    closed: bool
    width: float = 0.0  # a line standing for a band (the middle of a track): half its width

    @property
    def box(self) -> Box:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return Box(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def segments(self) -> list[tuple[Point, Point]]:
        pts = list(self.points)
        segs = list(pairwise(pts))
        if self.closed and len(pts) > 2:
            segs.append((pts[-1], pts[0]))
        return segs


def apply(m: Matrix, p: Point) -> Point:
    a, b, c, d, e, f = m
    return a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f


def _flatten_path(d: str) -> tuple[list[Point], bool]:
    """SVG path data as one polyline (sub-paths joined), and whether it closes."""
    tokens = _TOKEN.findall(d)
    out: list[Point] = []
    cur: Point = (0.0, 0.0)
    start: Point = (0.0, 0.0)
    last_ctrl: Point | None = None
    closed = False
    cmd = ""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            if cmd in "Zz":
                closed = True
                cur = start
                out.append(cur)
                continue
        if not cmd:
            break
        upper = cmd.upper()
        n = _ARGS[upper]
        if i + n > len(tokens) or any(t.isalpha() for t in tokens[i : i + n]):
            break
        v = [float(t) for t in tokens[i : i + n]]
        i += n
        rel = cmd.islower()
        ox, oy = cur if rel else (0.0, 0.0)
        if upper == "M":
            cur = (ox + v[0], oy + v[1])
            start = cur
            out.append(cur)
            cmd = "l" if rel else "L"  # further pairs are lines
            last_ctrl = None
        elif upper == "L":
            cur = (ox + v[0], oy + v[1])
            out.append(cur)
            last_ctrl = None
        elif upper == "H":
            cur = ((cur[0] if rel else 0.0) + v[0], cur[1])
            out.append(cur)
            last_ctrl = None
        elif upper == "V":
            cur = (cur[0], (cur[1] if rel else 0.0) + v[0])
            out.append(cur)
            last_ctrl = None
        elif upper in ("C", "S"):
            if upper == "C":
                c1 = (ox + v[0], oy + v[1])
                c2, end = (ox + v[2], oy + v[3]), (ox + v[4], oy + v[5])
            else:
                c1 = (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1]) if last_ctrl else cur
                c2, end = (ox + v[0], oy + v[1]), (ox + v[2], oy + v[3])
            p0 = cur
            for k in range(1, CURVE_STEPS + 1):
                t = k / CURVE_STEPS
                u = 1 - t
                out.append(
                    (
                        u**3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t**3 * end[0],
                        u**3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t**3 * end[1],
                    )
                )
            cur, last_ctrl = end, c2
        elif upper in ("Q", "T"):
            if upper == "Q":
                c, end = (ox + v[0], oy + v[1]), (ox + v[2], oy + v[3])
            else:
                c = (2 * cur[0] - last_ctrl[0], 2 * cur[1] - last_ctrl[1]) if last_ctrl else cur
                end = (ox + v[0], oy + v[1])
            p0 = cur
            for k in range(1, CURVE_STEPS + 1):
                t = k / CURVE_STEPS
                u = 1 - t
                out.append(
                    (
                        u * u * p0[0] + 2 * u * t * c[0] + t * t * end[0],
                        u * u * p0[1] + 2 * u * t * c[1] + t * t * end[1],
                    )
                )
            cur, last_ctrl = end, c
        elif upper == "A":
            cur = (ox + v[5], oy + v[6])  # an arc as its chord: close enough for a surface
            out.append(cur)
            last_ctrl = None
    return out, closed


def _regular_polygon(sides: int, radius: float) -> list[Point]:
    return [
        (
            radius * math.cos(-math.pi / 2 + 2 * math.pi * i / sides),
            radius * math.sin(-math.pi / 2 + 2 * math.pi * i / sides),
        )
        for i in range(sides)
    ]


def shape_of(o: dict[str, Any]) -> Shape | None:
    """The outline of a sampled frame object, or None when it has no size."""
    m_raw = o.get("matrix")
    m: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    if isinstance(m_raw, list) and len(m_raw) == 6:
        m0, m1, m2, m3, m4, m5 = (float(v) for v in m_raw)
        m = (m0, m1, m2, m3, m4, m5)
    params = o.get("params") or {}
    kind = str(o.get("type"))
    oid = str(o.get("id"))
    if kind in ENDPOINTS:
        p0, p1 = params.get("from"), params.get("to")
        if isinstance(p0, dict) and isinstance(p1, dict) and "x" in p0 and "x" in p1:
            pts = (apply(m, (float(p0["x"]), float(p0["y"]))), apply(m, (float(p1["x"]), float(p1["y"]))))
            return Shape(oid, pts, False)
    if kind == "polygon":
        raw = params.get("points")
        local = (
            [(float(p["x"]), float(p["y"])) for p in raw if isinstance(p, dict)]
            if isinstance(raw, list) and len(raw) >= 3
            else _regular_polygon(int(params.get("sides") or 6), float(params.get("radius") or 60))
        )
        return Shape(oid, tuple(apply(m, p) for p in local), True)
    if kind == "path" and isinstance(params.get("d"), str):
        local, closed = _flatten_path(params["d"])
        if len(local) >= 2:
            return Shape(oid, tuple(apply(m, p) for p in local), closed or bool(params.get("closed")))
    lb = o.get("localBBox")
    if isinstance(lb, dict) and (float(lb.get("w", 0)) > 0 or float(lb.get("h", 0)) > 0):
        x, y, w, h = float(lb["x"]), float(lb["y"]), float(lb["w"]), float(lb["h"])
        corners = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
        return Shape(oid, tuple(apply(m, p) for p in corners), True)
    bb = o.get("bbox")
    if isinstance(bb, dict) and (float(bb.get("w", 0)) > 0 or float(bb.get("h", 0)) > 0):
        x, y, w, h = float(bb["x"]), float(bb["y"]), float(bb["w"]), float(bb["h"])
        return Shape(oid, ((x, y), (x + w, y), (x + w, y + h), (x, y + h)), True)
    return None


def edge_at(shape: Shape, x: float, top: bool = True) -> tuple[float, float] | None:
    """Where a vertical line at `x` meets the shape's top (or bottom) edge, and the edge's slope there
    (degrees, clockwise as the engine turns things; 0 for level). None when `x` is outside the shape."""
    hits: list[tuple[float, float]] = []
    for (x0, y0), (x1, y1) in shape.segments():
        lo, hi = min(x0, x1), max(x0, x1)
        if hi - lo < 1e-6:
            continue  # a vertical side has no top to stand on
        if lo - 1e-6 <= x <= hi + 1e-6:
            t = (x - x0) / (x1 - x0)
            y = y0 + t * (y1 - y0)
            ang = math.degrees(math.atan2(y1 - y0, x1 - x0))
            if ang > 90:
                ang -= 180
            elif ang < -90:
                ang += 180
            hits.append((y, ang))
    if not hits:
        return None
    return min(hits) if top else max(hits)


def middle_line(shape: Shape) -> list[Point]:
    """The line a thing travels along: the polyline of a line, arrow or open path; for a closed shape,
    its middle line along its longer side (a road drawn as a band)."""
    if not shape.closed:
        return list(shape.points)
    pts = list(shape.points)
    if len(pts) == 4 or len(pts) == 5:
        p0, p1, p2, p3 = pts[:4]
        side_a = math.dist(p0, p1)
        side_b = math.dist(p1, p2)
        if side_a >= side_b:  # p0-p1 and p3-p2 are the long sides
            return [((p0[0] + p3[0]) / 2, (p0[1] + p3[1]) / 2), ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)]
        return [((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2), ((p3[0] + p2[0]) / 2, (p3[1] + p2[1]) / 2)]
    b = shape.box
    if b.w >= b.h:
        return [(b.x, b.cy), (b.right, b.cy)]
    return [(b.cx, b.y), (b.cx, b.bottom)]


def _inside(p: Point, ring: list[Point]) -> bool:
    """Whether `p` is inside a closed ring (ray casting)."""
    x, y = p
    hit = False
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1], strict=True):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            hit = not hit
    return hit


def arms(shape: Shape) -> list[Shape]:
    """A road drawn as one outline of straight arms (a plus-shaped junction, a T, an L): its arms, each
    a band with its own middle line (the arms overlap where they cross). Any other shape as it is."""
    ring: list[Point] = []
    for p in shape.points:
        if not ring or math.dist(ring[-1], p) > 0.5:
            ring.append(p)
    if len(ring) > 1 and math.dist(ring[0], ring[-1]) <= 0.5:
        ring.pop()
    straight = all(
        abs(a[0] - b[0]) < 0.5 or abs(a[1] - b[1]) < 0.5
        for a, b in zip(ring, ring[1:] + ring[:1], strict=True)
    )
    if not shape.closed or len(ring) < 6 or not straight:
        return [shape]
    xs = sorted({round(p[0], 1) for p in ring})
    ys = sorted({round(p[1], 1) for p in ring})
    if len(xs) > 14 or len(ys) > 14:
        return [shape]
    cell = [
        [_inside(((xs[i] + xs[i + 1]) / 2, (ys[j] + ys[j + 1]) / 2), ring) for j in range(len(ys) - 1)]
        for i in range(len(xs) - 1)
    ]
    bars: set[tuple[float, float, float, float]] = set()
    for j in range(len(ys) - 1):  # runs across
        i = 0
        while i < len(xs) - 1:
            if cell[i][j]:
                k = i
                while k + 1 < len(xs) - 1 and cell[k + 1][j]:
                    k += 1
                bars.add((xs[i], ys[j], xs[k + 1], ys[j + 1]))
                i = k + 1
            else:
                i += 1
    for i in range(len(xs) - 1):  # runs down
        j = 0
        while j < len(ys) - 1:
            if cell[i][j]:
                k = j
                while k + 1 < len(ys) - 1 and cell[i][k + 1]:
                    k += 1
                bars.add((xs[i], ys[j], xs[i + 1], ys[k + 1]))
                j = k + 1
            else:
                j += 1
    kept = [
        b
        for b in bars
        if not any(o != b and o[0] <= b[0] and o[1] <= b[1] and o[2] >= b[2] and o[3] >= b[3] for o in bars)
    ]
    if len(kept) < 2:
        return [shape]
    return [
        Shape(f"{shape.id}#{n}", ((x0, y0), (x1, y0), (x1, y1), (x0, y1)), True)
        for n, (x0, y0, x1, y1) in enumerate(sorted(kept))
    ]


def resample(line: list[Point], n: int) -> list[Point]:
    """`n` points evenly spaced along a polyline."""
    lengths = [0.0]
    for a, b in pairwise(line):
        lengths.append(lengths[-1] + math.dist(a, b))
    total = lengths[-1]
    if total <= 0 or n < 2:
        return list(line[:1])
    out: list[Point] = []
    k = 0
    for i in range(n):
        want = total * i / (n - 1)
        while k < len(line) - 2 and lengths[k + 1] < want:
            k += 1
        seg = lengths[k + 1] - lengths[k]
        t = 0.0 if seg <= 0 else (want - lengths[k]) / seg
        (x0, y0), (x1, y1) = line[k], line[k + 1]
        out.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
    return out


def between(a: Shape, b: Shape, name: str) -> Shape | None:
    """The middle of two lines running side by side (a track's two rails, a road drawn as its two
    edges): a line between them, as wide as they are apart. None when they do not run side by side."""
    if a.closed or b.closed or len(a.points) < 2 or len(b.points) < 2:
        return None
    samples = resample(list(a.points), 16)
    if len(samples) < 16:
        return None
    gaps: list[float] = []
    mids: list[Point] = []
    for p in samples:
        q, _, _ = nearest(list(b.points), p)
        gaps.append(math.dist(p, q))
        mids.append(((p[0] + q[0]) / 2, (p[1] + q[1]) / 2))
    mean = sum(gaps) / len(gaps)
    if mean < 8 or max(gaps) - min(gaps) > 0.5 * mean:
        return None
    return Shape(name, tuple(mids), False, mean / 2)


def half_width(shape: Shape) -> float:
    """Half the width of a band (a road drawn as a rectangle: half its shorter side); 0 for a line."""
    if not shape.closed:
        return 0.0
    pts = list(shape.points)
    if len(pts) in (4, 5):
        return min(math.dist(pts[0], pts[1]), math.dist(pts[1], pts[2])) / 2
    b = shape.box
    return min(b.w, b.h) / 2


def side_of(q: Point, direction: float, p: Point) -> float:
    """How far `p` lies to the right of a line through `q` heading `direction` (degrees, y down)."""
    r = math.radians(direction)
    return (p[0] - q[0]) * -math.sin(r) + (p[1] - q[1]) * math.cos(r)


def offset_line(line: list[Point], off: float) -> list[Point]:
    """The polyline moved `off` to the right of its direction (a lane beside a road's middle line)."""
    if abs(off) < 1e-6 or len(line) < 2:
        return list(line)
    dirs = [math.atan2(y1 - y0, x1 - x0) for (x0, y0), (x1, y1) in pairwise(line)]
    out: list[Point] = []
    for i, (x, y) in enumerate(line):
        a = dirs[min(i, len(dirs) - 1)]
        b = dirs[max(i - 1, 0)]
        nx = (-math.sin(a) - math.sin(b)) / 2
        ny = (math.cos(a) + math.cos(b)) / 2
        n = math.hypot(nx, ny) or 1.0
        out.append((x + off * nx / n, y + off * ny / n))
    return out


def nearest(line: list[Point], p: Point) -> tuple[Point, float, float]:
    """The point of a polyline nearest to `p`, its distance along the line, and the line's direction
    there (degrees)."""
    best: tuple[float, Point, float, float] | None = None
    run = 0.0
    for (x0, y0), (x1, y1) in pairwise(line):
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        t = (
            0.0
            if length < 1e-9
            else max(0.0, min(1.0, ((p[0] - x0) * dx + (p[1] - y0) * dy) / (length * length)))
        )
        q = (x0 + t * dx, y0 + t * dy)
        dist = math.dist(q, p)
        if best is None or dist < best[0]:
            best = (dist, q, run + t * length, math.degrees(math.atan2(dy, dx)))
        run += length
    if best is None:
        return line[0], 0.0, 0.0
    return best[1], best[2], best[3]


def stretch(line: list[Point], s0: float, s1: float) -> list[Point]:
    """The part of a polyline between distances `s0` and `s1` along it (in that direction)."""
    a, b = (s0, s1) if s0 <= s1 else (s1, s0)
    marks: list[tuple[float, Point]] = []
    run = 0.0
    for (x0, y0), (x1, y1) in pairwise(line):
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 1e-9:
            continue
        if a < run < b:
            marks.append((run, (x0, y0)))
        for s in (a, b):
            if run <= s <= run + length:
                t = (s - run) / length
                marks.append((s, (x0 + t * (x1 - x0), y0 + t * (y1 - y0))))
        run += length
    marks.sort(key=lambda m: m[0])
    out: list[Point] = []
    for _, q in marks:
        if not out or math.dist(out[-1], q) > 0.5:
            out.append(q)
    return out if s0 <= s1 else out[::-1]
