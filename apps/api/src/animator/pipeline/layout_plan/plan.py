"""The stage plan (ADR-047): what the scene writer says about the layout, in a few lines, instead of
working out every position. Each group is a set of items (the things that stay in place: a drawing,
an icon, a card) arranged in a row, a column, a grid, a cycle, or freely (kept as drawn). Groups shown
at the same time share their region (stacked in `direction`); groups shown at different times reuse
it. The planner measures every item with the engine and computes the exact sizes and places."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Arrange = Literal["row", "column", "grid", "cycle", "free"]
ARRANGE = ("row", "column", "grid", "cycle", "free")


class PlanGroup(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = Field(default="g", max_length=48)
    region: str | None = None
    arrange: Arrange = "free"
    items: list[str] = Field(default_factory=list, max_length=16)
    align: Literal["center", "top", "bottom", "left", "right"] | None = None
    columns: int | None = Field(default=None, ge=1, le=6)
    connected: bool | None = None


RELATIONS = ("on", "along", "inside", "under", "above", "below", "near")


class PlanRelation(BaseModel):
    """What a thing sits on, in or along (ADR-048): checked at every moment it should hold, snapped right."""

    model_config = ConfigDict(extra="ignore")
    subject: str = Field(max_length=48)
    relation: Literal["on", "along", "inside", "under", "above", "below", "near"]
    object: str = Field(max_length=48)
    when: Literal["always", "end"] | None = None


class StagePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")
    direction: Literal["column", "row"] | None = None
    groups: list[PlanGroup] = Field(default_factory=list, max_length=8)
    relations: list[PlanRelation] = Field(default_factory=list, max_length=24)


def parse_plan(data: Any) -> StagePlan | None:
    """The plan a scene reply carries, or None when it has none or it cannot be read (the scene is
    then laid out as written, the classic way)."""
    if not isinstance(data, dict):
        return None
    groups = data.get("groups")
    if not isinstance(groups, list):
        groups = []
    cleaned = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        g = {k: v for k, v in g.items() if v is not None}
        if g.get("arrange") not in ARRANGE:
            g["arrange"] = "free"
        g["items"] = [str(i) for i in g.get("items") or [] if isinstance(i, str) and i.strip()][:16]
        if g["items"]:
            cleaned.append(g)
    relations = []
    for r in data.get("relations") or []:
        if not isinstance(r, dict):
            continue
        try:
            rel = PlanRelation.model_validate({k: v for k, v in r.items() if v is not None})
        except ValidationError:
            continue
        if rel.subject != rel.object:
            relations.append(rel)
    try:
        plan = StagePlan.model_validate(
            {"direction": data.get("direction"), "groups": cleaned[:8], "relations": relations[:24]}
        )
    except ValidationError:
        return None
    return plan if plan.groups or plan.relations else None


def _nullable(schema: dict[str, Any], description: str) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}], "description": description}


def plan_schema() -> dict[str, Any]:
    """The `stage` property of a planned scene, for strict structured outputs."""
    group = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "description": "A short id for the group, e.g. 'flow'"},
            "region": _nullable(
                {"type": "string", "enum": ["stage", "equation_panel", "left", "right", "hero"]},
                "The layout region the group fills (null: the main region)",
            ),
            "arrange": {
                "type": "string",
                "enum": list(ARRANGE),
                "description": "row: left to right, in order; column: top to bottom; grid: rows of "
                "`columns`; cycle: around a circle, clockwise from the top; free: kept as you drew "
                "them, only sized and centred",
            },
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The items, in order: each the id (and id prefix) of one thing that stays "
                "in place, e.g. 'press' for press, press_top, press_tray",
            },
            "align": _nullable(
                {"type": "string", "enum": ["center", "top", "bottom", "left", "right"]},
                "How a row's items line up (bottom: standing on one floor) or a column's (null: centre)",
            ),
            "columns": _nullable({"type": "integer"}, "For a grid: items per row (null: automatic)"),
            "connected": _nullable(
                {"type": "boolean"}, "True when arrows join consecutive items (more room between them)"
            ),
        },
        "required": ["id", "region", "arrange", "items", "align", "columns", "connected"],
    }
    relation = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "subject": {"type": "string", "description": "The thing placed: an object id or item id"},
            "relation": {
                "type": "string",
                "enum": list(RELATIONS),
                "description": "on: its bottom rests on the object's top (side view); along: its centre "
                "is on the object's line (a road from above, a wire, a route); inside: within the object; "
                "under: its top touches the object's bottom; above / below: wholly above / below; near: "
                "close to it",
            },
            "object": {"type": "string", "description": "The thing it relates to: an object id or item id"},
            "when": _nullable(
                {"type": "string", "enum": ["always", "end"]},
                "always: whenever both are on stage (null means always); end: at the end of the scene",
            ),
        },
        "required": ["subject", "relation", "object", "when"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "description": "The stage plan: how the things on stage are arranged, and what sits on, in or "
        "along what. The engine computes exact sizes and positions from it and checks every relation.",
        "properties": {
            "direction": _nullable(
                {"type": "string", "enum": ["column", "row"]},
                "How groups shown at the same time share a region: column (stacked, the default) or row "
                "(side by side)",
            ),
            "groups": {"type": "array", "items": group},
            "relations": {
                "type": "array",
                "items": relation,
                "description": "What sits on, in or along what: every vehicle on its road, everything "
                "resting on a surface or held in a container (empty only when nothing does)",
            },
        },
        "required": ["direction", "groups", "relations"],
    }


def planned_scene_schema(scene: dict[str, Any]) -> dict[str, Any]:
    """The engine's scene schema with the stage plan, written just before the objects (the plan is
    decided first, then the things in it are drawn)."""
    props: dict[str, Any] = {}
    for key, value in scene["properties"].items():
        if key == "objects":
            props["stage"] = plan_schema()
        props[key] = value
    if "stage" not in props:
        props["stage"] = plan_schema()
    return {**scene, "properties": props, "required": list(props)}


def planned_patch_schema(patch: dict[str, Any]) -> dict[str, Any]:
    """A correction patch that may also replace the stage plan."""
    props = dict(patch["properties"])
    props["stage"] = {
        "anyOf": [plan_schema(), {"type": "null"}],
        "description": "The complete new stage plan, or null to keep it",
    }
    return {**patch, "properties": props, "required": list(props)}
