"""Sure repairs of a planned scene draft before the engine sees it (ADR-047): slips whose meaning is
plain, fixed without a model call. Each was seen in recorded drafts; the classic flow sends them back
to the model instead.

- A part of a group that `follow`s what it is pinned to: the group's position would apply twice
  (parts pinned to `group.center` land far outside the frame). It is placed once, where meant.
- An action started `{"at": "after:a3"}` or `{"at": "with:a3"}`: that is `after` / `with`. An action
  started `{"at": "a3"}` (an action's id): it starts after that action.
- A `parent` that is not a group (a polygon, a rectangle): the object is placed in world coordinates
  anyway, so it is kept where it is, without the parent.
- A slot the scene's layout does not have (`stage.center` in a `split` layout, whose regions are
  `left` and `right`): the same point on the stage, by coordinates.
- Parts of a group placed by offsets from the group's centre ({"x": -42, "y": -23}): the engine reads
  coordinates on the canvas, so the parts would sit in its corner. They become the group's place plus
  the offset (the commonest slip of all; it also strands parts in classic scenes).
"""

from __future__ import annotations

import copy
import re
from typing import Any

from animator.pipeline.layout_plan.figures import WORLD_SPACE
from animator.pipeline.layout_plan.regions import REGIONS, default_region, regions, slot_box

_TIME_REF = re.compile(r"^(cue|beat|beat_end):[a-z][a-z0-9_]*$")
# Drawn from their endpoints: their coordinates are already in the parent's frame.
_ENDPOINTS = WORLD_SPACE
# Offsets from a group's centre: all within this of 0,0, centred within LOCAL_CENTRE of it.
LOCAL_REACH = 420.0
LOCAL_CENTRE = 160.0


def _raw(place: Any) -> tuple[float, float] | None:
    if (
        isinstance(place, dict)
        and isinstance(place.get("x"), (int, float))
        and isinstance(place.get("y"), (int, float))
    ):
        return float(place["x"]), float(place["y"])
    return None


def _origin(group: dict[str, Any], layout: str | None) -> tuple[float, float]:
    """Where a group's origin is: its raw place, its slot's centre, or the stage centre."""
    raw = _raw(group.get("place"))
    if raw is not None:
        return raw
    given = group.get("place")
    place: dict[str, Any] = given if isinstance(given, dict) else {}
    slot = place.get("slot")
    box = slot_box(layout, str(slot)) if slot else regions(layout)[default_region(layout)]
    given_off = place.get("offset")
    off: dict[str, Any] = given_off if isinstance(given_off, dict) else {}
    return box.cx + float(off.get("x") or 0), box.cy + float(off.get("y") or 0)


def _local_parts(out: dict[str, Any], objects: list[dict[str, Any]], groups: set[str]) -> list[str]:
    """Parts of a group given as offsets from the group's centre (all within a few hundred px of
    0,0, where nothing of a scene is ever drawn): the engine reads a part's coordinates on the canvas,
    so they become the group's place plus the offset. Their moves by coordinates near 0,0 too."""
    done: list[str] = []
    by_id = {str(o.get("id")): o for o in objects}
    for gid in sorted(groups):
        parts = [
            o
            for o in objects
            if o.get("parent") == gid and o.get("type") not in _ENDPOINTS and _raw(o.get("place"))
        ]
        if not parts:
            continue
        points = [_raw(o.get("place")) for o in parts]
        xs = [p[0] for p in points if p]
        ys = [p[1] for p in points if p]
        mean = (sum(xs) / len(xs), sum(ys) / len(ys))
        if max(abs(v) for v in xs + ys) > LOCAL_REACH or (mean[0] ** 2 + mean[1] ** 2) ** 0.5 > LOCAL_CENTRE:
            continue
        gx, gy = _origin(by_id[gid], out.get("layout"))
        moved = {str(o.get("id")) for o in parts}
        for o in parts:
            o["place"]["x"], o["place"]["y"] = round(gx + o["place"]["x"], 1), round(gy + o["place"]["y"], 1)
        for a in out.get("timeline") or []:
            to = a.get("to") if isinstance(a, dict) and a.get("action") == "move" else None
            p = _raw(to)
            if (
                isinstance(to, dict)
                and p is not None
                and a.get("target") in moved
                and max(map(abs, p)) <= LOCAL_REACH
            ):
                to["x"], to["y"] = round(gx + p[0], 1), round(gy + p[1], 1)
        done.append(f"{gid}: its parts' coordinates are offsets from its centre")
    return done


def _foreign_slots(out: dict[str, Any], objects: list[dict[str, Any]]) -> list[str]:
    """Slots of another layout (the full stage's `stage.center` in a split scene): their point, by
    coordinates."""
    done: list[str] = []
    layout = out.get("layout") or "stage_with_caption"
    own = regions(layout)
    for o in objects:
        place = o.get("place")
        if not isinstance(place, dict):
            continue
        slot = place.get("slot")
        if not isinstance(slot, str) or slot.split(".")[0] in own:
            continue
        other = next(
            (
                name
                for name in ("stage_with_caption", "stage_with_equation")
                if slot.split(".")[0] in REGIONS[name]
            ),
            None,
        )
        if other is None:
            continue
        box = slot_box(other, slot)
        given = place.get("offset")
        off: dict[str, Any] = given if isinstance(given, dict) else {}
        point = {
            "x": round(box.cx + float(off.get("x") or 0), 1),
            "y": round(box.cy + float(off.get("y") or 0), 1),
        }
        o["place"] = {**point, **({"anchor": place["anchor"]} if place.get("anchor") else {})}
        done.append(f"{o.get('id')}: slot '{slot}' is not in this layout")
    return done


def repair_draft(dsl: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The draft with its sure slips repaired, and what was repaired (empty: the draft as it was)."""
    out = copy.deepcopy(dsl)
    done: list[str] = []
    objects = [o for o in out.get("objects") or [] if isinstance(o, dict)]
    kinds = {str(o.get("id")): o.get("type") for o in objects}
    groups = {oid for oid, kind in kinds.items() if kind == "group"}
    done += _foreign_slots(out, objects)
    done += _local_parts(out, objects, groups)
    for o in objects:
        parent = o.get("parent")
        place = o.get("place")
        if parent in groups and isinstance(place, dict) and place.get("follow"):
            place["follow"] = False
            done.append(f"{o.get('id')}: placed once in its group")
        elif parent and parent in kinds and parent not in groups:
            o.pop("parent", None)
            done.append(f"{o.get('id')}: parent '{parent}' is not a group")
    actions = {str(a.get("id")) for a in out.get("timeline") or [] if isinstance(a, dict) and a.get("id")}
    for a in out.get("timeline") or []:
        start = a.get("start") if isinstance(a, dict) else None
        if not isinstance(start, dict):
            continue
        at = start.get("at")
        if not isinstance(at, str) or _TIME_REF.match(at):
            continue
        kind, _, ref = at.partition(":")
        if kind in ("after", "with") and ref in actions:
            start["at"], start[kind] = None, ref
        elif at in actions and at != a.get("id"):
            start["at"], start["after"] = None, at
        else:
            continue
        start.pop("at", None)
        done.append(f"{a.get('id')}: starts {'with' if kind == 'with' else 'after'} '{ref or at}'")
    return (out, done) if done else (dsl, [])
