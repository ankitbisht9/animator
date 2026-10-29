"""Keep it moving (ADR-067): while the narrator talks, something on screen changes every few seconds.
The engine's compiled scene says when each action runs and when each object is on screen; a stretch
of narration longer than `STILL_S` with nothing changing gets gentle highlights, one for each `STILL_S`
of stillness (`MOST` a scene at most), spread through it: on the thing the narration names there, as
its name is said (the signalling principle; a diagram's node gets its ring), or else a soft glow on the
scene's main picture. Never on words or pointers, never over another highlight of the same thing; the
pipeline keeps the result only when the engine finds the scene valid with no new glitch. No model call.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from animator.pipeline.narration import SYNC_LEAD_S, _name_tokens, _names

STILL_S = 5.0  # the longest stretch of narration with nothing changing on screen
MOST = 2  # highlights added to one scene at most
PULSE_S = 1.2  # how long an added highlight lasts
PAD_S = 0.3  # the eye still registers a change this long after it ends
EDGE_S = 0.2  # an added highlight keeps this far from where its stretch begins
# Actions that change nothing on screen by themselves (a binding to time does: see `_uses_time`).
IDLE = {"wait", "bind", "follow", "group", "ungroup"}
CONTINUOUS = {"flow_particles", "particles"}  # components that move on their own while shown
# Objects that are words, pointers or backdrop, not the picture: never given an added highlight.
NOT_PICTURE = {
    "text",
    "label",
    "caption",
    "title_card",
    "note",
    "badge",
    "speech_bubble",
    "callout",
    "readout",
    "arrow",
    "vector_arrow",
    "dimension",
    "bracket",
    "marker",
    "burst",
    "card",
    "grid",
}
LARGE = {"diagram", "axes", "graph", "container", "group"}  # a soft glow: a pulse would grow them
PARTS = ("nodes", "edges", "lanes")  # a diagram's named parts (ADR-061)
PREFIX = "keep_moving_"  # the ids of the highlights this pass adds


def _uses_time(expr: Any) -> bool:
    """Whether an engine expression (its parsed form) depends on the time `t`."""
    if not isinstance(expr, Mapping):
        return False
    if expr.get("k") == "ref":
        return expr.get("name") == "t"
    kids = [expr.get("e"), expr.get("l"), expr.get("r"), *(expr.get("args") or [])]
    return any(_uses_time(k) for k in kids)


def _level(ch: Mapping[str, Any] | None, t: float) -> float:
    """A presence or opacity channel's value at `t`; halfway through a change counts as half."""
    if not ch:
        return 1.0
    v: Any = ch.get("base", 1)
    for seg in sorted(ch.get("segs") or [], key=lambda s: float(s.get("t0", 0.0))):
        if float(seg.get("t0", 0.0)) > t:
            break
        done = t >= float(seg.get("t1", 0.0))
        if seg.get("k") in ("presence", "tween"):
            v = seg.get("to") if done else (float(seg.get("from", 0)) + float(seg.get("to", 0))) / 2
        elif seg.get("k") == "step":
            v = seg.get("value")
        elif seg.get("k") == "keys":
            v = (seg.get("values") or [v])[-1] if done else 0.5
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    return float(v) if isinstance(v, int | float) else 1.0


def still_stretches(
    compiled: Mapping[str, Any], beats: Sequence[Mapping[str, Any]]
) -> list[tuple[float, float]]:
    """Stretches of narration (scene seconds) with nothing changing on screen."""
    end = float(compiled.get("duration") or 0.0)
    busy: list[tuple[float, float]] = []
    for a in (compiled.get("actions") or {}).values():
        if a.get("action") not in IDLE:
            busy.append((float(a["start"]), float(a["end"]) + PAD_S))
    for s in compiled.get("solvers") or []:
        if s.get("t0") is not None and s.get("t1") is not None:
            busy.append((float(s["t0"]), float(s["t1"]) + PAD_S))
    for o in compiled.get("objects") or []:
        bound = (o.get("bound") or {}).values()
        if o.get("type") in CONTINUOUS or any(_uses_time(b.get("expr")) for b in bound):
            busy.append((0.0, end))
        for ch in (o.get("ch") or {}).values():
            for seg in ch.get("segs") or []:
                if seg.get("k") == "expr" and _uses_time(seg.get("expr")):
                    busy.append((float(seg["t0"]), float(seg["t1"]) + PAD_S))
    busy.sort()
    merged: list[list[float]] = []
    for a, b in busy:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    pieces: list[tuple[float, float]] = []
    for beat in beats:
        t, hi = float(beat["start"]), float(beat["end"])
        for a, b in merged:
            if b <= t or a >= hi:
                continue
            if a > t:
                pieces.append((t, a))
            t = max(t, b)
        if t < hi:
            pieces.append((t, hi))
    joined: list[list[float]] = []
    for a, b in pieces:
        if joined and abs(a - joined[-1][1]) < 1e-6:
            joined[-1][1] = b
        else:
            joined.append([a, b])
    return [(a, b) for a, b in joined if b - a > 0.05]


def _visible(objects: Mapping[str, Mapping[str, Any]], oid: str, t: float) -> bool:
    """Whether an object, and every group it sits in, is fully on screen at `t`."""
    seen: set[str] = set()
    while oid and oid not in seen:
        seen.add(oid)
        o = objects.get(oid)
        if o is None:
            return False
        ch = o.get("ch") or {}
        if _level(ch.get("presence"), t) < 0.99 or _level(ch.get("opacity"), t) < 0.3:
            return False
        oid = str(o.get("parent") or "")
    return True


def _emphasised(o: Mapping[str, Any], a: float, b: float) -> bool:
    """Whether the object (or a part of it) is already highlighted somewhere in [a, b]."""
    ch = o.get("ch") or {}
    return any(
        seg.get("k") == "pulse" and float(seg["t0"]) < b and float(seg["t1"]) > a
        for name in ("emph", "marks")
        for seg in (ch.get(name) or {}).get("segs") or []
    )


def _parts(o: Mapping[str, Any]) -> list[tuple[str, list[str]]]:
    """A diagram's named parts and the words that name each (its id and its label)."""
    if o.get("type") != "diagram":
        return []
    params = o.get("params") or {}
    out = []
    for kind in PARTS:
        for p in params.get(kind) or []:
            if isinstance(p, Mapping) and p.get("id"):
                pid = str(p["id"])
                tokens = _name_tokens(pid) + _name_tokens(str(p.get("label") or ""))
                if tokens:
                    out.append((pid, tokens))
    return out


def _area(params: Mapping[str, Any]) -> float:
    """A rough size of a shape from its params (0 when they do not say)."""

    def num(name: str) -> float:
        v = params.get(name)
        return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else 0.0

    return max(
        num("width") * num("height"), 4 * num("radius") ** 2, 4 * num("rx") * num("ry"), num("size") ** 2
    )


def _naming(objects: Mapping[str, Mapping[str, Any]], dsl: Mapping[str, Any]) -> dict[str, list[str]]:
    """The words that name each object: its id, its name, and the text of a label that points at it
    (`pointTo` an anchor of it, or a `label` action on it). A label merely placed beside an object may
    be about something else (a distance's label next to a block)."""
    out = {
        oid: _name_tokens(oid) + _name_tokens(str((o.get("metadata") or {}).get("name") or ""))
        for oid, o in objects.items()
    }
    links: list[tuple[str, Any]] = []
    for o in dsl.get("objects") or []:
        params = o.get("params") or {}
        if isinstance(params.get("pointTo"), str):
            links.append((params["pointTo"].split(".")[0], params.get("text")))
    for a in dsl.get("timeline") or []:
        if a.get("action") == "label":
            links.append((str(a.get("target")), a.get("text")))
    for oid, text in links:
        if oid in out and isinstance(text, str):
            out[oid] += _name_tokens(text)
    return out


def keep_moving(
    dsl: Mapping[str, Any],
    compiled: Mapping[str, Any],
    beats: Sequence[Mapping[str, Any]],
    words: Mapping[str, Sequence[tuple[str, float]]] | None = None,
) -> dict[str, Any] | None:
    """The scene with gentle highlights in its long still stretches of narration, or None when nothing
    sits still that long (or nothing on screen can take one). Each highlight goes where the still parts
    on either side of it stay within `STILL_S` (the longest stretches first), as near the middle as a
    named thing allows. `words`: per beat, each spoken word and when it starts (seconds from the beat's
    start), to land a highlight on the thing as it is named."""
    stretches = sorted(
        ((a, b) for a, b in still_stretches(compiled, beats) if b - a > STILL_S), key=lambda s: s[0] - s[1]
    )
    if not stretches or not beats:
        return None
    objects = {str(o.get("id")): o for o in compiled.get("objects") or []}
    naming = _naming(objects, dsl)
    acts = [a for a in (compiled.get("actions") or {}).values() if a.get("target")]
    said = sorted(
        (float(beat["start"]) + float(start) - SYNC_LEAD_S, word)
        for beat in beats
        for word, start in (words or {}).get(str(beat["id"]), [])
    )
    out = copy.deepcopy(dict(dsl))
    timeline = list(out.get("timeline") or [])
    taken = {str(a.get("id")) for a in timeline}
    added = 0

    def root(oid: str) -> str:
        """The outermost group an object sits in (itself when in none)."""
        seen = {oid}
        while str(objects[oid].get("parent") or "") in objects and objects[oid]["parent"] not in seen:
            oid = str(objects[oid]["parent"])
            seen.add(oid)
        return oid

    def candidates(at: float) -> list[str]:
        """Picture objects on screen through a highlight starting at `at`, none highlighted then (a
        group draws nothing of its own to highlight: its parts are the candidates)."""
        return [
            oid
            for oid, o in objects.items()
            if o.get("type") not in NOT_PICTURE
            and o.get("type") != "group"
            and not o.get("decorative")
            and o.get("layer") != "background"
            and all(_visible(objects, oid, x) for x in (at - 0.5, at, at + PULSE_S))
            and not _emphasised(o, at - 0.2, at + PULSE_S + 0.2)
        ]

    def named(at: float, word: str) -> tuple[str, list[str] | None] | None:
        """What on screen the word names: an object (the biggest, when several are: a cart's body, not
        its wheel), or a diagram's part (shown by then)."""
        shown = candidates(at)
        hits = [oid for oid in shown if _names(naming[oid], word)]
        if hits:
            return max(hits, key=lambda oid: _area(objects[oid].get("params") or {})), None
        for oid in shown:
            ch = objects[oid].get("ch") or {}
            for pid, tokens in _parts(objects[oid]):
                if _names(tokens, word) and _level(ch.get(f"term.{pid}"), at) >= 0.99:
                    return oid, [pid]
        return None

    def latest(at: float) -> str | None:
        """What the picture last showed or changed by `at`: the newest thing on screen (a group with
        all its parts: what moves a group moves them), and of it the biggest part (a cart's body, not
        the wheel that popped in last)."""
        shown = candidates(at)
        if not shown:
            return None
        changed: dict[str, float] = {}
        for act in acts:
            if float(act["start"]) <= at and act["target"] in objects:
                r = root(act["target"])
                changed[r] = max(changed.get(r, -1.0), float(act["end"]))
        return max(
            shown,
            key=lambda oid: (changed.get(root(oid), -1.0), _area(objects[oid].get("params") or {})),
        )

    span = STILL_S + PULSE_S + PAD_S  # a highlight and the stillness one may leave after it
    for a, b in stretches:
        cursor = a  # where the stillness still to break up begins
        while b - cursor > STILL_S and added < MOST:
            k = int((b - cursor) // STILL_S)  # highlights the rest of this stretch needs
            ideal = cursor + (b - cursor) / (k + 1) - PULSE_S / 2
            lo = max(cursor + EDGE_S, b - k * span, ideal - STILL_S / 2)
            hi = min(cursor + STILL_S, b - PULSE_S, ideal + STILL_S / 2)
            if hi < lo:
                lo = hi = min(max(ideal, cursor + EDGE_S), b - PULSE_S)
            pick: tuple[float, str, list[str] | None, str] | None = None
            # The thing the narration names near the middle, as it is named.
            for at, word in sorted((x for x in said if lo <= x[0] <= hi), key=lambda x: abs(x[0] - ideal)):
                hit = named(at, word)
                if hit is not None:
                    oid, terms = hit
                    style = "glow" if terms is None and objects[oid].get("type") in LARGE else "pulse"
                    pick = (at, oid, terms, style)
                    break
            # Otherwise a soft glow on what the picture last showed: the narration is still on it.
            if pick is None:
                at = min(max(ideal, lo), hi)
                last = latest(at)
                if last is None:
                    break
                pick = (at, last, None, "glow")
            at, oid, terms, style = pick
            beat = next((bt for bt in beats if float(bt["start"]) <= at < float(bt["end"])), beats[0])
            n = added + 1
            while f"{PREFIX}{n}" in taken:
                n += 1
            taken.add(f"{PREFIX}{n}")
            action: dict[str, Any] = {
                "id": f"{PREFIX}{n}",
                "action": "highlight",
                "target": oid,
                "style": style,
                "duration": PULSE_S,
                "start": {"at": f"beat:{beat['id']}", "delay": round(max(0.0, at - float(beat["start"])), 2)},
            }
            if terms:
                action["terms"] = terms
            timeline.append(action)
            added += 1
            cursor = at + PULSE_S + PAD_S
    if not added:
        return None
    out["timeline"] = timeline
    return out


def kept_moving(dsl: Mapping[str, Any]) -> int:
    """How many highlights this pass has added to a scene."""
    return sum(1 for a in dsl.get("timeline") or [] if str(a.get("id", "")).startswith(PREFIX))
