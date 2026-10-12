"""Making and checking a saved drawing without a model (ADR-055).

`to_art` turns imported shapes into the stored form: fitted to a box whose long side is 400 units,
path data written compactly. `with_anchors` gives each part a point on what is visible of it (where a
label's leader line ends). `check` runs the checks every drawing must pass before a lesson may use it,
and scores it (the review queue is sorted by the score):
- every shape is valid path data, painted with known colors;
- the drawing fills its box (no stray shape far away, no empty margins that would push labels off);
- every named part can be seen (not hidden under other shapes) and has its anchor on it;
- sensible proportions and enough detail to be worth keeping.
"""

from __future__ import annotations

from collections.abc import Sequence

from animator.objects import art as A
from animator.objects.models import Anchor, Art, Check, Quality, SavedObject, Shape
from animator.objects.svg import RawShape

LONG_SIDE = 400.0
PAD = 4.0
MIN_VISIBLE = 0.15  # a part mostly hidden under others is no use to point at


def to_art(
    shapes: Sequence[RawShape], anchors: Sequence[tuple[str, float, float]] = (), digits: int = 1
) -> Art:
    """Shapes fitted to a box with a 400-unit long side (strokes included), written compactly."""
    boxes = []
    for s in shapes:
        b = A.bounds(s.segs)
        if b is not None:
            boxes.append(b.grow(s.stroke_width / 2))
    if not boxes:
        raise ValueError("nothing is drawn")
    box = boxes[0]
    for b in boxes[1:]:
        box = box.union(b)
    side = max(box.w, box.h)
    if side <= 0:
        raise ValueError("the drawing has no size")
    k = (LONG_SIDE - 2 * PAD) / side
    m: A.Matrix = (k, 0.0, 0.0, k, PAD - box.x0 * k, PAD - box.y0 * k)
    out: list[Shape] = []
    for s in shapes:
        stroke = s.stroke if s.stroke not in (None, "none") and s.stroke_width > 0 else None
        out.append(
            Shape(
                d=A.serialize(A.transform(s.segs, m), digits),
                fill=s.fill or "none",
                stroke=stroke,
                strokeWidth=round(s.stroke_width * k, 2) if stroke else None,
                opacity=round(s.opacity, 3) if s.opacity < 0.999 else None,
                fillRule="evenodd" if s.evenodd else None,
                part=s.part,
            )
        )
    named = []
    for name, x, y in anchors:
        ax, ay = A.apply(m, x, y)
        named.append(Anchor(name=name, x=round(ax, 1), y=round(ay, 1)))
    return Art(w=round(box.w * k + 2 * PAD, 1), h=round(box.h * k + 2 * PAD, 1), shapes=out, anchors=named)


def _geoms(art: Art) -> list[A.Geom | None]:
    out: list[A.Geom | None] = []
    for s in art.shapes:
        filled = s.fill not in (None, "none")
        stroke = (s.strokeWidth or 2.0) if s.stroke not in (None, "none") else 0.0
        out.append(
            A.geom_of(s.d, filled, stroke, s.fillRule == "evenodd", (s.opacity or 1.0) >= 0.6, tolerance=0.75)
        )
    return out


def spots(art: Art) -> dict[str, A.Spot | None]:
    """Each part's point on what is visible of it."""
    geoms = _geoms(art)
    usable = [g for g in geoms if g is not None]
    owners = [s.part for s, g in zip(art.shapes, geoms, strict=True) if g is not None]
    return {p: A.interior_point(usable, owners, p) for p in art.parts}


def with_anchors(art: Art) -> tuple[Art, dict[str, A.Spot | None]]:
    """The art with an anchor on each part (named points the author placed are kept as they are)."""
    found = spots(art)
    named = {a.name for a in art.anchors}
    anchors = list(art.anchors)
    for part, spot in found.items():
        if spot is not None and part not in named:
            anchors.append(Anchor(name=part, x=round(spot.x, 1), y=round(spot.y, 1)))
    return art.model_copy(update={"anchors": anchors}), found


def check(obj: SavedObject, found: dict[str, A.Spot | None] | None = None) -> Quality:
    art = obj.art
    found = spots(art) if found is None else found
    checks: list[Check] = []
    geoms = _geoms(art)
    bad = [i for i, g in enumerate(geoms) if g is None]
    checks.append(
        Check(name="paths", ok=not bad, detail=f"shapes {bad[:5]} are not valid path data" if bad else "")
    )
    boxes = [
        g.box.grow((s.strokeWidth or 0) / 2) for s, g in zip(art.shapes, geoms, strict=True) if g is not None
    ]
    if boxes:
        box = boxes[0]
        for b in boxes[1:]:
            box = box.union(b)
        over = max(-box.x0, -box.y0, box.x1 - art.w, box.y1 - art.h) / max(art.w, art.h)
        fill = min(box.w / art.w, box.h / art.h)
        checks.append(
            Check(
                name="fits",
                ok=over <= 0.02 and fill >= 0.85,
                detail=""
                if over <= 0.02 and fill >= 0.85
                else f"overflow {over:.0%}, fills {fill:.0%} of its box",
            )
        )
    hidden = [p for p, s in found.items() if s is None or s.visible < MIN_VISIBLE]
    checks.append(
        Check(
            name="parts_visible",
            ok=not hidden,
            detail=f"hidden or empty: {', '.join(hidden)}" if hidden else f"{len(found)} parts",
        )
    )
    outside = [a.name for a in art.anchors if not (0 <= a.x <= art.w and 0 <= a.y <= art.h)]
    checks.append(Check(name="anchors_inside", ok=not outside, detail=", ".join(outside)))
    off = [a.name for a in art.anchors if a.name in found and not _on_part(art, geoms, a.name, a.x, a.y)]
    checks.append(
        Check(
            name="anchors_on_parts",
            ok=not off,
            detail=f"not on what is visible of: {', '.join(off)}" if off else "",
        )
    )
    aspect = art.h / art.w
    checks.append(Check(name="proportions", ok=0.2 <= aspect <= 5, detail=f"height = {aspect:.2f} x width"))
    painted = any(s.fill not in (None, "none") for s in art.shapes)
    checks.append(Check(name="painted", ok=painted, detail="" if painted else "only lines, nothing filled"))
    segments = sum(len(A.parse_path(s.d)) for s, g in zip(art.shapes, geoms, strict=True) if g is not None)
    detail = len(art.shapes) >= 3 or segments >= 8
    checks.append(Check(name="detail", ok=detail, detail=f"{len(art.shapes)} shapes"))
    unnamed = [p for p in art.parts if not obj.parts.get(p)]
    checks.append(
        Check(
            name="named_parts",
            ok=not unnamed,
            detail=f"no label for: {', '.join(unnamed)}" if unnamed else "",
        )
    )
    chars = sum(len(s.d) for s in art.shapes)
    checks.append(Check(name="compact", ok=chars <= 60_000, detail=f"{chars:,} characters of path data"))
    passed = sum(c.ok for c in checks) / len(checks)
    richness = min(1.0, len(art.shapes) / 14) * 0.5 + min(1.0, len(art.parts) / 5) * 0.5
    score = round(0.7 * passed + 0.3 * richness, 3)
    return Quality(score=score, checks=checks)


def _on_part(
    art: Art, geoms: list[A.Geom | None], part: str, x: float, y: float, tolerance: float = 3.0
) -> bool:
    """Is a part's anchor on the part: on what can be seen of it, inside it (a nucleus under its
    protons), or within a few units of its lines?"""
    mine = [g for s, g in zip(art.shapes, geoms, strict=True) if g is not None and s.part == part]
    if any(g.covers(x, y) for g in mine):
        return True
    return any(g.clearance(x, y) <= tolerance + g.stroke / 2 for g in mine)
