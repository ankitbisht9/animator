"""Strict Structured Outputs schemas from Pydantic models.

Strict mode needs every property required (optional = nullable), `additionalProperties: false`
on every object and only a subset of keywords. Constraints Pydantic would emit (lengths,
ranges) are dropped here and enforced by validating the parsed output with the model itself.
"""

from __future__ import annotations

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
