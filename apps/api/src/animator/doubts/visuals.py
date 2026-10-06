"""The picture of an answer (ADR-050), built by code from the tutor's reply: the model never writes
scene DSL for a doubt, so there is nothing to correct and no second call.

- A lesson picture: the scene starts from the lesson's own picture (a SceneState: the moment the hand
  went up, or an earlier scene's end) and, while each sentence is said, the things it talks about glow
  and a short note points at the first of them.
- A board: a title, an icon, an equation and key-point cards written as they are said.

Each builder returns variants from fullest to plainest; the service keeps the first one the engine
compiles (a note that does not fit is dropped, never sent back to the model)."""

from __future__ import annotations

import re
from typing import Any

from animator.doubts.models import DoubtAnswer
from animator.pipeline.layout_plan.figures import TEXT_TYPES
from animator.pipeline.layout_plan.regions import Box, regions, slot_box

WORDS_PER_S = 2.3
NOTE_WORDS = 7
MAX_PICTURE_NOTES = 3
MAX_CARDS = 4
CORNERS = ("stage.top_right", "stage.top_left", "stage.bottom_right", "stage.bottom_left")
CARD_ROLES = ("accent", "blue", "green", "purple")
_WORD = re.compile(r"\S+")


def sentence_seconds(text: str) -> float:
    """About how long a sentence takes to say (the voice fits the scene to itself afterwards)."""
    return round(len(_WORD.findall(text)) / WORDS_PER_S + 0.45, 2)


def short(text: str | None, words: int = NOTE_WORDS) -> str | None:
    if not text or not text.strip():
        return None
    parts = text.replace("**", "").split()
    return " ".join(parts[:words]).rstrip(",;:") if parts else None


LEAD_S = 0.3  # a moment before the first sentence
END_S = 0.8  # and after the last


def narration(answer: DoubtAnswer) -> tuple[list[dict[str, Any]], float]:
    """The answer's sentences as narration beats, each starting when the one before is said (the voice
    fits them to itself afterwards), and how long the scene runs."""
    beats = []
    at = LEAD_S
    for i, b in enumerate(answer.beats):
        beats.append({"id": f"b{i + 1}", "text": b.text.strip(), "at": round(at, 2)})
        at += sentence_seconds(b.text)
    return beats, round(max(3.0, at + END_S), 2)


def visible_objects(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        oid: o
        for oid, o in (state.get("objects") or {}).items()
        if o.get("lifecycle") == "active" and o.get("visible") and float(o.get("opacity") or 0) >= 0.05
    }


def _corner(layout: str, state: dict[str, Any], target: dict[str, Any]) -> str:
    """The stage corner a note goes in: the one that covers the least of the picture, then the one
    farthest from what it points at."""
    seen = [Box(**o["bbox"]) for o in visible_objects(state).values() if isinstance(o.get("bbox"), dict)]
    tb = target.get("bbox") or {}
    tx = float(tb.get("x", 0)) + float(tb.get("w", 0)) / 2
    ty = float(tb.get("y", 0)) + float(tb.get("h", 0)) / 2

    def cost(slot: str) -> tuple[float, float]:
        b = slot_box(layout, slot)
        covered = sum(b.overlap(s) for s in seen)
        return round(covered / 1000), -((b.cx - tx) ** 2 + (b.cy - ty) ** 2)

    return min(CORNERS, key=cost)


def picture_variants(
    answer: DoubtAnswer, state: dict[str, Any], source: dict[str, Any], scene_id: str
) -> list[dict[str, Any]]:
    """The lesson's picture as the scene (it continues from `state`), with what each sentence talks
    about glowing; variants: with notes, without notes, and the picture alone."""
    layout = str(source.get("layout") or "stage_with_caption")
    shown = visible_objects(state)
    beats, duration = narration(answer)
    glows: list[dict[str, Any]] = []
    notes: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    last: str | None = None
    for i, b in enumerate(answer.beats):
        bid = f"b{i + 1}"
        focus = [f for f in dict.fromkeys(b.focus) if f in shown][:2]
        for k, fid in enumerate(focus):
            glows.append(
                {
                    "id": f"doubt_glow_{i + 1}_{k + 1}",
                    "action": "highlight",
                    "target": fid,
                    "style": "box" if shown[fid].get("type") in TEXT_TYPES else "glow",
                    "start": {"at": f"beat:{bid}", "delay": 0.15},
                    "duration": round(max(0.8, 0.65 * sentence_seconds(b.text)), 2),
                }
            )
        text = short(b.note)
        if text and focus and len(notes) < MAX_PICTURE_NOTES:
            nid = f"doubt_note_{i + 1}"
            obj = {
                "id": nid,
                "type": "callout",
                "params": {"text": text, "target": f"{focus[0]}.center", "maxWidth": 380},
                "place": {"slot": _corner(layout, state, shown[focus[0]])},
            }
            actions = [
                {
                    "id": f"show_{nid}",
                    "action": "create",
                    "target": nid,
                    "start": {"at": f"beat:{bid}", "delay": 0.2},
                }
            ]
            if last is not None:
                actions.append(
                    {"id": f"hide_{last}", "action": "remove", "target": last, "start": {"at": f"beat:{bid}"}}
                )
            notes.append((obj, actions))
            last = nid

    def scene(with_glows: bool, with_notes: bool) -> dict[str, Any]:
        objects = [o for o, _ in notes] if with_notes else []
        timeline = (glows if with_glows else []) + (
            [a for _, acts in notes for a in acts] if with_notes else []
        )
        return {
            "dsl_version": "1.0",
            "id": scene_id,
            "title": answer.title,
            "duration": duration,
            "layout": layout,
            "theme": source.get("theme") or "educational_clean",
            "transition_in": {"type": "continue"},
            "narration": {"beats": beats},
            "objects": objects,
            "timeline": timeline,
        }

    variants = [scene(True, True)] if notes else []
    variants += [scene(True, False), scene(False, False)]
    return variants


def brief_glows(dsl: dict[str, Any]) -> dict[str, Any] | None:
    """The scene with every glow short (when the voice made its sentences shorter than estimated and a
    glow would outlast the scene); None when it has none."""
    glows = [a for a in dsl.get("timeline") or [] if a.get("action") == "highlight"]
    if not glows:
        return None
    for a in glows:
        a["duration"] = 0.8
    return dsl


def board_variants(answer: DoubtAnswer, scene_id: str, icons: set[str]) -> list[dict[str, Any]]:
    """A clean board: the title, an icon, an equation and key-point cards appearing as they are said;
    variants drop the equation, the icon, then cards from the last, down to the title alone."""
    layout = "stage_with_caption"
    stage = regions(layout)["stage"]
    beats, duration = narration(answer)
    n = len(answer.beats)
    cards = [(i, text) for i, b in enumerate(answer.beats) if (text := short(b.note))][:MAX_CARDS]
    icon = answer.visual.icon if answer.visual.icon in icons else None
    eq = answer.visual.equation or None
    eq_beat = min(max(1, answer.visual.equation_beat or 1), n)

    def scene(with_icon: bool, with_eq: bool, n_cards: int) -> dict[str, Any]:
        objects: list[dict[str, Any]] = [
            {
                "id": "doubt_title",
                "type": "text",
                "params": {"text": answer.title, "size": "heading", "weight": "bold", "maxWidth": 1300},
                "place": {"slot": "title_bar"},
            }
        ]
        timeline: list[dict[str, Any]] = [
            {
                "id": "write_doubt_title",
                "action": "write",
                "target": "doubt_title",
                "start": {"at": 0.1},
                "duration": 0.6,
            }
        ]
        column_x = stage.x + stage.w * (0.6 if with_icon else 0.5)
        width = 640 if with_icon else 820
        if with_icon:
            objects.append(
                {
                    "id": "doubt_icon",
                    "type": "icon",
                    "params": {"name": icon, "size": 190, "role": "accent", "badge": True},
                    "place": {"x": round(stage.x + stage.w * 0.2), "y": round(stage.cy)},
                }
            )
            timeline.append(
                {
                    "id": "show_doubt_icon",
                    "action": "create",
                    "target": "doubt_icon",
                    "start": {"at": "beat:b1"},
                }
            )
        above: str | None = None
        if with_eq and eq:
            objects.append(
                {
                    "id": "doubt_eq",
                    "type": "equation",
                    "params": {"parts": [p.model_dump() for p in eq], "box": True, "size": 54},
                    "place": {"x": round(column_x), "y": round(stage.y + 70), "anchor": "top"},
                }
            )
            timeline.append(
                {
                    "id": "write_doubt_eq",
                    "action": "write",
                    "target": "doubt_eq",
                    "start": {"at": f"beat:b{eq_beat}"},
                    "duration": 1.2,
                }
            )
            above = "doubt_eq"
        for k, (i, text) in enumerate(cards[:n_cards]):
            cid = f"doubt_point_{k + 1}"
            place: dict[str, Any] = (
                {"relativeTo": above, "side": "below", "gap": 22}
                if above
                else {"x": round(column_x), "y": round(stage.y + 40), "anchor": "top"}
            )
            # A one-point card per key point: body-size text, readable on the small doubt stage too.
            objects.append(
                {
                    "id": cid,
                    "type": "card",
                    "params": {
                        "items": [text],
                        "marker": "arrow",
                        "size": "body",
                        "role": CARD_ROLES[k % len(CARD_ROLES)],
                        "width": width,
                    },
                    "place": place,
                }
            )
            timeline.append(
                {
                    "id": f"show_{cid}",
                    "action": "write",
                    "target": cid,
                    "start": {"at": f"beat:b{i + 1}", "delay": 0.2},
                    "duration": 0.8,
                }
            )
            above = cid
        return {
            "dsl_version": "1.0",
            "id": scene_id,
            "title": answer.title,
            "duration": duration,
            "layout": layout,
            "theme": "educational_clean",
            "narration": {"beats": beats},
            "objects": objects,
            "timeline": timeline,
        }

    variants = []
    for with_eq in [True, False] if eq else [False]:
        for with_icon in [True, False] if icon else [False]:
            variants.append(scene(with_icon, with_eq, len(cards)))
    variants += [scene(False, False, k) for k in range(len(cards) - 1, -1, -1)]
    return variants


def centre_column(dsl: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    """The board's column moved so that what it shows at the end sits in the middle of the stage, top
    to bottom (the cards' heights are only known once the engine has laid them out). None when it is
    centred already."""
    stage = regions(str(dsl.get("layout") or "stage_with_caption"))["stage"]
    ids = [
        o["id"]
        for o in dsl.get("objects") or []
        if o["id"] in ("doubt_eq",) or o["id"].startswith("doubt_point_")
    ]
    boxes = [Box(**state["objects"][i]["bbox"]) for i in ids if i in (state.get("objects") or {})]
    if not boxes:
        return None
    top = min(b.y for b in boxes)
    bottom = max(b.bottom for b in boxes)
    shift = round((stage.cy - (top + bottom) / 2), 1)
    if abs(shift) < 8:
        return None
    first = next((o for o in dsl["objects"] if o["id"] in ids), None)
    if first is None or "y" not in first["place"]:
        return None
    first["place"]["y"] = round(first["place"]["y"] + shift, 1)
    return dsl
