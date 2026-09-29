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
    return dsl


_ENDS_AT = re.compile(r"ends at ([0-9.]+)s")
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
    applied = False
    for e in errors:
        code, message, path = e.get("code"), str(e.get("message", "")), str(e.get("path", ""))
        if code == "ACTION_EXCEEDS_SCENE" and (m := _ENDS_AT.search(message)):
            # An action running a moment past the end: the scene lasts that moment longer.
            end = float(m.group(1))
            if end <= float(out.get("duration") or 0) + 3:  # longer is not a slip: the timing is off
                out["duration"] = max(out.get("duration") or 0, math.ceil(round((end + 0.3) * 10, 6)) / 10)
                applied = True
        elif code == "SCHEMA" and (m := _TOO_BIG.search(message) or _TOO_SMALL.search(message)):
            limit = float(m.group(1))
            applied = _set_number(out, path, int(limit) if limit.is_integer() else limit) or applied
        elif code == "SCHEMA" and _ANCHOR_REF in message:
            applied = _complete_anchor(out, path, set(objects)) or applied
        elif code == "UNSUPPORTED_GLYPH":
            applied = _drop_glyphs(out, path, message) or applied
        elif isinstance(e.get("fix"), dict):
            # The engine says how to move the object (off the frame, over the title, onto another
            # label): one move per object and pass.
            oid = path.rsplit("/", 1)[-1]
            if oid not in moved and _move(objects.get(oid), e["fix"], moving - {oid}):
                moved.add(oid)
                applied = True
    return out if applied else None
