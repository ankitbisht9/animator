"""Vendor line icons from Lucide (lucide-static, ISC License) into the components package.

Usage: python scripts/vendor_icons.py <path to an unpacked lucide-static package>
(`npm pack lucide-static` and untar it anywhere). Writes
packages/components/src/renderers/icons-lucide.ts: one SVG path per icon on the 24x24 grid the
existing icons use. The engine's list of icon names (packages/engine/src/catalog/annotation.ts)
must name the same icons.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "packages" / "components" / "src" / "renderers" / "icons-lucide.ts"

# DSL name -> Lucide name. Picked for lessons: people and the body, nature, animals, science, technology,
# everyday things. (The hand-drawn set in icons.tsx keeps its own names.)
ICONS = {
    # people and the body
    "person": "user",
    "people": "users",
    "person_standing": "person-standing",
    "baby": "baby",
    "hand": "hand",
    "heart": "heart",
    "brain": "brain",
    "eye": "eye",
    "ear": "ear",
    "bone": "bone",
    "pill": "pill",
    "stethoscope": "stethoscope",
    # nature
    "sun": "sun",
    "moon": "moon",
    "cloud": "cloud",
    "rain": "cloud-rain",
    "snowflake": "snowflake",
    "flame": "flame",
    "leaf": "leaf",
    "sprout": "sprout",
    "tree": "tree-deciduous",
    "flower": "flower-2",
    "mountain": "mountain",
    "waves": "waves-horizontal",
    "wind": "wind",
    "earth": "earth",
    "globe": "globe",
    # animals
    "fish": "fish",
    "bird": "bird",
    "dog": "dog",
    "cat": "cat",
    "bug": "bug",
    # science
    "atom": "atom",
    "dna": "dna",
    "microscope": "microscope",
    "telescope": "telescope",
    "flask": "flask-conical",
    "thermometer": "thermometer",
    "magnet": "magnet",
    "bolt": "zap",
    "battery": "battery",
    "gear": "cog",
    "rocket": "rocket",
    "ruler": "ruler",
    "calculator": "calculator",
    # technology
    "laptop": "laptop",
    "phone": "smartphone",
    "server": "server",
    "database": "database",
    "chip": "cpu",
    "code": "code",
    "robot": "bot",
    "wifi": "wifi",
    "network": "network",
    "link": "link",
    "file": "file-text",
    "message": "message-circle",
    "mail": "mail",
    "lock": "lock",
    "key": "key",
    # everyday
    "home": "house",
    "school": "school",
    "graduation": "graduation-cap",
    "car": "car",
    "bike": "bike",
    "factory": "factory",
    "apple": "apple",
    "coins": "coins",
    "cart": "shopping-cart",
    "trophy": "trophy",
    "calendar": "calendar",
    "map_pin": "map-pin",
    "flag": "flag",
    "bell": "bell",
    "camera": "camera",
    "music": "music",
    "gift": "gift",
    "wrench": "wrench",
    "hammer": "hammer",
    "recycle": "recycle",
}

# Small edits for large drawings (ISC allows modification): the standing figure's head is a dot.
TWEAKS = {"person-standing": {("circle", "r"): "2"}}


def _n(v: str) -> float:
    return float(v)


_ARGS = {"m": 2, "l": 2, "h": 1, "v": 1, "c": 6, "s": 4, "q": 4, "t": 2, "a": 7, "z": 0}


def normalize_path(d: str) -> str:
    """Path data with every number separated (SVG allows packed arc flags like "a1 1 0 00-1 1"; the
    engine's strict parser does not)."""
    out: list[str] = []
    i, n = 0, len(d)
    cmd = ""
    arg = 0

    def number() -> str:
        nonlocal i
        j = i
        if j < n and d[j] in "+-":
            j += 1
        dot = False
        while j < n and (d[j].isdigit() or (d[j] == "." and not dot)):
            dot = dot or d[j] == "."
            j += 1
        if j < n and d[j] in "eE":
            j += 1
            if j < n and d[j] in "+-":
                j += 1
            while j < n and d[j].isdigit():
                j += 1
        token, i = d[i:j], j
        if not token or token in "+-.":
            raise ValueError(f"bad number at {i} in {d!r}")
        return token

    while i < n:
        ch = d[i]
        if ch in " ,\t\n":
            i += 1
        elif ch.lower() in _ARGS:
            cmd, arg = ch, 0
            out.append(ch)
            i += 1
        else:
            if cmd.lower() == "a" and arg % 7 in (3, 4):  # a flag: exactly one character
                if ch not in "01":
                    raise ValueError(f"bad arc flag {ch!r} in {d!r}")
                out.append(ch)
                i += 1
            else:
                out.append(number())
            arg += 1
    # A path's leading relative moveto is absolute (SVG spec), and further pairs after it are relative
    # linetos. Written out explicitly, so the path stays the same when joined after another one.
    if out and out[0] == "m":
        rest = out[3:]
        out = ["M", out[1], out[2]] + (["l", *rest] if rest and not rest[0].isalpha() else rest)
    text = ""
    for tok in out:
        if tok.isalpha():
            text += tok
        else:
            text += ("" if not text or text[-1].isalpha() else " ") + tok
    return text


def _f(x: float) -> str:
    s = f"{x:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def to_path(tag: str, a: dict[str, str]) -> str:
    """One SVG element as path data (same drawing, stroke only)."""
    if tag == "path":
        return normalize_path(a["d"])
    if tag in ("circle", "ellipse"):
        cx, cy = _n(a["cx"]), _n(a["cy"])
        rx = _n(a.get("r", a.get("rx", "0")))
        ry = _n(a.get("r", a.get("ry", "0")))
        return (
            f"M{_f(cx - rx)} {_f(cy)}a{_f(rx)} {_f(ry)} 0 1 0 {_f(2 * rx)} 0"
            f"a{_f(rx)} {_f(ry)} 0 1 0 {_f(-2 * rx)} 0"
        )
    if tag == "line":
        return f"M{a['x1']} {a['y1']}L{a['x2']} {a['y2']}"
    if tag in ("polyline", "polygon"):
        pts = a["points"].replace(",", " ").split()
        pairs = [f"{pts[i]} {pts[i + 1]}" for i in range(0, len(pts), 2)]
        return "M" + "L".join(pairs) + ("Z" if tag == "polygon" else "")
    if tag == "rect":
        x, y, w, h = _n(a["x"]), _n(a["y"]), _n(a["width"]), _n(a["height"])
        rx = min(_n(a.get("rx", a.get("ry", "0"))), w / 2, h / 2)
        if rx <= 0:
            return f"M{_f(x)} {_f(y)}h{_f(w)}v{_f(h)}h{_f(-w)}Z"
        return (
            f"M{_f(x + rx)} {_f(y)}h{_f(w - 2 * rx)}a{_f(rx)} {_f(rx)} 0 0 1 {_f(rx)} {_f(rx)}"
            f"v{_f(h - 2 * rx)}a{_f(rx)} {_f(rx)} 0 0 1 {_f(-rx)} {_f(rx)}h{_f(-(w - 2 * rx))}"
            f"a{_f(rx)} {_f(rx)} 0 0 1 {_f(-rx)} {_f(-rx)}v{_f(-(h - 2 * rx))}"
            f"a{_f(rx)} {_f(rx)} 0 0 1 {_f(rx)} {_f(-rx)}Z"
        )
    raise ValueError(f"unsupported element <{tag}>")


def main() -> None:
    pkg = Path(sys.argv[1])
    nodes = json.loads((pkg / "icon-nodes.json").read_text(encoding="utf-8"))
    version = json.loads((pkg / "package.json").read_text(encoding="utf-8"))["version"]
    license_text = (pkg / "LICENSE").read_text(encoding="utf-8").strip()
    lines = []
    for name, lucide in ICONS.items():
        parts = []
        for tag, attrs in nodes[lucide]:
            attrs = dict(attrs)
            for (t, key), value in TWEAKS.get(lucide, {}).items():
                if t == tag and key in attrs:
                    attrs[key] = value
            parts.append(to_path(tag, attrs))
        lines.append(f'  {name}: "{"".join(parts)}",')
    header = "\n".join(f" * {line}".rstrip() for line in license_text.splitlines())
    OUT.write_text(
        "/**\n"
        f" * Line icons from Lucide (https://lucide.dev), lucide-static {version}, converted to single SVG\n"
        " * paths on the 24x24 grid of ./icons.tsx by scripts/vendor_icons.py (do not edit by hand).\n"
        " * Static data, never input. The standing figure's head is enlarged (r 1 -> 2).\n"
        " *\n"
        f"{header}\n"
        " */\n"
        "export const LUCIDE_ICONS: Record<string, string> = {\n" + "\n".join(lines) + "\n};\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {len(ICONS)} icons to {OUT.relative_to(ROOT)}")
    print("engine names:", ", ".join(f'"{n}"' for n in ICONS))


if __name__ == "__main__":
    main()
