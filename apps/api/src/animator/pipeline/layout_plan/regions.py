"""The fixed frame (ADR-047): the 1600 x 900 canvas, each layout's regions and named slots, exactly as
the engine defines them (packages/engine/src/layout/templates.ts). The planner places things inside
these regions; the player and the export scale the whole canvas, so a layout planned here is right
at every screen size, full screen included."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    def union(self, other: Box) -> Box:
        x0, y0 = min(self.x, other.x), min(self.y, other.y)
        x1, y1 = max(self.right, other.right), max(self.bottom, other.bottom)
        return Box(x0, y0, x1 - x0, y1 - y0)

    def overlap(self, other: Box) -> float:
        """The area the two boxes share."""
        w = min(self.right, other.right) - max(self.x, other.x)
        h = min(self.bottom, other.bottom) - max(self.y, other.y)
        return w * h if w > 0 and h > 0 else 0.0

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        return self.x - margin <= x <= self.right + margin and self.y - margin <= y <= self.bottom + margin

    def distance(self, x: float, y: float) -> float:
        dx = max(self.x - x, 0.0, x - self.right)
        dy = max(self.y - y, 0.0, y - self.bottom)
        return float((dx * dx + dy * dy) ** 0.5)

    def inset(self, dx: float, dy: float) -> Box:
        return Box(self.x + dx, self.y + dy, self.w - 2 * dx, self.h - 2 * dy)

    @staticmethod
    def of(b: dict[str, float]) -> Box:
        return Box(float(b["x"]), float(b["y"]), float(b["w"]), float(b["h"]))


CANVAS = Box(0, 0, 1600, 900)
_MX = 48.0
_TITLE = Box(_MX, 26, 1600 - 2 * _MX, 78)
CAPTION_BAR = Box(150, 752, 1300, 116)
_STAGE_Y, _STAGE_H = 116.0, 620.0

REGIONS: dict[str, dict[str, Box]] = {
    "stage_with_caption": {
        "title_bar": _TITLE,
        "stage": Box(_MX, _STAGE_Y, 1600 - 2 * _MX, _STAGE_H),
        "caption_bar": CAPTION_BAR,
    },
    "stage_with_equation": {
        "title_bar": _TITLE,
        "stage": Box(_MX, _STAGE_Y, 940, _STAGE_H),
        "equation_panel": Box(1012, _STAGE_Y, 1600 - 1012 - _MX, _STAGE_H),
        "caption_bar": CAPTION_BAR,
    },
    "split": {
        "title_bar": _TITLE,
        "left": Box(_MX, _STAGE_Y, 736, _STAGE_H),
        "right": Box(1600 - _MX - 736, _STAGE_Y, 736, _STAGE_H),
        "caption_bar": CAPTION_BAR,
    },
    "title_card": {"hero": Box(150, 150, 1300, 520), "caption_bar": CAPTION_BAR},
    "no_title": {"stage": Box(_MX, 36, 1600 - 2 * _MX, 700), "caption_bar": CAPTION_BAR},
}

# Regions things may be planned into (the bars belong to the title and the captions).
CONTENT = ("stage", "equation_panel", "left", "right", "hero")

_SUBSLOTS = (
    "center",
    "top",
    "bottom",
    "left",
    "right",
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
)


def regions(layout: str | None) -> dict[str, Box]:
    return REGIONS.get(layout or "stage_with_caption", REGIONS["stage_with_caption"])


def content_regions(layout: str | None) -> dict[str, Box]:
    return {k: v for k, v in regions(layout).items() if k in CONTENT}


def default_region(layout: str | None) -> str:
    """Where an unplaced object goes, and the region a plan group gets when it names none."""
    found = content_regions(layout)
    for name in ("stage", "hero", "left"):
        if name in found:
            return name
    return next(iter(found))


def _sub(r: Box, sub: str) -> Box:
    cw, ch = r.w / 3, r.h / 3
    col = 0 if "left" in sub else 2 if "right" in sub else 1
    row = 0 if "top" in sub else 2 if "bottom" in sub else 1
    spans_x = sub in ("top", "bottom")
    spans_y = sub in ("left", "right")
    return Box(
        r.x if spans_x else r.x + col * cw,
        r.y if spans_y else r.y + row * ch,
        r.w if spans_x else cw,
        r.h if spans_y else ch,
    )


def slot_box(layout: str | None, slot: str) -> Box:
    """A named slot's rectangle; an unknown slot is the default region (as in the engine)."""
    regs = regions(layout)
    if slot in regs:
        return regs[slot]
    name, _, sub = slot.partition(".")
    if name in regs and name not in ("title_bar", "caption_bar") and sub in _SUBSLOTS:
        return _sub(regs[name], sub)
    return regs[default_region(layout)]
