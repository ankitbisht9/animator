"""Deterministic clean-up of model-written scenes, so mechanical slips cost no correction round
(each LLM round adds seconds; ADR-033). The engine stays strict: these only change a scene the way
the model evidently meant it, and the engine still validates the result.

- `tidy` runs on every scene before it is compiled.
- `autofix` applies the sure fixes for the engine's errors (an action ending a moment late, a number
  out of range, a bare id where an anchor belongs, an object off the frame, over the title or on
  another label that the engine says how to move); the model gets the rest.
"""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Callable
from typing import Any

# TeX commands models like that the engine's TeX subset spells differently (same rendering).
TEX_ALIASES = {
    "longrightarrow": "rightarrow",
    "Longrightarrow": "Rightarrow",
    "longleftarrow": "leftarrow",
    "Longleftarrow": "Leftarrow",
    "longleftrightarrow": "leftrightarrow",
    "Longleftrightarrow": "Leftrightarrow",
    "implies": "Rightarrow",
    "iff": "Leftrightarrow",
    "gets": "leftarrow",
    "mapsto": "rightarrow",
    "textrm": "mathrm",
    "textit": "mathit",
    "textsf": "mathsf",
    "mbox": "text",
    "cdotp": "cdot",
    "bullet": "cdot",
    "lvert": "|",
    "rvert": "|",
    "vert": "|",
    "lt": "<",
    "gt": ">",
    "leqslant": "leq",
    "geqslant": "geq",
}
_TEX_COMMAND = re.compile(r"\\([A-Za-z]+)")


def _tex(tex: str) -> str:
    def swap(m: re.Match[str]) -> str:
        alias = TEX_ALIASES.get(m.group(1))
        if alias is None:
            return m.group(0)
        return alias if not alias[0].isalpha() else "\\" + alias

    return _TEX_COMMAND.sub(swap, tex)


_TIME_REF = re.compile(r"^(cue|beat|beat_end):[a-z][a-z0-9_]*$")


def _time_ref(value: str, beats: set[str], cues: set[str]) -> str:
    """'b2' -> 'beat:b2', 'Beat: b2' -> 'beat:b2', 'b2_end' -> 'beat_end:b2' (engine form)."""
    ref = re.sub(r"\s+", "", value).lower().replace("beat-end", "beat_end").replace("beatend", "beat_end")
    if _TIME_REF.match(ref):
        return ref
    if ref in beats:
        return f"beat:{ref}"
    if ref in cues:
        return f"cue:{ref}"
    if ref.endswith("_end") and ref[:-4] in beats:
        return f"beat_end:{ref[:-4]}"
    return value


def tidy(dsl: dict[str, Any]) -> dict[str, Any]:
    """Resolve slips with one obvious reading (the dict is changed in place and returned)."""
    beats = {
        str(b.get("id")) for b in ((dsl.get("narration") or {}).get("beats") or []) if isinstance(b, dict)
    }
    cues = {str(c.get("id")) for c in dsl.get("cues") or [] if isinstance(c, dict)}
    for action in dsl.get("timeline") or []:
        start = action.get("start")
        if isinstance(start, dict):
            # Strict outputs write every start field; a model sometimes fills `at` next to the
            # `after`/`with` it meant. The relative reference is the deliberate one.
            given = [k for k in ("with", "after", "at") if start.get(k) is not None]
            for k in given[1:]:
                start[k] = None
            if isinstance(start.get("at"), str):
                start["at"] = _time_ref(start["at"], beats, cues)
    for obj in dsl.get("objects") or []:
        if obj.get("type") == "equation":
            for part in (obj.get("params") or {}).get("parts") or []:
                if isinstance(part.get("tex"), str):
                    part["tex"] = _tex(part["tex"])
        elif isinstance(obj.get("params"), dict):
            _line_breaks(obj["params"])
    for action in dsl.get("timeline") or []:
        if action.get("property") == "text":
            _line_breaks(action.get("keyframes"))
    _action_ids(dsl)
    _diagram_parts(dsl)
    return dsl


_ID = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
# Marks a model may write for a prime (a typographic apostrophe); opening quotes are dropped.
_PRIMES = str.maketrans({"\u2019": "'", "\u2032": "'", "\u2033": "''", "`": "'", "\u2018": None, '"': None})


def _id_form(raw: str, limit: int) -> str:
    """The engine's form of a name a model wrote: "C'" -> 'c_prime', 'Feature/Login' ->
    'feature_login', '10' -> 'n10'."""
    s = raw.strip().translate(_PRIMES).lstrip("'").replace("'", "_prime").lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    if not s[:1].isalpha():
        s = f"n{s}"
    return s[:limit].rstrip("_")


def _free(base: str, limit: int, used: Callable[[str], bool]) -> str:
    """`base`, or `base_2`, `base_3`... while it is taken."""
    name, k = base, 2
    while used(name):
        tail = f"_{k}"
        name = base[: limit - len(tail)].rstrip("_") + tail
        k += 1
    return name


def _action_ids(dsl: dict[str, Any]) -> None:
    """Action ids a model wrote off the engine's form ("show_C'", 'Step1') take it, and so do the
    starts that name them."""
    timeline = [a for a in dsl.get("timeline") or [] if isinstance(a, dict)]
    ids = {a["id"] for a in timeline if isinstance(a.get("id"), str) and _ID.match(a["id"])}
    renamed: dict[str, str] = {}
    for action in timeline:
        raw = action.get("id")
        if not isinstance(raw, str) or not raw or _ID.match(raw):
            continue
        if raw not in renamed:
            renamed[raw] = _free(_id_form(raw, 48), 48, ids.__contains__)
            ids.add(renamed[raw])
        action["id"] = renamed[raw]
    for action in timeline:
        start = action.get("start")
        if not isinstance(start, dict):
            continue
        for k in ("after", "with"):
            ref = start.get(k)
            if isinstance(ref, str) and ref and ref not in ids:
                fixed = renamed.get(ref) or _id_form(ref, 48)
                if fixed in ids:
                    start[k] = fixed


# Diagram parts (ADR-061): nodes, named edges and lanes share one set of names, each node also takes
# its sides and corners ('c2_top', 'c2_top_left'), and the diagram's box anchors are its own (the
# engine's diagram checks).
_PART_ID = re.compile(r"^[a-z][a-z0-9_]{0,24}$")
_PART_SIDES = ("top_left", "top_right", "bottom_left", "bottom_right", "top", "bottom", "left", "right")
_BOX_ANCHORS = frozenset(
    ("center", "top", "bottom", "left", "right", "top_left", "top_right", "bottom_left", "bottom_right")
)
_PART_KINDS = (("node", "nodes"), ("edge", "edges"), ("lane", "lanes"))
# Beside a part, by side: the part's anchor, this object's anchor and the direction of the gap.
_BESIDE = {
    "left": ("_left", "right", (-1, 0)),
    "right": ("_right", "left", (1, 0)),
    "above": ("_top", "bottom", (0, -1)),
    "below": ("_bottom", "top", (0, 1)),
    "center": ("", "center", (0, 0)),
}
_BESIDE_GAP = 24  # the engine's gap beside an object
_ANCHOR_LIKE = re.compile(r"^[^.\s]+\.\S+$")
_TEXT_KEYS = frozenset(("text", "label", "title", "caption", "note", "items"))  # shown, never references


class _Parts:
    """One diagram's part names, after any renaming, and what a reference to them means."""

    def __init__(self, ids: dict[str, set[str]], renamed: dict[tuple[str, str], str], labels: dict[str, str]):
        self.nodes = ids["node"]
        self.parts = ids["node"] | ids["edge"] | ids["lane"]
        self.anchors = (
            set(_BOX_ANCHORS) | self.parts | {f"{n}_{side}" for n in self.nodes for side in _PART_SIDES}
        )
        self.renamed = renamed
        self.labels = labels

    def part(self, ref: str) -> str | None:
        """The node, edge or lane that `ref` names (by its id as written, or a node's label)."""
        if ref in self.parts:
            return ref
        for kind, _ in _PART_KINDS:
            if (kind, ref) in self.renamed:
                return self.renamed[(kind, ref)]
        form = _id_form(ref, 25)
        return form if form in self.parts else self.labels.get(ref)

    def anchor(self, ref: str) -> str | None:
        """The anchor that `ref` means: a part, a node's side ("C'_top", 'c2.top') or a box anchor."""
        if ref in self.anchors:
            return ref
        for side in _PART_SIDES:
            for sep in ("_", "."):
                if ref.endswith(sep + side):
                    node = self.part(ref[: -len(side) - 1])
                    if node in self.nodes:
                        return f"{node}_{side}"
        form = _id_form(ref, 32)
        return form if form in self.anchors else self.part(ref)


def _rename_parts(params: dict[str, Any]) -> _Parts:
    groups = {kind: [p for p in params.get(key) or [] if isinstance(p, dict)] for kind, key in _PART_KINDS}

    def claims(kind: str, name: str) -> list[str]:
        return [name, *(f"{name}_{side}" for side in _PART_SIDES)] if kind == "node" else [name]

    def fine(v: Any) -> bool:
        return isinstance(v, str) and _PART_ID.match(v) is not None and v not in _BOX_ANCHORS

    taken = set(_BOX_ANCHORS)
    for kind, items in groups.items():
        for p in items:
            if fine(p.get("id")):
                taken.update(claims(kind, p["id"]))
    renamed: dict[tuple[str, str], str] = {}
    for kind, items in groups.items():
        for p in items:
            raw = p.get("id")
            if not isinstance(raw, str) or not raw or fine(raw):
                continue
            if (kind, raw) not in renamed:
                base = _id_form(raw, 25)
                if base in _BOX_ANCHORS:
                    base = f"{base}_node"  # the engine's own suggestion

                def used(name: str, kind: str = kind) -> bool:
                    return any(x in taken for x in claims(kind, name))

                name = _free(base, 25, used)
                taken.update(claims(kind, name))
                renamed[(kind, raw)] = name
            p["id"] = renamed[(kind, raw)]
    ids = {kind: {p["id"] for p in items if isinstance(p.get("id"), str)} for kind, items in groups.items()}
    labels: dict[str, str] = {}
    for n in groups["node"]:
        label = n.get("label")
        if isinstance(label, str) and label and isinstance(n.get("id"), str):
            labels[label] = n["id"] if label not in labels else ""  # a label two nodes share names neither
    labels = {k: v for k, v in labels.items() if v}

    def ref(kind: str, v: Any) -> Any:
        if not isinstance(v, str) or v in ids[kind]:
            return v
        fixed = renamed.get((kind, v)) or _id_form(v, 25)
        if fixed in ids[kind]:
            return fixed
        return labels.get(v, v) if kind == "node" else v

    for n in groups["node"]:
        if "lane" in n:
            n["lane"] = ref("lane", n["lane"])
    for e in groups["edge"]:
        for end in ("from", "to"):
            if end in e:
                e[end] = ref("node", e[end])
    return _Parts(ids, renamed, labels)


def _diagram_parts(dsl: dict[str, Any]) -> None:
    """Diagram part ids a model wrote off the engine's form ("C'", 'A', '10') take it, and so does
    every reference to them: edge ends, node lanes, the `terms` that name them and anchors such as
    "history.C'_top". A place beside a part ('relativeTo' takes whole objects) becomes a pin at
    that side of the part."""
    objects = [o for o in dsl.get("objects") or [] if isinstance(o, dict)]
    diagrams = {
        o["id"]: _rename_parts(o["params"])
        for o in objects
        if o.get("type") == "diagram" and isinstance(o.get("id"), str) and isinstance(o.get("params"), dict)
    }
    if not diagrams:
        return

    def anchor_ref(value: str) -> str:
        diagram, _, rest = value.partition(".")
        found = diagrams[diagram].anchor(rest) if diagram in diagrams else None
        return f"{diagram}.{found}" if found else value

    timeline = [a for a in dsl.get("timeline") or [] if isinstance(a, dict)]
    for action in timeline:
        parts = diagrams.get(action.get("target") or "")
        if parts is not None and isinstance(action.get("terms"), list):
            action["terms"] = [(parts.part(t) or t) if isinstance(t, str) else t for t in action["terms"]]
    for o in objects:
        if o.get("type") != "diagram":
            _anchor_refs(o.get("params"), anchor_ref)
        place = o.get("place")
        if isinstance(place, dict):
            pinned = _beside_part(place, diagrams)
            o["place"] = pinned or _anchor_refs(place, anchor_ref)
    for action in timeline:
        to = action.get("to")
        pinned = _beside_part(to, diagrams) if isinstance(to, dict) else None
        if pinned:
            action["to"] = pinned  # a move beside a part
        for k, v in action.items():
            if k not in ("id", "target", "terms", "start"):
                action[k] = _anchor_refs(v, anchor_ref)


def _anchor_refs(value: Any, fix: Callable[[str], str]) -> Any:
    if isinstance(value, dict):
        for k, v in value.items():
            if k not in _TEXT_KEYS:
                value[k] = _anchor_refs(v, fix)
    elif isinstance(value, list):
        value[:] = [_anchor_refs(v, fix) for v in value]
    elif isinstance(value, str) and _ANCHOR_LIKE.match(value):
        return fix(value)
    return value


def _beside_part(place: dict[str, Any], diagrams: dict[str, _Parts]) -> dict[str, Any] | None:
    """{'relativeTo': 'tree.n10', 'side': 'left'}: the pin it means, at the part's left side."""
    ref, side = place.get("relativeTo"), place.get("side")
    if not isinstance(ref, str) or side not in _BESIDE:
        return None
    diagram, dot, rest = ref.partition(".")
    found = diagrams[diagram].anchor(rest) if dot and diagram in diagrams else None
    if found is None:
        return None
    suffix, own, (dx, dy) = _BESIDE[side]
    gap = place.get("gap")
    gap = float(gap) if isinstance(gap, (int, float)) else (0.0 if side == "center" else _BESIDE_GAP)
    at = f"{found}{suffix}" if found in diagrams[diagram].nodes else found
    return {
        "at": f"{diagram}.{at}",
        "anchor": own,
        "offset": {"x": dx * gap, "y": dy * gap},
        "follow": place.get("follow"),
    }


# Text params where a backslash is never meant literally (TeX and expressions keep theirs: \neq, \nu).
_LITERAL_KEYS = {"tex", "expr", "latex", "terms"}


def _line_breaks(value: Any) -> Any:
    """A line break a model wrote escaped ("a\\nb", shown as a visible backslash and n) becomes a
    real one, which the engine lays out as a new line."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k not in _LITERAL_KEYS:
                value[k] = _line_breaks(v)
    elif isinstance(value, list):
        value[:] = [_line_breaks(v) for v in value]
    elif isinstance(value, str) and "\\n" in value:
        return value.replace("\\r\\n", "\n").replace("\\n", "\n")
    return value


_ENDS_AT = re.compile(r"ends at ([0-9.]+)s")
_LASTS = re.compile(r"lasts ([0-9.]+)s")
_ACTION_NAMED = re.compile(r"Action '([a-z][a-z0-9_]*)'")


def _pull_back_from_the_end(dsl: dict[str, Any], message: str, end: float) -> bool:
    """An action started at the end of the last beat starts where the scene ends, however long the
    scene is (the last beat ends with it), so a longer scene never fits it: it starts its own length
    earlier instead, ending just before the scene does (a time the voice's retiming keeps in step)."""
    named, lasts = _ACTION_NAMED.search(message), _LASTS.search(message)
    beats = [b.get("id") for b in (dsl.get("narration") or {}).get("beats") or [] if isinstance(b, dict)]
    if not named or not lasts or not beats:
        return False
    action = next(
        (a for a in dsl.get("timeline") or [] if isinstance(a, dict) and a.get("id") == named.group(1)), None
    )
    start = action.get("start") if action else None
    if not isinstance(start, dict) or start.get("at") != f"beat_end:{beats[-1]}":
        return False
    scene = float(lasts.group(1))
    delay = start.get("delay")
    length = end - scene - (float(delay) if isinstance(delay, (int, float)) else 0.0)
    at = round(scene - length - 0.1, 2)
    if length <= 0 or at < 0:
        return False
    start.update(at=at, delay=None)
    return True


_TOO_BIG = re.compile(r"Too big: expected number to be <=?\s*(-?[0-9.]+)")
_TOO_SMALL = re.compile(r"Too small: expected number to be >=?\s*(-?[0-9.]+)")


_ANCHOR_REF = "anchor references look like"
_BARE_ID = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


def _slot(dsl: dict[str, Any], pointer: str) -> tuple[Any, Any] | None:
    """The container and key a JSON pointer ends at, or None when the path does not exist."""
    keys = [k for k in pointer.split("/") if k]
    if not keys:
        return None
    node: Any = dsl
    for k in keys[:-1]:
        if isinstance(node, list) and k.isdigit() and int(k) < len(node):
            node = node[int(k)]
        elif isinstance(node, dict) and k in node:
            node = node[k]
        else:
            return None
    last = keys[-1]
    if isinstance(node, list) and last.isdigit() and int(last) < len(node):
        return node, int(last)
    if isinstance(node, dict) and last in node:
        return node, last
    return None


def _set_number(dsl: dict[str, Any], pointer: str, value: float) -> bool:
    found = _slot(dsl, pointer)
    if found is None or not isinstance(found[0][found[1]], int | float):
        return False
    found[0][found[1]] = value
    return True


_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')
# Emoji glue that is invisible on its own: variation selectors, zero-width joiner, skin tones.
_EMOJI_GLUE = re.compile("[︎️‍\U0001f3fb-\U0001f3ff]")


def _drop_glyphs(dsl: dict[str, Any], pointer: str, message: str) -> bool:
    """Remove the characters the font cannot draw (the engine lists them; they would show as "?"),
    e.g. emoji in a label. Words stay; the model can use an icon for the picture."""
    found = _slot(dsl, pointer)
    if found is None or not isinstance(found[0][found[1]], str):
        return False
    bad = {json.loads(f'"{m}"') for m in _QUOTED.findall(message)}
    if not bad:
        return False
    text = str(found[0][found[1]])
    kept = _EMOJI_GLUE.sub("", "".join(ch for ch in text if ch not in bad))
    kept = re.sub(r"[ \t]{2,}", " ", kept).strip()
    if kept == text or not kept:
        return False
    found[0][found[1]] = kept
    return True


def _complete_anchor(dsl: dict[str, Any], pointer: str, ids: set[Any]) -> bool:
    """A bare object id where an anchor reference belongs means the object's center."""
    found = _slot(dsl, pointer)
    if found is None:
        return False
    value = found[0][found[1]]
    if not isinstance(value, str) or not _BARE_ID.match(value) or value not in ids:
        return False
    found[0][found[1]] = f"{value}.center"
    return True


def _is_point(v: Any) -> bool:
    return (
        isinstance(v, dict)
        and set(v) == {"x", "y"}
        and all(isinstance(v[k], int | float) and not isinstance(v[k], bool) for k in "xy")
    )


def _shift_points(params: dict[str, Any], dx: float, dy: float) -> bool:
    """Move the raw points of a world-space object (a line's `from`/`to`, a path of points...).
    Anchor references ("pipe.top") are left alone: they follow their objects."""
    moved = False
    for value in params.values():
        points = value if isinstance(value, list) else [value]
        for p in points:
            if _is_point(p):
                p["x"], p["y"] = float(p["x"]) + dx, float(p["y"]) + dy
                moved = True
    return moved


def _pinned_to(obj: dict[str, Any]) -> str | None:
    place = obj.get("place")
    if not isinstance(place, dict):
        return None
    if isinstance(place.get("at"), str):
        return str(place["at"]).split(".")[0]
    return str(place["relativeTo"]) if place.get("relativeTo") else None


_ABS_PATH = re.compile(r"^[MLHVCSQTZ0-9eE.,\s+-]+$")  # absolute commands only (no arcs: no bounds)
_CANVAS = (0.0, 0.0, 1600.0, 900.0)


def _path_bounds(d: str) -> tuple[float, float, float, float] | None:
    """Bounds of path data written with absolute commands (control points included), else None."""
    if not _ABS_PATH.match(d):
        return None
    xs: list[float] = []
    ys: list[float] = []
    cmd, k = "", 0
    for tok in re.findall(r"[MLHVCSQTZ]|-?\d*\.?\d+(?:[eE][-+]?\d+)?", d):
        if tok.isalpha():
            cmd, k = tok, 0
            continue
        v = float(tok)
        if cmd == "H":
            xs.append(v)
        elif cmd == "V":
            ys.append(v)
        else:
            (xs if k % 2 == 0 else ys).append(v)  # x, y pairs
        k += 1
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _place_at_own_points(obj: dict[str, Any] | None) -> bool:
    """Place a raw-positioned path where its own (frame) coordinates put it."""
    if not obj or obj.get("type") != "path":
        return False
    place, d = obj.get("place"), (obj.get("params") or {}).get("d")
    if not isinstance(place, dict) or place.get("x") is None or not isinstance(d, str):
        return False
    box = _path_bounds(d)
    if box is None:
        return False
    x0, y0, x1, y1 = box
    cx0, cy0, cx1, cy1 = _CANVAS
    if not (cx0 <= x0 and x1 <= cx1 and cy0 <= y0 and y1 <= cy1) or (x1 - x0) * (y1 - y0) <= 0:
        return False
    center = ((x0 + x1) / 2, (y0 + y1) / 2)
    if (place.get("x"), place.get("y")) == center:
        return False
    place.update(x=center[0], y=center[1], anchor="center")
    return True


def _move(obj: dict[str, Any] | None, fix: dict[str, Any], moving: set[str]) -> bool:
    """Apply the engine's move hint (shrink, then move) to an object: its slot offset or position,
    its pin offset (it keeps following what it is pinned to), the side of a relative place, or the
    raw points of an object drawn between points (a line, an arrow). An object pinned to one that
    moves too is left alone: it moves with it."""
    if obj is None:
        return False
    place = obj.get("place")
    dx, dy = float(fix.get("dx") or 0), float(fix.get("dy") or 0)
    new_params = fix.get("params")
    if isinstance(new_params, dict) and new_params and isinstance(obj.get("params"), dict):
        obj["params"].update(new_params)  # e.g. an arrow's `curve` that routes it clear
        if not dx and not dy and not fix.get("scale"):
            return True
    if not isinstance(place, dict):
        params = obj.get("params")
        return isinstance(params, dict) and _shift_points(params, dx, dy)
    if _pinned_to(obj) in moving:
        return False  # moves with the object it is attached to
    if place.get("relativeTo"):
        # A relative place has no offset: move it to another side of its object instead (the engine
        # checks the result, and the change is kept only if it helps).
        side = place.get("side")
        if dy and side in ("below", "above"):
            place["side"] = "right"
        elif dx and side in ("left", "right"):
            place["side"] = "below" if dy >= 0 else "above"
        else:
            return False
        return True
    if place.get("slot") or place.get("at"):
        offset = place.get("offset") or {}
        place["offset"] = {"x": float(offset.get("x") or 0) + dx, "y": float(offset.get("y") or 0) + dy}
    elif place.get("x") is not None and place.get("y") is not None:
        place["x"], place["y"] = float(place["x"]) + dx, float(place["y"]) + dy
    else:
        return False
    if fix.get("scale"):
        obj["scale"] = round(float(obj.get("scale") or 1) * float(fix["scale"]), 3)
    return True


def autofix(dsl: dict[str, Any], errors: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A copy of the scene with every sure fix applied, or None when none applies. The engine
    judges the result (see `Pipeline._check`)."""
    out = copy.deepcopy(dsl)
    objects = {o.get("id"): o for o in out.get("objects") or [] if isinstance(o, dict)}
    moving = {str(e.get("path", "")).rsplit("/", 1)[-1] for e in errors if isinstance(e.get("fix"), dict)}
    moved: set[str] = set()
    overlaps: list[dict[str, Any]] = []
    applied = False
    for e in errors:
        code, message, path = e.get("code"), str(e.get("message", "")), str(e.get("path", ""))
        if code == "ACTION_EXCEEDS_SCENE" and (m := _ENDS_AT.search(message)):
            # An action running a moment past the end: the scene lasts that moment longer.
            end = float(m.group(1))
            if _pull_back_from_the_end(out, message, end):
                applied = True
            elif end <= float(out.get("duration") or 0) + 3:  # longer is not a slip: the timing is off
                out["duration"] = max(out.get("duration") or 0, math.ceil(round((end + 0.3) * 10, 6)) / 10)
                applied = True
        elif code == "SCHEMA" and (m := _TOO_BIG.search(message) or _TOO_SMALL.search(message)):
            limit = float(m.group(1))
            applied = _set_number(out, path, int(limit) if limit.is_integer() else limit) or applied
        elif code == "SCHEMA" and _ANCHOR_REF in message:
            applied = _complete_anchor(out, path, set(objects)) or applied
        elif code == "UNSUPPORTED_GLYPH":
            applied = _drop_glyphs(out, path, message) or applied
        elif code == "OUT_OF_BOUNDS" and _place_at_own_points(objects.get(path.rsplit("/", 1)[-1])):
            # A path drawn in frame coordinates, placed somewhere else as well: the model meant its
            # points as written (better than pushing the whole drawing into a corner).
            moved.add(path.rsplit("/", 1)[-1])
            applied = True
        elif code == "OBJECT_OVERLAP" and isinstance(e.get("fix"), dict):
            overlaps.append(e)
        elif isinstance(e.get("fix"), dict):
            # The engine says how to move the object (off the frame, over the title, onto another
            # label): one move per object and pass.
            oid = path.rsplit("/", 1)[-1]
            if oid not in moved and _move(objects.get(oid), e["fix"], moving - {oid}):
                moved.add(oid)
                applied = True
    # An object covering another moves with the rest of its drawing (the parts sharing its id prefix,
    # like a magnet's two halves), by the largest move any of its parts needs.
    drawings: dict[str, dict[str, Any]] = {}
    for e in overlaps:
        prefix = str(e.get("path", "")).rsplit("/", 1)[-1].split("_")[0]
        fix = e["fix"]
        size = abs(float(fix.get("dx") or 0)) + abs(float(fix.get("dy") or 0))
        best = drawings.get(prefix)
        if best is None or size > abs(float(best.get("dx") or 0)) + abs(float(best.get("dy") or 0)):
            drawings[prefix] = fix
    everything = {str(o) for o in objects}
    for prefix, fix in drawings.items():
        for key, obj in objects.items():
            part = str(key)
            # A part pinned to another object moves with that object, not on its own.
            if part.split("_")[0] == prefix and part not in moved and _move(obj, fix, everything - {part}):
                moved.add(part)
                applied = True
    return out if applied else None


_OVERLAP = re.compile(r"'([A-Za-z0-9_]+)' and '([A-Za-z0-9_]+)' overlap")


def salvage(dsl: dict[str, Any], errors: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A last resort for a scene whose only remaining errors are labels overlapping each other (after
    every correction round): the later label of each pair is left out, with the actions that show it,
    when nothing else refers to it. A scene missing one label teaches better than the simplified
    fallback scene. None when this does not apply; the engine judges the result."""
    if not errors or any(e.get("code") != "LABEL_OVERLAP" for e in errors):
        return None
    drop: set[str] = set()
    for e in errors:
        m = _OVERLAP.search(str(e.get("message", "")))
        if m is None:
            return None
        drop.add(m.group(2))
    out = copy.deepcopy(dsl)
    kept = [o for o in out.get("objects") or [] if o.get("id") not in drop]
    if len(kept) == len(out.get("objects") or []):
        return None
    removed: dict[str, Any] = {}
    timeline = []
    for action in out.get("timeline") or []:
        if action.get("target") in drop:
            removed[str(action.get("id"))] = action.get("start")
        else:
            timeline.append(action)
    for action in timeline:
        start = action.get("start")
        if isinstance(start, dict):
            for key in ("with", "after"):
                if start.get(key) in removed:
                    # It started from an action that is gone: it starts where that one started.
                    action["start"] = copy.deepcopy(removed[start[key]]) or {"at": 0}
                    break
    rest = json.dumps([kept, timeline, out.get("values"), out.get("solvers"), out.get("interactions")])
    if any(f'"{oid}"' in rest or f'"{oid}.' in rest or f'"bind": "{oid}' in rest for oid in drop):
        return None  # something else still depends on it
    out["objects"] = kept
    out["timeline"] = timeline
    return out
