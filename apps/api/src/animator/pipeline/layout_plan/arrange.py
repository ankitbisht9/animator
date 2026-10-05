"""The arrangement solver (ADR-047): pure geometry, no engine and no model.

An item is one thing that stays in place, measured by the engine: its core (the drawing itself) and
its footprint (the core plus the labels attached to it), both over its whole time on stage. Scaling an
item scales its core; attached labels keep their size and their distance from the core.

A group arranges items (row, column, grid, cycle, free) at one scale for all of them. Groups shown at
the same time get separate lanes of their region; groups shown at different times share a lane. The
region solve finds the largest common scale at which every lane fits, within each group's limits, and
places every item: the result is, for each item, where its core's centre goes and at what scale.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from animator.pipeline.layout_plan.regions import Box

GAP = 48.0  # between items of a group
GAP_CONNECTED = 104.0  # between items joined by arrows: room for a readable arrow
MAX_GAP = 3.0  # spare width spreads the items, up to this many gaps
LANE_GAP = 36.0  # between groups shown at the same time
FILL_W, FILL_H = 0.9, 0.88  # the most of a region a layout fills
FLOOR = 0.5  # the least the planner ever shrinks anything
EDGE_MARGIN = 12.0  # what is kept as drawn stays this far inside its region
WRAP_FROM = 5  # a row of this many items may be wrapped into two
COEXIST_S = 0.6  # groups on stage together for less than this are a hand-over, not company


@dataclass
class Item:
    id: str
    core: Box
    foot: Box
    scalable: bool = True
    smax: float = 1.6
    smin: float = 0.5  # the least it may shrink (its smallest text stays readable)

    @property
    def margins(self) -> tuple[float, float, float, float]:
        """Room the attached labels take on each side of the core: left, right, top, bottom."""
        return (
            max(0.0, self.core.x - self.foot.x),
            max(0.0, self.foot.right - self.core.right),
            max(0.0, self.core.y - self.foot.y),
            max(0.0, self.foot.bottom - self.core.bottom),
        )

    def k(self, s: float) -> float:
        """The item's scale at group scale `s`: a drawing follows it; text (a card, an equation) is
        never enlarged, and shrinks only when there is no room, never below readable."""
        return s if self.scalable else min(1.0, max(s, self.smin))

    def size(self, s: float) -> tuple[float, float]:
        ml, mr, mt, mb = self.margins
        k = self.k(s)
        return k * self.core.w + ml + mr, k * self.core.h + mt + mb


@dataclass
class Placed:
    """Where an item's core centre goes, and its scale."""

    cx: float
    cy: float
    k: float


@dataclass
class Group:
    id: str
    kind: str
    items: list[Item]
    region: str
    t0: float = 0.0
    t1: float = 0.0
    align: str = "center"
    columns: int | None = None
    connected: bool = False
    snake: bool = False  # a wrapped row: its second row runs back, right to left
    _union: Item | None = field(default=None, repr=False)

    @property
    def gap(self) -> float:
        return GAP_CONNECTED if self.connected else GAP

    @property
    def smax(self) -> float:
        caps = [i.smax for i in self.items if i.scalable]
        return min(caps) if caps else 1.0

    @property
    def smin(self) -> float:
        floors = [i.smin for i in self.items]
        return min(self.smax, max(floors)) if floors else 1.0

    def s(self, s: float) -> float:
        return min(max(s, self.smin), self.smax)

    def coexists(self, other: Group) -> bool:
        return min(self.t1, other.t1) - max(self.t0, other.t0) > COEXIST_S

    # ---------------------------------------------------------------- sizes

    def union(self) -> Item:
        """A free group as one item: everything kept where it was drawn, relative to each other."""
        if self._union is None:
            core = self.items[0].core
            foot = self.items[0].foot
            for it in self.items[1:]:
                core, foot = core.union(it.core), foot.union(it.foot)
            self._union = Item(self.id, core, foot, any(i.scalable for i in self.items), self.smax, self.smin)
        return self._union

    def _grid_cols(self) -> int:
        n = len(self.items)
        if self.columns:
            return max(1, min(n, self.columns))
        return {1: 1, 2: 2, 3: 3, 4: 2, 5: 3, 6: 3, 7: 4, 8: 4, 9: 3}.get(n, 4)

    def _cell(self, i: int, cols: int) -> tuple[int, int]:
        """The column and row of item `i`; a wrapped row snakes back, so each step stays next to the
        one before it (the flow turns the corner instead of jumping across)."""
        c, r = i % cols, i // cols
        return (cols - 1 - c if self.snake and r % 2 else c), r

    def _grid_dims(self, s: float) -> tuple[list[float], list[float]]:
        cols = self._grid_cols()
        rows = math.ceil(len(self.items) / cols)
        colw, rowh = [0.0] * cols, [0.0] * rows
        for i, it in enumerate(self.items):
            w, h = it.size(self.s(s))
            c, r = self._cell(i, cols)
            colw[c] = max(colw[c], w)
            rowh[r] = max(rowh[r], h)
        return colw, rowh

    def _cycle_radius(self, s: float) -> float:
        n = len(self.items)
        biggest = max(max(it.size(self.s(s))) for it in self.items)
        if n <= 1:
            return 0.0
        return (biggest + self.gap) / (2 * math.sin(math.pi / n))

    def size(self, s: float) -> tuple[float, float]:
        """The group's natural size at scale `s`, with the smallest gaps."""
        sizes = [it.size(self.s(s)) for it in self.items]
        n = len(sizes)
        if self.kind == "row":
            return sum(w for w, _ in sizes) + (n - 1) * self.gap, max(h for _, h in sizes)
        if self.kind == "column":
            return max(w for w, _ in sizes), sum(h for _, h in sizes) + (n - 1) * self.gap
        if self.kind == "grid":
            colw, rowh = self._grid_dims(s)
            return sum(colw) + (len(colw) - 1) * self.gap, sum(rowh) + (len(rowh) - 1) * self.gap
        if self.kind == "cycle" and n > 1:
            r = self._cycle_radius(s)
            mw = max(w for w, _ in sizes)
            mh = max(h for _, h in sizes)
            return 2 * r * 1.15 + mw, 2 * r * 0.85 + mh
        return self.union().size(self.s(s))

    # ---------------------------------------------------------------- placing

    def place(self, rect: Box, s: float) -> dict[str, Placed]:
        """Every item's core centre inside `rect` at scale `s` (centred, gaps spread when there is
        spare room). A free group moves and scales as one piece."""
        se = self.s(s)
        if self.kind == "row":
            return self._line(rect, se, horizontal=True)
        if self.kind == "column":
            return self._line(rect, se, horizontal=False)
        if self.kind == "grid":
            return self._grid(rect, se)
        if self.kind == "cycle" and len(self.items) > 1:
            return self._cycle(rect, se)
        u = self.union()
        k = u.k(se)
        ml, _, mt, _ = u.margins
        w, h = u.size(se)
        fx, fy = rect.cx - w / 2, rect.cy - h / 2  # the footprint's top left, centred
        ucx, ucy = (
            fx + ml + k * u.core.w / 2,
            fy + mt + k * u.core.h / 2,
        )  # where the union's core centre goes
        out: dict[str, Placed] = {}
        for it in self.items:
            kk = it.k(se)
            # Each item keeps its place relative to the union's core centre (scaled as one piece).
            out[it.id] = Placed(ucx + k * (it.core.cx - u.core.cx), ucy + k * (it.core.cy - u.core.cy), kk)
        return out

    def _line(self, rect: Box, s: float, horizontal: bool) -> dict[str, Placed]:
        sizes = [it.size(s) for it in self.items]
        n = len(self.items)
        main = [w if horizontal else h for w, h in sizes]
        room = rect.w if horizontal else rect.h
        gap = self.gap
        if n > 1:
            spare = room - sum(main) - (n - 1) * gap
            gap = gap + min(max(0.0, spare) / (n - 1), (MAX_GAP - 1) * self.gap)
        total = sum(main) + (n - 1) * gap
        pos = (rect.cx if horizontal else rect.cy) - total / 2
        out: dict[str, Placed] = {}
        # The cross axis: cores line up on one line (centre), or on one floor / ceiling.
        cross_lo = rect.y if horizontal else rect.x
        cross_hi = rect.bottom if horizontal else rect.right
        line = self._cross_line(cross_lo, cross_hi, s, horizontal)
        for it, length in zip(self.items, main, strict=True):
            ml, _, mt, _ = it.margins
            k = it.k(s)
            if horizontal:
                cx = pos + ml + k * it.core.w / 2
                half = k * it.core.h / 2
                cy = line + half if self.align == "top" else line - half if self.align == "bottom" else line
                out[it.id] = Placed(cx, cy, k)
            else:
                cy = pos + mt + k * it.core.h / 2
                half = k * it.core.w / 2
                cx = line + half if self.align == "left" else line - half if self.align == "right" else line
                out[it.id] = Placed(cx, cy, k)
            pos += length + gap
        return out

    def _cross_line(self, lo: float, hi: float, s: float, horizontal: bool) -> float:
        """The line the items' cores line up on, across the main axis: chosen so every footprint fits
        between `lo` and `hi`, as near the middle as possible."""
        mid = (lo + hi) / 2
        starts, ends = [], []  # each item's reach before and after the line
        for it in self.items:
            ml, mr, mt, mb = it.margins
            k = it.k(s)
            size, before, after = (k * it.core.h, mt, mb) if horizontal else (k * it.core.w, ml, mr)
            if self.align in (("top",) if horizontal else ("left",)):
                starts.append(before)
                ends.append(size + after)
            elif self.align in (("bottom",) if horizontal else ("right",)):
                starts.append(size + before)
                ends.append(after)
            else:
                starts.append(size / 2 + before)
                ends.append(size / 2 + after)
        reach_lo, reach_hi = max(starts), max(ends)
        # Centre the whole extent [line - reach_lo, line + reach_hi] in [lo, hi].
        line = mid + (reach_lo - reach_hi) / 2
        return line

    def _grid(self, rect: Box, s: float) -> dict[str, Placed]:
        colw, rowh = self._grid_dims(s)
        cols, rows = len(colw), len(rowh)
        gx = self.gap + (max(0.0, rect.w - sum(colw) - (cols - 1) * self.gap) / (cols - 1) if cols > 1 else 0)
        gy = self.gap + (max(0.0, rect.h - sum(rowh) - (rows - 1) * self.gap) / (rows - 1) if rows > 1 else 0)
        gx = min(gx, MAX_GAP * self.gap)
        gy = min(gy, MAX_GAP * self.gap)
        width = sum(colw) + (cols - 1) * gx
        height = sum(rowh) + (rows - 1) * gy
        x0, y0 = rect.cx - width / 2, rect.cy - height / 2
        out: dict[str, Placed] = {}
        for i, it in enumerate(self.items):
            c, r = self._cell(i, cols)
            # The last row, when shorter, is centred under the others (a snaking row starts where
            # the row above ended).
            in_row = min(cols, len(self.items) - r * cols)
            shift = (
                (sum(colw[in_row:]) + (cols - in_row) * gx) / 2 if in_row < cols and not self.snake else 0.0
            )
            cell_x = x0 + shift + sum(colw[:c]) + c * gx
            cell_y = y0 + sum(rowh[:r]) + r * gy
            ml, _, mt, _ = it.margins
            k = it.k(s)
            w, h = it.size(s)
            fx = cell_x + (colw[c] - w) / 2
            fy = cell_y + (rowh[r] - h) / 2
            out[it.id] = Placed(fx + ml + k * it.core.w / 2, fy + mt + k * it.core.h / 2, k)
        return out

    def _cycle(self, rect: Box, s: float) -> dict[str, Placed]:
        sizes = [it.size(s) for it in self.items]
        n = len(self.items)
        mw = max(w for w, _ in sizes)
        mh = max(h for _, h in sizes)
        need = self._cycle_radius(s)
        # An ellipse that fills the lane, never tighter than the items need, never very flat.
        ry = max(need * 0.75, (rect.h - mh) / 2)
        rx = min(max(need, (rect.w - mw) / 2), 1.8 * ry)
        out: dict[str, Placed] = {}
        for i, it in enumerate(self.items):
            a = -math.pi / 2 + 2 * math.pi * i / n
            fx = rect.cx + rx * math.cos(a)
            fy = rect.cy + ry * math.sin(a)
            ml, _, mt, _ = it.margins
            k = it.k(s)
            w, h = it.size(s)
            # (fx, fy) is the footprint's centre; the core sits inside it by the label margins.
            out[it.id] = Placed(fx - w / 2 + ml + k * it.core.w / 2, fy - h / 2 + mt + k * it.core.h / 2, k)
        return out


@dataclass
class Solved:
    """Every item's place, each group's lane and scale, and whether it all fitted."""

    placed: dict[str, Placed]
    lanes: dict[str, Box]
    scale: dict[str, float]
    fits: bool


def lanes_of(groups: list[Group]) -> list[list[Group]]:
    """Groups that are on stage together get separate lanes; a group that only comes after another
    has gone reuses its lane (in plan order: the first lane it can share)."""
    lanes: list[list[Group]] = []
    for g in groups:
        for lane in lanes:
            if not any(g.coexists(other) for other in lane):
                lane.append(g)
                break
        else:
            lanes.append([g])
    return lanes


def _wrap(group: Group, usable: Box) -> None:
    """A long row (or column) that does not fit across the region at its smallest readable scale is
    laid out in two rows (columns), in order."""
    n = len(group.items)
    if group.kind not in ("row", "column") or n < WRAP_FROM:
        return
    w, h = group.size(group.smin)
    if (w if group.kind == "row" else h) <= (usable.w if group.kind == "row" else usable.h):
        return
    group.columns = math.ceil(n / 2) if group.kind == "row" else 2
    group.snake = group.kind == "row"
    group.kind = "grid"


def solve_region(region: Box, groups: list[Group], direction: str = "column") -> Solved:
    """Lay out one region's groups: lanes stacked along `direction`, one common scale (each group
    within its own limits) as large as fits. Groups kept as drawn only keep clear of the region's
    edge; arranged groups also leave some room around them."""
    if all(g.kind == "free" for g in groups):
        usable = region.inset(EDGE_MARGIN, EDGE_MARGIN)
    else:
        usable = region.inset(region.w * (1 - FILL_W) / 2, region.h * (1 - FILL_H) / 2)
    for g in groups:
        _wrap(g, usable)
    lanes = lanes_of(groups)
    vertical = direction != "row"

    def lane_size(lane: list[Group], s: float) -> tuple[float, float]:
        sizes = [g.size(s) for g in lane]
        return max(w for w, _ in sizes), max(h for _, h in sizes)

    def fits(s: float) -> bool:
        sizes = [lane_size(lane, s) for lane in lanes]
        gaps = LANE_GAP * (len(lanes) - 1)
        if vertical:
            return (
                sum(h for _, h in sizes) + gaps <= usable.h + 0.5
                and max(w for w, _ in sizes) <= usable.w + 0.5
            )
        return (
            sum(w for w, _ in sizes) + gaps <= usable.w + 0.5 and max(h for _, h in sizes) <= usable.h + 0.5
        )

    # The largest scale that fits (each group within its own limits: never enlarged past its
    # maximum, never shrunk until its text is too small to read). When even the smallest does not
    # fit, it is reported (the engine then judges the result).
    top = max(g.smax for g in groups)
    ok = True
    if fits(top):
        s = top
    elif not fits(FLOOR):
        ok, s = False, FLOOR
    else:
        lo, hi = FLOOR, top
        for _ in range(40):
            mid = (lo + hi) / 2
            if fits(mid):
                lo = mid
            else:
                hi = mid
        s = lo
    sizes = [lane_size(lane, s) for lane in lanes]
    gaps = LANE_GAP * (len(lanes) - 1)
    extent = (sum(h for _, h in sizes) if vertical else sum(w for w, _ in sizes)) + gaps
    room = usable.h if vertical else usable.w
    # Spare room goes evenly between and around the lanes (centred as a whole).
    pad = max(0.0, room - extent) / (len(lanes) + 1)
    pos = (usable.y if vertical else usable.x) + pad
    placed: dict[str, Placed] = {}
    lane_boxes: dict[str, Box] = {}
    scale: dict[str, float] = {}
    if extent > room:
        pos = (usable.cy if vertical else usable.cx) - extent / 2
        pad = 0.0
    for lane, (lw, lh) in zip(lanes, sizes, strict=True):
        box = Box(usable.x, pos, usable.w, lh) if vertical else Box(pos, usable.y, lw, usable.h)
        for g in lane:
            lane_boxes[g.id] = box
            scale[g.id] = g.s(s)
            placed.update(g.place(box, s))
        pos += (lh if vertical else lw) + LANE_GAP + pad
    return Solved(placed, lane_boxes, scale, ok)
