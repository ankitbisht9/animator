"""What the scene writer of a cost-saver lesson is told about saved drawings (ADR-055): the scene
schema with one more kind of object (`saved`), and, per scene, the drawings it may use. The list is
in the user message, after the cached instructions, so the instructions stay the same for every
scene and cache."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any

from animator.objects.matching import Offer
from animator.objects.resolve import STAGE_H, STAGE_W

SAVED_DESCRIPTION = (
    "A saved drawing offered for this scene (listed in the message): exactly that thing, drawn in "
    "detail, as one object. `ref` is its name from the list; size it with `width` (or `height`): it "
    "keeps its proportions. Its parts are anchors (`<id>.<part>`) for labels and arrows."
)


def with_saved(schema: dict[str, Any]) -> dict[str, Any]:
    """A copy of a scene (or patch) schema whose objects may also be saved drawings."""
    out = copy.deepcopy(schema)
    defs = out.get("$defs")
    if not isinstance(defs, dict) or "Object" not in defs or "Object_icon" not in defs:
        raise ValueError("the scene schema has no object kinds to extend")
    variant = copy.deepcopy(defs["Object_icon"])  # the fields every object has
    props = variant["properties"]
    props["type"] = {"type": "string", "enum": ["saved"]}
    props["params"] = {
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "The drawing's name, exactly as offered"},
            "width": {
                "type": ["number", "null"],
                "description": "Width on the canvas in px (null: its usual size); the height follows",
            },
            "height": {
                "type": ["number", "null"],
                "description": "Or its height in px (with width null)",
            },
        },
        "required": ["ref", "width", "height"],
        "additionalProperties": False,
    }
    variant["description"] = SAVED_DESCRIPTION
    defs["Object_saved"] = variant
    union = defs["Object"]
    union["anyOf"] = [*union["anyOf"], {"$ref": "#/$defs/Object_saved"}]
    return out


def _px(v: float) -> str:
    return f"{round(v):d}"


def offer_text(offers: Sequence[Offer]) -> str:
    """The drawings a scene may use, for its user message."""
    if not offers:
        return (
            "Saved drawings for this scene: none match it exactly. Draw everything yourself, as usual "
            "(do not use `saved` objects)."
        )
    lines = [
        "Saved drawings for this scene (use one only where the scene shows exactly that thing, see "
        '"Saved drawings"; draw everything else yourself):'
    ]
    for o in offers:
        obj = o.obj
        hi = min(obj.size.max, STAGE_W, STAGE_H / obj.aspect)
        lo = min(obj.size.min, hi)
        usual = min(max(obj.size.default, lo), hi)
        parts = [f"{p} ({obj.parts[p]})" if obj.parts.get(p) else p for p in obj.anchor_names()]
        named = f" The brief names it: {', '.join(f'`{i}`' for i in o.for_ids)}." if o.for_ids else ""
        view = f", {obj.view}" if obj.view else ""
        lines.append(
            f"- `{obj.id}`: {obj.title}{view}. {obj.description.rstrip('.')}."
            f"{named}"
            + (f" Parts (anchors): {', '.join(parts)}." if parts else " No named parts.")
            + f" Width {_px(lo)} to {_px(hi)} px (usually {_px(usual)}); height = {obj.aspect:.2f} x width."
        )
    return "\n".join(lines)
