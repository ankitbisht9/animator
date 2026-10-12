"""SVG drawings in (ADR-055): an admin's file, or a library source, as saved-object shapes.

Only drawing is taken: paths and the basic shapes (rect, circle, ellipse, line, polyline, polygon),
with their transforms, fill, stroke, stroke width, opacity and fill rule, inherited through groups.
Everything else is dropped (text, images, links to other files, scripts, filters, masks), and a file
with a DOCTYPE or entities is refused outright (no entity expansion, no external references).
Gradients become their middle color.

Names and colors (all optional):
- `data-part="left_ventricle"` (or a meaningful `id`) on a shape or group names its part;
- `data-fill="red.dark"`, `data-stroke="text"`: a color role, used instead of the drawn color, so
  library drawings follow the lesson's theme (light or dark);
- `data-anchor="apex"` on a circle marks a named point (not drawn);
- `data-skip` leaves an element out (guides while drawing).
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from animator.objects import art as A
from animator.objects.models import NAME_RE, RESERVED, paint_ok

MAX_SVG_BYTES = 600_000


@dataclass
class RawShape:
    segs: list[A.Seg]
    fill: str | None
    stroke: str | None
    stroke_width: float
    opacity: float
    evenodd: bool
    part: str | None


@dataclass
class Imported:
    shapes: list[RawShape] = field(default_factory=list)
    anchors: list[tuple[str, float, float]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


_NAMED = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000", "blue": "#0000ff",
    "yellow": "#ffff00", "orange": "#ffa500", "purple": "#800080", "pink": "#ffc0cb", "brown": "#a52a2a",
    "gray": "#808080", "grey": "#808080", "silver": "#c0c0c0", "navy": "#000080", "teal": "#008080",
    "maroon": "#800000", "olive": "#808000", "lime": "#00ff00", "aqua": "#00ffff", "cyan": "#00ffff",
    "fuchsia": "#ff00ff", "magenta": "#ff00ff", "gold": "#ffd700", "beige": "#f5f5dc", "tan": "#d2b48c",
    "khaki": "#f0e68c", "salmon": "#fa8072", "coral": "#ff7f50", "crimson": "#dc143c",
    "darkgreen": "#006400", "lightblue": "#add8e6", "skyblue": "#87ceeb", "darkblue": "#00008b",
    "lightgray": "#d3d3d3", "lightgrey": "#d3d3d3", "darkgray": "#a9a9a9", "darkgrey": "#a9a9a9",
    "darkred": "#8b0000", "lightgreen": "#90ee90", "violet": "#ee82ee", "indigo": "#4b0082",
    "turquoise": "#40e0d0", "chocolate": "#d2691e", "sienna": "#a0522d", "peru": "#cd853f",
    "tomato": "#ff6347", "orangered": "#ff4500", "steelblue": "#4682b4", "royalblue": "#4169e1",
    "forestgreen": "#228b22", "seagreen": "#2e8b57", "plum": "#dda0dd", "orchid": "#da70d6",
    "wheat": "#f5deb3", "ivory": "#fffff0", "lavender": "#e6e6fa", "mintcream": "#f5fffa",
}  # fmt: skip
_SKIP = {
    "defs", "clipPath", "mask", "symbol", "marker", "pattern", "linearGradient", "radialGradient",
    "filter", "metadata", "title", "desc", "style", "script", "foreignObject", "namedview",
}  # fmt: skip
_DROPPED = {"text": "text", "image": "images", "use": "reused elements (<use>)", "tspan": "text"}
_INHERITED = ("fill", "stroke", "stroke-width", "fill-rule", "data-fill", "data-stroke")
_DEFAULT_ID = re.compile(
    r"^(path|g|rect|circle|ellipse|polygon|polyline|line|layer|svg|defs|use|text|shape|group|object)[-_]?\d*$"
)
_TRANSFORM = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")
_NUMS = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _local(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _attr(el: ET.Element, name: str) -> str | None:
    for k, v in el.attrib.items():
        if _local(k) == name:
            return v
    return None


def parse_transform(text: str | None) -> A.Matrix:
    m: A.Matrix = A.IDENTITY
    if not text:
        return m
    for kind, args in _TRANSFORM.findall(text):
        v = [float(x) for x in _NUMS.findall(args)]
        if kind == "matrix" and len(v) == 6:
            t: A.Matrix = (v[0], v[1], v[2], v[3], v[4], v[5])
        elif kind == "translate" and v:
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif kind == "scale" and v:
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif kind == "rotate" and v:
            a = math.radians(v[0])
            r: A.Matrix = (math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0)
            if len(v) >= 3:
                t = A.multiply(A.multiply((1, 0, 0, 1, v[1], v[2]), r), (1, 0, 0, 1, -v[1], -v[2]))
            else:
                t = r
        elif kind == "skewX" and v:
            t = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif kind == "skewY" and v:
            t = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            continue
        m = A.multiply(m, t)
    return m


def parse_color(value: str | None, gradients: dict[str, str]) -> str | None:
    """A color as hex, `none`, or a role (`currentColor` is the text color); None when unknown."""
    if value is None:
        return None
    v = value.strip()
    low = v.lower()
    if low in ("none", "transparent"):
        return "none"
    if low == "currentcolor":
        return "text"
    if m := re.match(r"url\(\s*#([^)\s]+)\s*\)", v):
        return gradients.get(m.group(1), "#9aa4b2")
    if re.fullmatch(r"#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?", v):
        if len(v) == 4:
            v = "#" + "".join(c * 2 for c in v[1:])
        return v.lower()
    if m := re.fullmatch(r"rgba?\(([^)]*)\)", low):
        parts = re.split(r"[\s,/]+", m.group(1).strip())
        try:
            rgb = [round(float(p[:-1]) * 2.55) if p.endswith("%") else round(float(p)) for p in parts[:3]]
        except ValueError:
            return None
        if len(rgb) == 3:
            return "#" + "".join(f"{max(0, min(255, c)):02x}" for c in rgb)
    return _NAMED.get(low)


def _style(el: ET.Element) -> dict[str, str]:
    """Presentation attributes, then the `style` attribute's declarations (which win)."""
    out: dict[str, str] = {}
    for key in (
        "fill",
        "stroke",
        "stroke-width",
        "opacity",
        "fill-opacity",
        "stroke-opacity",
        "fill-rule",
        "display",
        "visibility",
        "stop-color",
    ):
        v = _attr(el, key)
        if v is not None:
            out[key] = v
    for decl in (_attr(el, "style") or "").split(";"):
        k, _, v = decl.partition(":")
        if k.strip() and v.strip():
            out[k.strip()] = v.strip()
    for key in ("data-fill", "data-stroke"):
        v = _attr(el, key)
        if v is not None:
            out[key] = v.strip()
    return out


def _num(el: ET.Element, name: str, default: float = 0.0) -> float:
    v = _attr(el, name)
    if v is None:
        return default
    m = _NUMS.match(v.strip())
    return float(m.group()) if m else default


def _points(text: str | None) -> list[tuple[float, float]]:
    v = [float(x) for x in _NUMS.findall(text or "")]
    return list(zip(v[0::2], v[1::2], strict=False))


def part_name(text: str | None) -> str | None:
    """A part name from an id or label ("LeftVentricle", "left-ventricle" -> left_ventricle), or None
    for a drawing program's default ids ("path123")."""
    if not text:
        return None
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text.strip())
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    if not s or _DEFAULT_ID.match(s) or s in RESERVED:
        return None
    if s[0].isdigit():
        s = "p_" + s
    s = s[:48].rstrip("_")
    return s if NAME_RE.match(s) else None


def _shape_path(tag: str, el: ET.Element) -> str | None:
    if tag == "path":
        return _attr(el, "d")
    if tag == "rect":
        x, y, w, h = _num(el, "x"), _num(el, "y"), _num(el, "width"), _num(el, "height")
        if w <= 0 or h <= 0:
            return None
        rx_attr, ry_attr = _attr(el, "rx"), _attr(el, "ry")
        rx = _num(el, "rx") if rx_attr is not None else (_num(el, "ry") if ry_attr is not None else 0.0)
        ry = _num(el, "ry") if ry_attr is not None else rx
        rx, ry = min(max(rx, 0.0), w / 2), min(max(ry, 0.0), h / 2)
        if rx <= 0 or ry <= 0:
            return f"M{x} {y}H{x + w}V{y + h}H{x}Z"
        return (
            f"M{x + rx} {y}H{x + w - rx}A{rx} {ry} 0 0 1 {x + w} {y + ry}V{y + h - ry}"
            f"A{rx} {ry} 0 0 1 {x + w - rx} {y + h}H{x + rx}A{rx} {ry} 0 0 1 {x} {y + h - ry}"
            f"V{y + ry}A{rx} {ry} 0 0 1 {x + rx} {y}Z"
        )
    if tag in ("circle", "ellipse"):
        cx, cy = _num(el, "cx"), _num(el, "cy")
        rx = _num(el, "r") if tag == "circle" else _num(el, "rx")
        ry = _num(el, "r") if tag == "circle" else _num(el, "ry")
        if rx <= 0 or ry <= 0:
            return None
        return f"M{cx - rx} {cy}A{rx} {ry} 0 1 0 {cx + rx} {cy}A{rx} {ry} 0 1 0 {cx - rx} {cy}Z"
    if tag == "line":
        return f"M{_num(el, 'x1')} {_num(el, 'y1')}L{_num(el, 'x2')} {_num(el, 'y2')}"
    if tag in ("polyline", "polygon"):
        pts = _points(_attr(el, "points"))
        if len(pts) < 2:
            return None
        d = "M" + "L".join(f"{x} {y}" for x, y in pts)
        return d + ("Z" if tag == "polygon" else "")
    return None


def _gradients(root: ET.Element) -> dict[str, str]:
    out: dict[str, str] = {}
    for el in root.iter():
        if _local(el.tag) not in ("linearGradient", "radialGradient"):
            continue
        gid = _attr(el, "id")
        stops = [s for s in el if _local(s.tag) == "stop"]
        if not gid or not stops:
            continue
        mid = stops[len(stops) // 2]
        color = parse_color(_style(mid).get("stop-color"), {})
        if color and color != "none":
            out[gid] = color
    return out


def parse_svg(text: str) -> Imported:
    """The drawing in an SVG document. Raises ValueError for anything that is not a safe SVG."""
    if len(text.encode("utf-8")) > MAX_SVG_BYTES:
        raise ValueError(f"the file is larger than {MAX_SVG_BYTES // 1000} KB")
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.IGNORECASE):
        raise ValueError("SVG files with a DOCTYPE or entities are not accepted")
    try:
        root = ET.fromstring(text)  # noqa: S314 (no DOCTYPE or entities: refused above)
    except ET.ParseError as e:
        raise ValueError(f"not a readable SVG file ({e})") from e
    if _local(root.tag) != "svg":
        raise ValueError("not an SVG file (its root is not <svg>)")
    out = Imported()
    gradients = _gradients(root)
    dropped: dict[str, int] = {}

    def walk(el: ET.Element, m: A.Matrix, inherited: dict[str, str], part: str | None) -> None:
        tag = _local(el.tag)
        if tag in _SKIP or _attr(el, "data-skip") is not None:
            return
        if tag in _DROPPED:
            dropped[_DROPPED[tag]] = dropped.get(_DROPPED[tag], 0) + 1
            return
        own = _style(el)
        if own.get("display") == "none" or own.get("visibility") == "hidden":
            return
        m = A.multiply(m, parse_transform(_attr(el, "transform")))
        style = {k: v for k, v in inherited.items() if k in _INHERITED}
        # Opacity multiplies down the tree.
        style["opacity"] = str(float(inherited.get("opacity", "1")) * _float(own.get("opacity"), 1.0))
        style.update({k: v for k, v in own.items() if k != "opacity"})
        name = part_name(_attr(el, "data-part")) or part_name(_attr(el, "id")) or part
        anchor = _attr(el, "data-anchor")
        if anchor is not None:
            if tag in ("circle", "ellipse") and (aname := part_name(anchor)):
                out.anchors.append((aname, *A.apply(m, _num(el, "cx"), _num(el, "cy"))))
            return
        if tag in ("svg", "g", "a", "switch"):
            if tag == "svg" and el is not root:
                m = A.multiply(m, (1, 0, 0, 1, _num(el, "x"), _num(el, "y")))
            for child in el:
                walk(child, m, style, name)
            return
        d = _shape_path(tag, el)
        if d is None:
            if tag not in ("svg", "g"):
                dropped[tag] = dropped.get(tag, 0) + 1
            return
        try:
            segs = A.transform(A.parse_path(d), m)
        except A.PathError as e:
            out.warnings.append(f"a {tag} was left out: {e}")
            return
        fill = style.get("data-fill") if paint_ok(style.get("data-fill")) else None
        fill = fill or parse_color(style.get("fill", "#000000"), gradients) or "#000000"
        if tag in ("line",):
            fill = "none"
        stroke = style.get("data-stroke") if paint_ok(style.get("data-stroke")) else None
        stroke = stroke or parse_color(style.get("stroke", "none"), gradients) or "none"
        width = _float(style.get("stroke-width"), 1.0) * A.scale_of(m) if stroke != "none" else 0.0
        opacity = float(style.get("opacity", "1"))
        if fill != "none":
            opacity *= _float(style.get("fill-opacity"), 1.0)
        elif stroke != "none":
            opacity *= _float(style.get("stroke-opacity"), 1.0)
        if fill == "none" and (stroke == "none" or width <= 0):
            return  # paints nothing
        out.shapes.append(
            RawShape(
                segs=segs,
                fill=fill,
                stroke=stroke,
                stroke_width=width,
                opacity=max(0.0, min(1.0, opacity)),
                evenodd=style.get("fill-rule") == "evenodd",
                part=name,
            )
        )

    walk(root, A.IDENTITY, {"opacity": "1"}, None)
    for what, n in sorted(dropped.items()):
        out.warnings.append(f"{n} {what} left out (only shapes are imported)")
    if not out.shapes:
        raise ValueError("the file has no shapes to import")
    return out


def _float(v: str | None, default: float) -> float:
    if v is None:
        return default
    m = _NUMS.match(v.strip())
    if not m:
        return default
    x = float(m.group())
    return x / 100 if v.strip().endswith("%") else x
