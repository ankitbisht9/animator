"""Saved drawings in a scene (ADR-055): the scene writer's short reference becomes the engine's
`figure`, with the drawing inline (a lesson plays without the library), and back again for a
correction round (the model sees what it wrote, not the drawing).

The scene writer writes:
    {"id": "heart", "type": "saved", "params": {"ref": "heart_exterior", "width": 360, "height": null}, ...}
which becomes:
    {"id": "heart", "type": "figure", "params": {"art": {...}, "width": 360, "ref": "heart_exterior@1"}, ...}

A reference to a drawing that was not offered for this scene is an error for the scene writer to fix
(use one that was, or draw the thing): it is never replaced by something similar, and the object is
left as written, so the engine rejects the scene too and it is never shown.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from animator.objects.models import SavedObject

SAVED = "saved"
FIGURE = "figure"
# The stage a drawing must fit in (the engine's stage: between the title and caption bars).
STAGE_W = 1504.0
STAGE_H = 620.0
# Style a drawing ignores: it has its own colors.
_PAINT = ("fill", "stroke", "text", "strokeWidth")


@dataclass
class Resolution:
    dsl: dict[str, Any]
    used: list[str] = field(default_factory=list)  # refs placed ("heart_exterior@1"), in order
    problems: list[dict[str, Any]] = field(default_factory=list)  # engine-style issues
    flagged: list[str] = field(default_factory=list)  # paths of objects left unresolved
    # Actions made to fit a drawing: it appears with `create` (a `draw` or `write` of it becomes one).
    adjusted: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def issue(code: str, path: str, message: str, suggestion: str | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "path": path,
        "message": message,
        "suggestion": suggestion,
        "severity": "error",
        "source": "semantic",
    }


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) and v > 0 else None


def fit_width(obj: SavedObject, width: Any = None, height: Any = None) -> float:
    """The drawing's width on the canvas: as asked (width, else height through its proportions,
    else its usual size), kept within its legible range and the stage."""
    w, h = _num(width), _num(height)
    size = w if w is not None else (h / obj.aspect if h is not None else obj.size.default)
    hi = min(obj.size.max, STAGE_W, STAGE_H / obj.aspect)
    lo = min(obj.size.min, hi)
    return round(max(lo, min(hi, size)), 1)


def resolve(dsl: dict[str, Any], offers: Mapping[str, SavedObject]) -> Resolution:
    """Every `saved` object of a scene as a `figure`, when it names a drawing offered to the scene."""
    objects = dsl.get("objects")
    if not isinstance(objects, list) or not any(
        isinstance(o, dict) and o.get("type") == SAVED for o in objects
    ):
        return Resolution(dsl)
    res = Resolution(dsl)
    out: list[Any] = []
    for i, o in enumerate(objects):
        if not isinstance(o, dict) or o.get("type") != SAVED:
            out.append(o)
            continue
        raw = o.get("params")
        params: dict[str, Any] = raw if isinstance(raw, dict) else {}
        ref = str(params.get("ref") or "").strip().split("@")[0]
        obj = offers.get(ref)
        if obj is None:
            offered = ", ".join(f"`{k}`" for k in sorted(offers)) or "none"
            res.problems.append(
                issue(
                    "SAVED_NOT_OFFERED",
                    f"/objects/{i}/params/ref",
                    f"`{ref or '(empty)'}` is not a saved drawing offered for this scene "
                    f"(offered: {offered}).",
                    f"Use an offered drawing only if it is exactly what '{o.get('id')}' shows; "
                    "otherwise draw it yourself from shapes.",
                )
            )
            res.flagged.append(f"/objects/{i}")
            out.append(o)
            continue
        fig = {k: v for k, v in o.items() if k != "params"}
        fig["type"] = FIGURE
        fig["params"] = {
            "art": obj.art.engine(),
            "width": fit_width(obj, params.get("width"), params.get("height")),
            "ref": obj.ref,
        }
        style = fig.get("style")
        if isinstance(style, dict):
            kept = {k: v for k, v in style.items() if k not in _PAINT}
            fig["style"] = kept if any(v is not None for v in kept.values()) else None
        given = fig.get("metadata")
        meta: dict[str, Any] = given if isinstance(given, dict) else {}
        fig["metadata"] = {
            "name": meta.get("name") or obj.title[:80],
            "description": meta.get("description") or obj.title[:240],
            "concept": obj.concept[:80],
        }
        out.append(fig)
        res.used.append(obj.ref)
    res.dsl = {
        **dsl,
        "objects": _part_places(out, offers, res),
        "timeline": _entrances(dsl.get("timeline"), out, res),
    }
    _check_focus(res, offers, objects)
    return res


def _part_places(objects: list[Any], offers: Mapping[str, SavedObject], res: Resolution) -> list[Any]:
    """Something placed beside a drawing's part (`relativeTo: "heart.right_atrium"`, which the engine
    refuses: it places beside objects) goes beside the drawing; a label also gets its leader line to
    that part, as the prompt asks. A sure fix, made without a correction round."""
    anchors: dict[str, set[str]] = {}
    for o in objects:
        if isinstance(o, dict) and o.get("type") == FIGURE:
            obj = offers.get(str((o.get("params") or {}).get("ref") or "").split("@")[0])
            if obj is not None:
                anchors[str(o.get("id"))] = set(obj.anchor_names())
    if not anchors:
        return objects
    out: list[Any] = []
    for o in objects:
        place = o.get("place") if isinstance(o, dict) else None
        if not isinstance(place, dict):
            out.append(o)
            continue
        fig, _, part = str(place.get("relativeTo") or "").partition(".")
        if not part or part not in anchors.get(fig, set()):
            out.append(o)
            continue
        o = {**o, "place": {**place, "relativeTo": fig}}
        params = o.get("params")
        if o.get("type") == "label" and isinstance(params, dict) and not params.get("pointTo"):
            o["params"] = {**params, "pointTo": f"{fig}.{part}"}
        res.adjusted.append(f"{o.get('id')}.place")
        out.append(o)
    return out


def _check_focus(res: Resolution, offers: Mapping[str, SavedObject], written: list[Any]) -> None:
    """A part brought into focus (`transform` of `focus`) must be one of the drawing's parts."""
    parts: dict[str, list[str]] = {}
    for o in written:
        if isinstance(o, dict) and o.get("type") == SAVED and isinstance(o.get("params"), dict):
            obj = offers.get(str(o["params"].get("ref") or "").strip().split("@")[0])
            if obj is not None:
                parts[str(o.get("id"))] = obj.art.parts
    timeline = res.dsl.get("timeline")
    if not parts or not isinstance(timeline, list):
        return
    for i, a in enumerate(timeline):
        if not isinstance(a, dict) or a.get("action") != "transform" or a.get("target") not in parts:
            continue
        for j, c in enumerate(a.get("changes") or []):
            wrong = (
                isinstance(c, dict)
                and c.get("param") == "focus"
                and c.get("to") not in ("", None)
                and c.get("to") not in parts[a["target"]]
            )
            if wrong:
                res.problems.append(
                    issue(
                        "SAVED_PART",
                        f"/timeline/{i}/changes/{j}/to",
                        f"`{a['target']}` has no part '{c.get('to')}'.",
                        "Its parts: " + ", ".join(parts[a["target"]]) + ' ("" ends the focus).',
                    )
                )


def _entrances(timeline: Any, objects: list[Any], res: Resolution) -> Any:
    """A drawing appears with `create` (pop, fade, grow): a `draw` or `write` of one would be the
    engine's error, with one sure fix, made here instead of in a correction round. Drawing it in
    reverse (taking it away) is its removal."""
    figures = {o.get("id") for o in objects if isinstance(o, dict) and o.get("type") == FIGURE}
    if not figures or not isinstance(timeline, list):
        return timeline
    out: list[Any] = []
    for a in timeline:
        if isinstance(a, dict) and a.get("target") in figures and a.get("action") in ("draw", "write"):
            back = a.get("action") == "draw" and a.get("reverse") is True
            a = {k: v for k, v in a.items() if k not in ("reverse", "terms", "by")}
            a["action"] = "remove" if back else "create"
            res.adjusted.append(str(a.get("id")))
        out.append(a)
    return out


def compact(dsl: dict[str, Any]) -> dict[str, Any]:
    """A scene as its writer wrote it: each placed drawing back as its short reference (corrections
    see their own scene, not hundreds of path points)."""
    objects = dsl.get("objects")
    if not isinstance(objects, list) or not any(_placed(o) for o in objects):
        return dsl
    out: list[Any] = []
    for o in objects:
        if not _placed(o):
            out.append(o)
            continue
        p = o["params"]
        c = {k: v for k, v in o.items() if k != "params"}
        c["type"] = SAVED
        c["params"] = {"ref": str(p["ref"]).split("@")[0], "width": p.get("width"), "height": None}
        out.append(c)
    return {**dsl, "objects": out}


def _placed(o: Any) -> bool:
    return (
        isinstance(o, dict)
        and o.get("type") == FIGURE
        and isinstance(o.get("params"), dict)
        and isinstance(o["params"].get("ref"), str)
    )


def placed_refs(dsl: dict[str, Any] | None) -> list[str]:
    """The saved drawings a finished scene shows ("heart_exterior@1"), in order."""
    return [o["params"]["ref"] for o in (dsl or {}).get("objects") or [] if _placed(o)]
