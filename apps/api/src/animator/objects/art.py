"""Vector geometry for saved drawings (ADR-055). Plain functions, no I/O.

Path data in any SVG form (all commands, relative or absolute, arcs, shorthand curves) becomes
absolute moves, lines and cubic curves, which can be transformed exactly, measured, written back
compactly, and flattened to polygons. On the polygons: is a point inside a shape (its fill rule),
how far is it from an edge, and, for each part of a drawing, a point well inside what is visible of
it, where a label's leader line should end (`interior_point`).

The engine's parser (packages/engine/src/geom/path.ts) reads everything `serialize` writes.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

MAX_PATH_CHARS = 20_000  # the engine's limit for one path
MAX_SEGMENTS = 4_000

# ("M", x, y) | ("L", x, y) | ("C", x1, y1, x2, y2, x, y) | ("Z",)
Seg = tuple[Any, ...]
Point = tuple[float, float]
Matrix = tuple[float, float, float, float, float, float]  # a b c d e f, as in SVG

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


class PathError(ValueError):
    pass


_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_SEP = re.compile(r"[\s,]*")
_CMDS = set("MmZzLlHhVvCcSsQqTtAa")


class _Scanner:
    def __init__(self, d: str) -> None:
        self.d = d
        self.i = 0
        self.n = len(d)

    def skip(self) -> None:
        m = _SEP.match(self.d, self.i)
        if m:
            self.i = m.end()

    def done(self) -> bool:
        self.skip()
        return self.i >= self.n

    def command(self) -> str | None:
        self.skip()
        if self.i < self.n and self.d[self.i] in _CMDS:
            self.i += 1
            return self.d[self.i - 1]
        return None

    def number(self) -> float:
        self.skip()
        m = _NUM.match(self.d, self.i)
        if not m:
            raise PathError(f"expected a number at character {self.i}")
        self.i = m.end()
        v = float(m.group())
        if not math.isfinite(v):
            raise PathError("non-finite coordinate")
        return v

    def flag(self) -> bool:
        self.skip()
        if self.i < self.n and self.d[self.i] in "01":
            self.i += 1
            return self.d[self.i - 1] == "1"
        raise PathError(f"expected an arc flag (0 or 1) at character {self.i}")


def _arc(
    x1: float, y1: float, rx: float, ry: float, phi_deg: float, large: bool, sweep: bool, x2: float, y2: float
) -> list[Seg]:
    """An elliptical arc as cubic curves (SVG implementation notes, F.6.5)."""
    if x1 == x2 and y1 == y2:
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [("L", x2, y2)]
    phi = math.radians(phi_deg % 360)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        coef = -coef
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (x1 + x2) / 2
    cy = sp * cxp + cp * cyp + (y1 + y2) / 2

    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    theta = angle(1.0, 0.0, ux, uy)
    delta = angle(ux, uy, vx, vy)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi
    n = max(1, math.ceil(abs(delta) / (math.pi / 2) - 1e-9))
    step = delta / n
    t = 4 / 3 * math.tan(step / 4)

    def at(a: float) -> Point:
        ca, sa = math.cos(a), math.sin(a)
        return cx + rx * ca * cp - ry * sa * sp, cy + rx * ca * sp + ry * sa * cp

    def slope(a: float) -> Point:
        ca, sa = math.cos(a), math.sin(a)
        return -rx * sa * cp - ry * ca * sp, -rx * sa * sp + ry * ca * cp

    out: list[Seg] = []
    a = theta
    for i in range(n):
        b = a + step
        p0, p1 = at(a), at(b)
        d0, d1 = slope(a), slope(b)
        end = (x2, y2) if i == n - 1 else p1
        out.append(("C", p0[0] + t * d0[0], p0[1] + t * d0[1], p1[0] - t * d1[0], p1[1] - t * d1[1], *end))
        a = b
    return out


def parse_path(d: str) -> list[Seg]:
    """SVG path data -> absolute M/L/C/Z segments. Raises PathError on anything malformed."""
    if len(d) > MAX_PATH_CHARS * 4:
        raise PathError("path data is too long")
    sc = _Scanner(d)
    segs: list[Seg] = []
    cx = cy = sx = sy = 0.0
    last_c2: Point | None = None  # for S
    last_q: Point | None = None  # for T
    cmd: str | None = None
    if sc.done():
        raise PathError("empty path data")
    while not sc.done():
        c = sc.command()
        if c is not None:
            cmd = c
            if cmd in "Zz":
                if segs:
                    segs.append(("Z",))
                cx, cy = sx, sy
                last_c2 = last_q = None
                continue
        elif cmd is None or cmd in "Zz":
            raise PathError("numbers without a command")
        rel = cmd.islower()
        k = cmd.upper()
        ox, oy = (cx, cy) if rel else (0.0, 0.0)
        if k == "M":
            x, y = sc.number() + ox, sc.number() + oy
            segs.append(("M", x, y))
            cx, cy = sx, sy = x, y
            cmd = "l" if rel else "L"  # further pairs are line-tos
            last_c2 = last_q = None
        elif not segs:
            raise PathError("path data must start with a move (M)")
        elif k == "L":
            x, y = sc.number() + ox, sc.number() + oy
            segs.append(("L", x, y))
            cx, cy = x, y
            last_c2 = last_q = None
        elif k == "H":
            x = sc.number() + ox
            segs.append(("L", x, cy))
            cx = x
            last_c2 = last_q = None
        elif k == "V":
            y = sc.number() + oy
            segs.append(("L", cx, y))
            cy = y
            last_c2 = last_q = None
        elif k == "C":
            x1, y1 = sc.number() + ox, sc.number() + oy
            x2, y2 = sc.number() + ox, sc.number() + oy
            x, y = sc.number() + ox, sc.number() + oy
            segs.append(("C", x1, y1, x2, y2, x, y))
            cx, cy, last_c2, last_q = x, y, (x2, y2), None
        elif k == "S":
            x1, y1 = (2 * cx - last_c2[0], 2 * cy - last_c2[1]) if last_c2 else (cx, cy)
            x2, y2 = sc.number() + ox, sc.number() + oy
            x, y = sc.number() + ox, sc.number() + oy
            segs.append(("C", x1, y1, x2, y2, x, y))
            cx, cy, last_c2, last_q = x, y, (x2, y2), None
        elif k in "QT":
            if k == "Q":
                qx, qy = sc.number() + ox, sc.number() + oy
            else:
                qx, qy = (2 * cx - last_q[0], 2 * cy - last_q[1]) if last_q else (cx, cy)
            x, y = sc.number() + ox, sc.number() + oy
            segs.append(
                (
                    "C",
                    cx + 2 / 3 * (qx - cx),
                    cy + 2 / 3 * (qy - cy),
                    x + 2 / 3 * (qx - x),
                    y + 2 / 3 * (qy - y),
                    x,
                    y,
                )
            )
            cx, cy, last_q, last_c2 = x, y, (qx, qy), None
        elif k == "A":
            rx, ry, rot = sc.number(), sc.number(), sc.number()
            large, sweep = sc.flag(), sc.flag()
            x, y = sc.number() + ox, sc.number() + oy
            segs.extend(_arc(cx, cy, rx, ry, rot, large, sweep, x, y))
            cx, cy = x, y
            last_c2 = last_q = None
        if len(segs) > MAX_SEGMENTS:
            raise PathError(f"more than {MAX_SEGMENTS} segments")
    if not segs:
        raise PathError("no drawing commands")
    return segs


# ---------------------------------------------------------------------- transform, measure, write


def multiply(m: Matrix, n: Matrix) -> Matrix:
    """m x n: apply n first, then m."""
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a * a2 + c * b2,
        b * a2 + d * b2,
        a * c2 + c * d2,
        b * c2 + d * d2,
        a * e2 + c * f2 + e,
        b * e2 + d * f2 + f,
    )


def apply(m: Matrix, x: float, y: float) -> Point:
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def scale_of(m: Matrix) -> float:
    """How much a matrix scales lengths on average (for stroke widths)."""
    return math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))


def transform(segs: Iterable[Seg], m: Matrix) -> list[Seg]:
    out: list[Seg] = []
    for s in segs:
        if s[0] == "Z":
            out.append(s)
        elif s[0] == "C":
            out.append(("C", *apply(m, s[1], s[2]), *apply(m, s[3], s[4]), *apply(m, s[5], s[6])))
        else:
            out.append((s[0], *apply(m, s[1], s[2])))
    return out


def _extrema(p0: float, p1: float, p2: float, p3: float) -> list[float]:
    a, b, c = p1 - p0, p2 - p1, p3 - p2
    qa, qb, qc = a - 2 * b + c, 2 * (b - a), a
    if abs(qa) < 1e-12:
        return [-qc / qb] if abs(qb) > 1e-12 and 0 < -qc / qb < 1 else []
    disc = qb * qb - 4 * qa * qc
    if disc < 0:
        return []
    r = math.sqrt(disc)
    return [t for t in ((-qb + r) / (2 * qa), (-qb - r) / (2 * qa)) if 0 < t < 1]


def _cubic(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    u = 1 - t
    return u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def center(self) -> Point:
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2

    def union(self, o: Box) -> Box:
        return Box(min(self.x0, o.x0), min(self.y0, o.y0), max(self.x1, o.x1), max(self.y1, o.y1))

    def grow(self, pad: float) -> Box:
        return Box(self.x0 - pad, self.y0 - pad, self.x1 + pad, self.y1 + pad)

    def overlaps(self, o: Box) -> bool:
        return self.x0 <= o.x1 and o.x0 <= self.x1 and self.y0 <= o.y1 and o.y0 <= self.y1


def bounds(segs: Sequence[Seg]) -> Box | None:
    """Exact bounds (curve extrema, not control points); None for nothing drawn."""
    xs: list[float] = []
    ys: list[float] = []
    cx = cy = sx = sy = 0.0
    for s in segs:
        if s[0] == "M":
            cx, cy = sx, sy = s[1], s[2]
            xs.append(cx)
            ys.append(cy)
        elif s[0] == "L":
            cx, cy = s[1], s[2]
            xs.append(cx)
            ys.append(cy)
        elif s[0] == "C":
            xs.append(s[5])
            ys.append(s[6])
            xs.extend(_cubic(cx, s[1], s[3], s[5], t) for t in _extrema(cx, s[1], s[3], s[5]))
            ys.extend(_cubic(cy, s[2], s[4], s[6], t) for t in _extrema(cy, s[2], s[4], s[6]))
            cx, cy = s[5], s[6]
        else:
            cx, cy = sx, sy
    if not xs:
        return None
    return Box(min(xs), min(ys), max(xs), max(ys))


def fmt(v: float, digits: int = 2) -> str:
    s = f"{round(v, digits):.{digits}f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def serialize(segs: Iterable[Seg], digits: int = 2) -> str:
    """Compact absolute path data (M L C Z), as the engine reads it."""
    out: list[str] = []
    for s in segs:
        if s[0] == "Z":
            out.append("Z")
        else:
            out.append(s[0] + " ".join(fmt(v, digits) for v in s[1:]))
    return "".join(out)


# ---------------------------------------------------------------------- polygons


def flatten(segs: Sequence[Seg], tolerance: float = 1.0) -> list[list[Point]]:
    """Each subpath as a polyline (curves split finely enough for `tolerance`)."""
    rings: list[list[Point]] = []
    ring: list[Point] = []
    cx = cy = sx = sy = 0.0
    for s in segs:
        if s[0] == "M":
            if len(ring) > 1:
                rings.append(ring)
            cx, cy = sx, sy = s[1], s[2]
            ring = [(cx, cy)]
        elif s[0] == "Z":
            if len(ring) > 1:
                if ring[-1] != (sx, sy):
                    ring.append((sx, sy))  # the closing edge is drawn too
                rings.append(ring)
            cx, cy = sx, sy
            ring = [(cx, cy)]
        else:
            if not ring:
                ring = [(cx, cy)]
            if s[0] == "L":
                cx, cy = s[1], s[2]
                ring.append((cx, cy))
            else:
                length = (
                    math.dist((cx, cy), (s[1], s[2]))
                    + math.dist((s[1], s[2]), (s[3], s[4]))
                    + math.dist((s[3], s[4]), (s[5], s[6]))
                )
                n = max(2, min(64, math.ceil(length / max(tolerance * 4, 1e-6))))
                for i in range(1, n + 1):
                    t = i / n
                    ring.append((_cubic(cx, s[1], s[3], s[5], t), _cubic(cy, s[2], s[4], s[6], t)))
                cx, cy = s[5], s[6]
    if len(ring) > 1:
        rings.append(ring)
    return rings


def _edges(rings: Sequence[Sequence[Point]], closed: bool = True) -> Iterable[tuple[Point, Point]]:
    for ring in rings:
        for i in range(len(ring) - 1):
            yield ring[i], ring[i + 1]
        if closed and len(ring) > 2 and ring[0] != ring[-1]:
            yield ring[-1], ring[0]


def inside(x: float, y: float, rings: Sequence[Sequence[Point]], evenodd: bool = False) -> bool:
    """Is the point inside the fill of these subpaths (each implicitly closed, as SVG fills them)?"""
    w = 0
    for (x0, y0), (x1, y1) in _edges(rings):
        if y0 <= y:
            if y1 > y and (x1 - x0) * (y - y0) - (x - x0) * (y1 - y0) > 0:
                w += 1
        elif y1 <= y and (x1 - x0) * (y - y0) - (x - x0) * (y1 - y0) < 0:
            w -= 1
    return (w % 2 != 0) if evenodd else w != 0


def _seg_dist(px: float, py: float, a: Point, b: Point) -> float:
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    ll = dx * dx + dy * dy
    t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def distance(x: float, y: float, rings: Sequence[Sequence[Point]], closed: bool = True) -> float:
    """Distance from a point to the nearest edge."""
    best = math.inf
    for a, b in _edges(rings, closed):
        d = _seg_dist(x, y, a, b)
        if d < best:
            best = d
    return best


def polygon_area(ring: Sequence[Point]) -> float:
    s = 0.0
    for i in range(len(ring)):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % len(ring)]
        s += x0 * y1 - x1 * y0
    return s / 2


# ---------------------------------------------------------------------- where a part is


@dataclass
class Geom:
    """One shape of a drawing, ready for point tests."""

    rings: list[list[Point]]
    box: Box
    filled: bool
    stroke: float  # stroke width (0: none)
    evenodd: bool = False
    opaque: bool = True

    def covers(self, x: float, y: float) -> bool:
        """Does this shape paint the point (its fill, or its stroke)?"""
        if not self.box.grow(self.stroke / 2 + 1e-9).overlaps(Box(x, y, x, y)):
            return False
        if self.filled and inside(x, y, self.rings, self.evenodd):
            return True
        return self.stroke > 0 and distance(x, y, self.rings, closed=self.filled) <= self.stroke / 2

    def clearance(self, x: float, y: float) -> float:
        return distance(x, y, self.rings, closed=self.filled)


def geom_of(
    d: str, filled: bool, stroke: float, evenodd: bool = False, opaque: bool = True, tolerance: float = 1.0
) -> Geom | None:
    try:
        segs = parse_path(d)
    except PathError:
        return None
    box = bounds(segs)
    if box is None:
        return None
    rings = flatten(segs, tolerance)
    return Geom(rings, box, filled, stroke, evenodd, opaque)


@dataclass
class Spot:
    """Where a part of a drawing is: a point on what is visible of it, how far that point is from
    the nearest edge, and how much of the part is visible (not under shapes painted over it)."""

    x: float
    y: float
    clearance: float
    visible: float


def interior_point(
    geoms: Sequence[Geom], owners: Sequence[str | None], part: str, grid: int = 24
) -> Spot | None:
    """The point well inside what is visible of a part: painted by the part itself (the topmost
    shape there is one of its own), as far as possible from every edge near it (the pole of
    inaccessibility, found on a grid and refined twice), the most central of equally good points.
    A part drawn only with lines gets the most central visible point on its lines."""
    mine = [i for i, o in enumerate(owners) if o == part]
    if not mine:
        return None
    box = geoms[mine[0]].box
    for i in mine[1:]:
        box = box.union(geoms[i].box)
    first = mine[0]
    near = [
        geoms[i]
        for i in range(first + 1, len(geoms))
        if owners[i] != part and geoms[i].opaque and geoms[i].box.overlaps(box)
    ]
    own = [geoms[i] for i in mine]
    cx, cy = box.center

    def top(x: float, y: float) -> str | bool | None:
        for i in range(len(geoms) - 1, -1, -1):
            g = geoms[i]
            if (g.opaque or owners[i] == part) and g.covers(x, y):
                return owners[i]
        return False

    def clearance(x: float, y: float) -> float:
        return min(
            min(g.clearance(x, y) for g in own), min((g.clearance(x, y) for g in near), default=math.inf)
        )

    if not any(g.filled for g in own):  # lines: the most central visible point on them
        pts = [pt for g in own for ring in g.rings for pt in _densify(ring, max(box.w, box.h) / 60 or 1.0)]
        seen = [pt for pt in pts if top(*pt) == part]
        if not seen:
            return None
        bx, by = min(seen, key=lambda pt: math.hypot(pt[0] - cx, pt[1] - cy))
        return Spot(bx, by, 0.0, len(seen) / len(pts))

    best: tuple[float, float, float] | None = None
    inside_n = shown_n = 0
    x0, y0, w, h = box.x0, box.y0, max(box.w, 1e-6), max(box.h, 1e-6)
    n = grid
    for level in range(3):
        stepx, stepy = w / n, h / n
        for i in range(n):
            for j in range(n):
                x, y = x0 + (i + 0.5) * stepx, y0 + (j + 0.5) * stepy
                if not any(g.filled and g.covers(x, y) for g in own):
                    continue
                visible = top(x, y) == part
                if level == 0:
                    inside_n += 1
                    shown_n += visible
                if not visible:
                    continue
                # Clearance first; 100 units off the center cost 1 unit of clearance.
                score = clearance(x, y) - 0.01 * math.hypot(x - cx, y - cy)
                if best is None or score > best[0]:
                    best = (score, x, y)
        if best is None:
            break
        _, bx, by = best
        x0, y0, w, h = bx - stepx, by - stepy, 2 * stepx, 2 * stepy
        n = 8
    if best is None:
        return None
    _, bx, by = best
    c = clearance(bx, by)
    return Spot(bx, by, 0.0 if math.isinf(c) else c, shown_n / inside_n if inside_n else 0.0)


def _densify(ring: Sequence[Point], step: float) -> list[Point]:
    out: list[Point] = []
    for a, b in pairwise(ring):
        n = max(1, math.ceil(math.dist(a, b) / step))
        out.extend((a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(n))
    if ring:
        out.append(ring[-1])
    return out
