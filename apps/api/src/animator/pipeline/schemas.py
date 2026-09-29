"""Strict Structured Outputs schemas from Pydantic models.

Strict mode needs every property required (optional = nullable), `additionalProperties: false`
on every object and only a subset of keywords. Constraints Pydantic would emit (lengths,
ranges) are dropped here and enforced by validating the parsed output with the model itself.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

_KEEP = {
    "type",
    "properties",
    "required",
    "items",
    "enum",
    "anyOf",
    "$ref",
    "$defs",
    "description",
    "additionalProperties",
}


def _clean(node: Any) -> Any:
    if isinstance(node, list):
        return [_clean(x) for x in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for k, v in node.items():
        if k not in _KEEP:
            continue
        if k in ("properties", "$defs"):
            out[k] = {name: _clean(sub) for name, sub in v.items()}
        else:
            out[k] = _clean(v)
    if "const" in node:
        out["enum"] = [node["const"]]
    if out.get("type") == "object" or "properties" in out:
        out["type"] = "object"
        out["additionalProperties"] = False
        out["required"] = list(out.get("properties", {}))
    if "$ref" in out:  # strict mode: a $ref carries nothing else
        return {"$ref": out["$ref"]}
    return out


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema(mode="validation")
    return _clean(schema)  # type: ignore[no-any-return]


def _nullable_list(items: dict[str, Any], description: str) -> dict[str, Any]:
    return {"anyOf": [{"type": "array", "items": items}, {"type": "null"}], "description": description}


def patch_schema(scene: dict[str, Any]) -> dict[str, Any]:
    """A correction that names only what changes (a few objects or actions) instead of repeating the
    whole scene: far fewer output tokens, so corrections take seconds (ADR-033). Built from the
    engine's scene schema, so the objects and actions in it are exactly as strict."""
    p = scene["properties"]
    ids = {"type": "string"}
    props: dict[str, Any] = {
        "objects": {
            **p["objects"],
            "description": "Objects to add, or to replace completely (matched by id)",
        },
        "remove_objects": _nullable_list(ids, "Ids of objects to delete (their actions go too)"),
        "timeline": {
            **p["timeline"],
            "description": "Actions to add, or to replace completely (matched by id)",
        },
        "remove_actions": _nullable_list(ids, "Ids of actions to delete"),
        "solvers": {**p["solvers"], "description": "Solvers to add or replace (matched by id)"},
        "values": {**p["values"], "description": "Computed values to add or replace (matched by id)"},
        "duration": {"type": ["number", "null"], "description": "New scene length in seconds, or null"},
        "narration": {**p["narration"], "description": "The complete new narration, or null to keep it"},
        "camera": {**p["camera"], "description": "The new initial camera, or null to keep it"},
    }
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
        "$defs": scene["$defs"],
    }


def apply_patch(scene: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """The scene with a correction patch applied (see `patch_schema`)."""
    out = copy.deepcopy(scene)
    gone = set(patch.get("remove_objects") or [])
    dropped = set(patch.get("remove_actions") or [])

    def upsert(key: str, items: list[dict[str, Any]] | None, keep: Callable[[dict[str, Any]], bool]) -> None:
        current = [x for x in out.get(key) or [] if keep(x)]
        where = {x.get("id"): i for i, x in enumerate(current)}
        for item in items or []:
            i = where.get(item.get("id"))
            if i is None:
                where[item.get("id")] = len(current)
                current.append(item)
            else:
                current[i] = item
        out[key] = current

    upsert("objects", patch.get("objects"), lambda o: o.get("id") not in gone)
    upsert(
        "timeline",
        patch.get("timeline"),
        lambda a: a.get("id") not in dropped and a.get("target") not in gone,
    )
    upsert("solvers", patch.get("solvers"), lambda s: True)
    upsert("values", patch.get("values"), lambda v: True)
    for key in ("duration", "narration", "camera"):
        if patch.get(key) is not None:
            out[key] = patch[key]
    _reanchor(out["timeline"], {a.get("id"): a for a in scene.get("timeline") or []})
    return out


def _reanchor(timeline: list[dict[str, Any]], before: dict[Any, dict[str, Any]]) -> None:
    """Actions timed after/with an action the patch deleted start when that action would have, so
    no reference dangles."""
    kept = {a.get("id") for a in timeline}
    for action in timeline:
        start = action.get("start")
        for _ in range(len(before)):  # follow chains of deleted actions (bounded: no cycles)
            if not isinstance(start, dict):
                break
            ref = start.get("after") or start.get("with")
            if ref is None or ref in kept or ref not in before:
                break
            inherited = dict(before[ref].get("start") or {"at": 0})
            if start.get("delay"):
                inherited["delay"] = (inherited.get("delay") or 0) + start["delay"]
            action["start"] = start = inherited
