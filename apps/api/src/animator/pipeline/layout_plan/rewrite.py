"""Applying a solved layout to the scene (ADR-047): every coordinate of the scene, rewritten.

Each item moves and scales as one piece: a similarity transform `p -> c' + k (p - c)` (c: its core's
centre as drawn, c': where the solver put it, k: its scale). Applied to its objects' positions,
scales, moves and keyframes, it draws the same item, moving the same way, in its new place. What lies
outside items is mapped point by point: an arrow's ends go with the items they touch, a ball thrown
into a basket lands in the basket wherever the basket now is, a label placed by coordinates keeps
its distance. Positions placed against another object (relativeTo, an anchor) follow that object by
themselves; only their spacing is scaled with the item.

The engine's frames: a child's place is given in world coordinates and converted to its parent's
frame, so a child of a moved group is mapped like any point and keeps its own scale (it inherits the
group's). An object drawn from its endpoints draws them in its parent's frame: a child's endpoints
are left alone.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from animator.pipeline.layout_plan.arrange import Placed
from animator.pipeline.layout_plan.figures import POINT_PARAMS, TEXT_TYPES, WORLD_SPACE, Measured
from animator.pipeline.layout_plan.regions import Box, content_regions, default_region, regions, slot_box

NEAR = 120.0  # a move ending this close to an item lands with it
EDGE = 16.0  # a point this close to an item's footprint counts as inside it
TOUCH = 32.0  # a connector end this close to an item is attached to it


@dataclass(frozen=True)
class Similarity:
    cx: float
    cy: float
    nx: float
    ny: float
    k: float

    def __call__(self, x: float, y: float) -> tuple[float, float]:
        return self.nx + self.k * (x - self.cx), self.ny + self.k * (y - self.cy)

    @property
    def identity(self) -> bool:
        return abs(self.k - 1) < 1e-9 and abs(self.nx - self.cx) < 1e-9 and abs(self.ny - self.cy) < 1e-9

    def then(self, c: Similarity) -> Similarity:
        """This transform followed by `c`: c(self(p))."""
        nx, ny = c(self.nx, self.ny)
        return Similarity(self.cx, self.cy, nx, ny, self.k * c.k)


def fit_into(env: Box, room: Box) -> Similarity | None:
    """The smallest correction that brings `env` inside `room`: shrink about its centre if it is too
    big, then shift it in. None when it already fits."""
    k = min(1.0, room.w / max(1.0, env.w), room.h / max(1.0, env.h))
    w, h = env.w * k, env.h * k
    x0 = env.cx - w / 2
    y0 = env.cy - h / 2
    x0 = min(max(x0, room.x), room.right - w)
    y0 = min(max(y0, room.y), room.bottom - h)
    nx, ny = x0 + w / 2, y0 + h / 2
    if k > 0.999 and abs(nx - env.cx) < 0.5 and abs(ny - env.cy) < 0.5:
        return None
    return Similarity(env.cx, env.cy, nx, ny, k)


IDENTITY = Similarity(0, 0, 0, 0, 1)


def _r(v: float) -> float:
    return round(float(v), 1)


class Rewriter:
    def __init__(self, m: Measured, placed: dict[str, Placed]) -> None:
        self.m = m
        self.t: dict[str, Similarity] = {}
        for fid, fig in m.figures.items():
            p = placed.get(fid)
            if p is None or fig.core is None:
                continue
            self.t[fid] = Similarity(fig.core.cx, fig.core.cy, p.cx, p.cy, p.k)
        self.feet = {fid: m.figures[fid].foot for fid in self.t}
        self.layout = m.layout
        self.region_of = {it.id: region for region, gs in m.groups.items() for g in gs for it in g.items}

    def correct(self, region: str, c: Similarity) -> None:
        """Apply a correction to everything placed in `region` (it moves and scales as one piece)."""
        for fid, t in list(self.t.items()):
            if self.region_of.get(fid) == region:
                self.t[fid] = t.then(c)

    def owner(self, oid: str) -> str | None:
        """The item an object moves with: its own, the one it is attached to, or, for anything the
        plan does not name, the item it lies in or nearest to (in the region it was drawn in)."""
        root = self._root(oid)
        fid = self.m.member_of.get(root) or self.m.attached_to.get(root)
        if fid is not None:
            return fid if fid in self.t else None
        if root in self.m.fixed:
            return None
        x, y = self._home_point(root)
        return self.nearest(x, y)

    def _factor(self, fid: str, t: Similarity) -> float:
        """An item's objects scale with it (a text-only item's scale is already limited by the solver:
        never enlarged, never shrunk below readable)."""
        return t.k

    # ---------------------------------------------------------------- mapping

    def nearest(self, x: float, y: float) -> str | None:
        """The item whose footprint holds the point (the smallest), else the nearest one, among the
        items of the region the point is in (all items when that region has none)."""
        region = next((n for n, r in content_regions(self.layout).items() if r.contains(x, y)), None)
        pool = {f: b for f, b in self.feet.items() if b is not None and self.region_of.get(f) == region}
        pool = pool or {f: b for f, b in self.feet.items() if b is not None}
        inside = [(b.area, f) for f, b in pool.items() if b.contains(x, y, EDGE)]
        if inside:
            return min(inside)[1]
        near = [(b.distance(x, y), f) for f, b in pool.items()]
        return min(near)[1] if near else None

    def touching(self, x: float, y: float, reach: float) -> str | None:
        """The item a point is on or right next to (within `reach`), if any."""
        hits = [(b.area, f) for f, b in self.feet.items() if b is not None and b.contains(x, y, reach)]
        return min(hits)[1] if hits else None

    def satellite(self, fid: str | None, x: float, y: float) -> tuple[float, float]:
        """A point that goes with item `fid`: inside its footprint it moves and scales with it;
        outside, it keeps its distance from the footprint's edge (a label or an arrow beside an item
        stays beside it, at its distance, however much the item grows)."""
        if fid is None or fid not in self.t:
            return x, y
        t, foot = self.t[fid], self.feet.get(fid)
        if foot is None:
            return t(x, y)
        qx, qy = min(max(x, foot.x), foot.right), min(max(y, foot.y), foot.bottom)
        nx, ny = t(qx, qy)
        k = min(1.0, t.k)
        return nx + k * (x - qx), ny + k * (y - qy)

    def map_end(self, x: float, y: float, own: str | None) -> tuple[float, float]:
        """An end of a connector, or what a pointer aims at: it goes with the item it touches,
        otherwise with the object's own item."""
        return self.satellite(self.touching(x, y, TOUCH) or own, x, y)

    def map_destination(self, x: float, y: float, own: str | None) -> tuple[float, float]:
        """Where a move ends: in (or next to) an item, it lands there wherever that item now is;
        anywhere else it keeps its place relative to the mover's own item."""
        return self.satellite(self.touching(x, y, EDGE) or self.touching(x, y, NEAR) or own, x, y)

    def transform_of(self, oid: str) -> tuple[Similarity, float, str | None, bool]:
        """An object's item transform, the factor its own scale gets, the item it goes with, and
        whether it is part of that item (else a satellite of it). Children go with their top-level
        ancestor."""
        root = self._root(oid)
        fid = self.m.member_of.get(root)
        if fid is not None and fid in self.t:
            return self.t[fid], self._factor(fid, self.t[fid]), fid, True
        if root in self.m.attached_to or root in self.m.fixed:
            return IDENTITY, 1.0, None, False
        near = self.owner(root)
        if near is None:
            return IDENTITY, 1.0, None, False
        t = self.t[near]
        text = self.m.objects.get(root, {}).get("type") in TEXT_TYPES
        return t, (1.0 if text else min(1.0, self._factor(near, t))), near, False

    def mapper(
        self, oid: str
    ) -> tuple[Callable[[float, float], tuple[float, float]], Similarity, float, str | None]:
        """How an object's own points map (part of an item: with it; a satellite: keeping its
        distance), its item transform, the factor for its scale, and its item."""
        t, factor, fid, member = self.transform_of(oid)
        if member:
            return t, t, factor, fid
        return (lambda x, y: self.satellite(fid, x, y)), t, factor, fid

    def _root(self, oid: str) -> str:
        seen = set()
        while True:
            parent = self.m.objects.get(oid, {}).get("parent")
            if not parent or parent not in self.m.objects or parent in seen:
                return oid
            seen.add(oid)
            oid = parent

    def _home_point(self, oid: str) -> tuple[float, float]:
        if oid in self.m.home:
            b = self.m.home[oid]
            return b.cx, b.cy
        o = self.m.objects.get(oid, {})
        return self.place_point(o.get("place"))

    def place_point(self, place: Any) -> tuple[float, float]:
        """The world point a slot, raw or missing placement puts the object's anchor on."""
        if isinstance(place, dict) and place.get("x") is not None and place.get("y") is not None:
            return float(place["x"]), float(place["y"])
        if isinstance(place, dict) and place.get("slot"):
            box = slot_box(self.layout, str(place["slot"]))
            off = place.get("offset") or {}
            return box.cx + float(off.get("x") or 0), box.cy + float(off.get("y") or 0)
        box = regions(self.layout)[default_region(self.layout)]
        return box.cx, box.cy

    # ---------------------------------------------------------------- the scene

    def rewrite(self, dsl: dict[str, Any], starts: dict[str, tuple[float, float]]) -> dict[str, Any]:
        """The scene with the layout applied. `starts`: for each `move ... by` action id, where its
        object's centre is when the move starts (as drawn)."""
        out = copy.deepcopy(dsl)
        objects = {str(o.get("id")): o for o in out.get("objects") or []}
        for oid, o in objects.items():
            if oid in self.m.fixed:
                continue
            self._object(oid, o)
        for a in out.get("timeline") or []:
            target = str(a.get("target") or "")
            if target in objects and target not in self.m.fixed:
                self._action(a, target, starts)
        return out

    def _same_item(self, oid: str, ref: str | None) -> bool:
        fid = self.m.member_of.get(oid)
        return fid is not None and ref is not None and self.m.member_of.get(ref) == fid

    def _relative(self, oid: str, place: dict[str, Any], k: float) -> None:
        """A placement against another object: it follows by itself; inside one item, its spacing
        scales with the item."""
        ref = place.get("relativeTo") or (str(place.get("at")).split(".")[0] if place.get("at") else None)
        if not self._same_item(oid, ref) or abs(k - 1) < 1e-9:
            return
        if place.get("relativeTo") and place.get("side") not in ("center",):
            gap = place.get("gap")
            if gap is None:
                gap = 16 if str(place.get("side", "")).startswith("inside") else 24
            place["gap"] = _r(float(gap) * k)
        off = place.get("offset")
        if isinstance(off, dict):
            place["offset"] = {"x": _r(float(off.get("x") or 0) * k), "y": _r(float(off.get("y") or 0) * k)}

    def _object(self, oid: str, o: dict[str, Any]) -> None:
        own, t, factor, fid = self.mapper(oid)
        kind = str(o.get("type"))
        child = bool(o.get("parent")) and o.get("parent") in self.m.objects
        place = o.get("place")
        member = self.m.member_of.get(self._root(oid)) in self.t
        if kind in WORLD_SPACE:
            if not child:
                params = o.get("params") or {}
                for key in POINT_PARAMS.get(kind, ()):
                    p = params.get(key)
                    if isinstance(p, dict) and p.get("x") is not None and p.get("y") is not None:
                        x, y = (
                            t(float(p["x"]), float(p["y"]))
                            if member
                            else self.map_end(float(p["x"]), float(p["y"]), fid)
                        )
                        params[key] = {"x": _r(x), "y": _r(y)}
            return
        if fid is None:
            self._points(o, fid)
            return
        if isinstance(place, dict) and (place.get("relativeTo") or place.get("at")):
            self._relative(oid, place, t.k)
        else:
            x, y = self.place_point(place)
            nx, ny = own(x, y)
            anchor = place.get("anchor") if isinstance(place, dict) else None
            o["place"] = {"x": _r(nx), "y": _r(ny), **({"anchor": anchor} if anchor else {})}
        if not child and abs(factor - 1) > 1e-9:
            scale = o.get("scale")
            o["scale"] = round((float(scale) if isinstance(scale, (int, float)) else 1.0) * factor, 4)
        self._points(o, fid)

    def _points(self, o: dict[str, Any], fid: str | None) -> None:
        """Points a label or bubble aims at (raw coordinates): with the item they touch."""
        params = o.get("params") or {}
        for key in POINT_PARAMS.get(str(o.get("type")), ()):
            p = params.get(key)
            if isinstance(p, dict) and p.get("x") is not None and p.get("y") is not None:
                x, y = self.map_end(float(p["x"]), float(p["y"]), fid)
                params[key] = {"x": _r(x), "y": _r(y)}

    def _action(self, a: dict[str, Any], target: str, starts: dict[str, tuple[float, float]]) -> None:
        o = self.m.objects.get(target, {})
        if o.get("type") in WORLD_SPACE:
            return
        _, t, factor, fid = self.mapper(target)
        if fid is None:
            return
        child = bool(o.get("parent")) and o.get("parent") in self.m.objects
        kind = a.get("action")
        if kind == "move":
            to = a.get("to")
            if isinstance(to, dict) and (to.get("relativeTo") or to.get("at")):
                self._relative(target, to, t.k)
            elif isinstance(to, dict):
                x, y = self.place_point(to)
                nx, ny = self.map_destination(x, y, fid)
                anchor = to.get("anchor")
                a["to"] = {"x": _r(nx), "y": _r(ny), **({"anchor": anchor} if anchor else {})}
            elif isinstance(a.get("by"), dict) and not child:
                by = a["by"]
                bx, by_ = float(by.get("x") or 0), float(by.get("y") or 0)
                sx, sy = starts.get(str(a.get("id")), self._home_point(target))
                x0, y0 = self.map_destination(sx, sy, fid)
                x1, y1 = self.map_destination(sx + bx, sy + by_, fid)
                a["by"] = {"x": _r(x1 - x0), "y": _r(y1 - y0)}
            if a.get("arcHeight") is not None:
                a["arcHeight"] = _r(float(a["arcHeight"]) * t.k)
        elif kind == "animate" and not child:
            prop = str(a.get("property"))
            for kf in a.get("keyframes") or []:
                v = kf.get("value")
                if not isinstance(v, (int, float)):
                    continue
                if prop == "x":
                    kf["value"] = _r(t.nx + t.k * (float(v) - t.cx))
                elif prop == "y":
                    kf["value"] = _r(t.ny + t.k * (float(v) - t.cy))
                elif prop == "scale":
                    kf["value"] = round(float(v) * factor, 4)
        elif kind == "scale" and not child and a.get("to") is not None:
            a["to"] = round(float(a["to"]) * factor, 4)
        elif kind == "follow" and isinstance(a.get("offset"), dict):
            off = a["offset"]
            a["offset"] = {"x": _r(float(off.get("x") or 0) * t.k), "y": _r(float(off.get("y") or 0) * t.k)}


def footprint(b: Box | None) -> dict[str, float] | None:
    return None if b is None else {"x": _r(b.x), "y": _r(b.y), "w": _r(b.w), "h": _r(b.h)}
