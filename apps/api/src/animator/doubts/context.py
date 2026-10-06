"""What the tutor knows when a hand goes up (ADR-050): the lesson (what each scene says and shows), where
the learner is, what is on screen at that moment, the doubt's conversation so far, and how long the
answer may be.

The lesson part is the same for every doubt in a lesson and comes first, after the static
instructions, so the provider caches it; what changes per question comes last."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from animator.pipeline.language import language_line
from animator.pipeline.models import Generation, SceneRecord

WORDS_PER_S = 2.3  # a teacher's speaking rate, pauses included
MAX_ITEMS = 28  # things listed per picture
FIGURE_TYPES_SKIPPED = {"group"}


@dataclass(frozen=True)
class Turn:
    question: str
    answer: list[str]  # the sentences said


def playable(rec: SceneRecord) -> bool:
    return rec.dsl is not None and rec.end_state is not None and rec.status in ("ready", "degraded")


def scene_duration(rec: SceneRecord) -> float:
    d = rec.duration if rec.duration is not None else (rec.dsl or {}).get("duration")
    return float(d) if isinstance(d, (int, float)) else 0.0


def lesson_seconds(gen: Generation) -> float:
    return sum(scene_duration(s) for s in gen.scenes if playable(s))


def answer_cap(lesson_s: float, share: float, least: float, most: float) -> float:
    """The longest an answer may be (seconds): a share of the lesson's length, within [least, most]. A
    one-minute lesson does not get a two-minute answer."""
    return max(least, min(most, share * lesson_s))


def words_for(seconds: float) -> int:
    return math.floor(seconds * WORDS_PER_S)


def _said(rec: SceneRecord) -> list[str]:
    beats = ((rec.dsl or {}).get("narration") or {}).get("beats") or []
    return [str(b.get("text", "")).strip() for b in beats if str(b.get("text", "")).strip()]


def inventory(state: dict[str, Any] | None) -> list[str]:
    """What a picture shows, for the model to point at: each visible thing's id, kind and what it is."""
    if not state:
        return []
    out: list[str] = []
    for oid, o in (state.get("objects") or {}).items():
        if o.get("lifecycle") != "active" or not o.get("visible") or float(o.get("opacity") or 0) < 0.05:
            continue
        kind = str(o.get("type"))
        if kind in FIGURE_TYPES_SKIPPED or o.get("decorative") or oid.startswith("title"):
            continue
        meta = o.get("metadata") or {}
        what = " - ".join(str(x) for x in (meta.get("name"), meta.get("description")) if x)
        params = o.get("params") or {}
        text = params.get("text") or params.get("title") or params.get("label")
        if not what and isinstance(text, str) and text.strip():
            what = f'"{text.strip()[:60]}"'
        out.append(f"{oid} ({kind}{': ' + what if what else ''})")
        if len(out) >= MAX_ITEMS:
            break
    return out


def lesson_block(gen: Generation) -> str:
    """The lesson as the tutor knows it: every scene's narration and picture (stable per lesson)."""
    r = gen.request
    title = (gen.plan.title if gen.plan else None) or r.topic
    scenes = [s for s in gen.scenes if playable(s)]
    lines = [
        "LESSON",
        f'Title: "{title}"',
        f"Topic: {r.topic} | Audience: {r.audience} | Length: {round(lesson_seconds(gen))} s, "
        f"{len(scenes)} scenes",
        "",
    ]
    start = 0.0
    for rec in scenes:
        d = scene_duration(rec)
        lines.append(f'Scene {rec.index + 1} "{rec.title}" ({_clock(start)}-{_clock(start + d)}):')
        lines.append("  Said: " + " / ".join(_said(rec)))
        shown = inventory(rec.end_state)
        if shown:
            lines.append("  Picture at its end: " + "; ".join(shown))
        start += d
    later = [s.title for s in gen.scenes if not playable(s)]
    if later:
        lines.append("Not made yet: " + "; ".join(later))
    return "\n".join(lines)


def turn_block(
    gen: Generation,
    scene_number: int,
    rec: SceneRecord,
    t: float,
    saying: str | None,
    now: dict[str, Any] | None,
    history: list[Turn],
    question: str,
    cap_s: float,
) -> str:
    """What changes per question: where the learner is, what is on screen, the conversation, the
    question, and the time the answer may take."""
    lines = [
        "NOW",
        f'The learner raised a hand in scene {scene_number} "{rec.title}", {t:.1f} s into it'
        + (f', while the tutor was saying: "{saying}"' if saying else "")
        + ". Everything up to this moment has been taught; the rest of the lesson has not been seen yet.",
    ]
    shown = inventory(now)
    lines.append(f"On screen right now (scene {scene_number}): " + ("; ".join(shown) if shown else "nothing"))
    later = [f'Scene {s.index + 1} "{s.title}"' for s in gen.scenes if s.index + 1 > scene_number]
    lines.append(
        "Later in this lesson (not seen yet): "
        + ("; ".join(later) if later else "nothing, this is the last scene")
    )
    if history:
        lines += ["", "THIS DOUBT SO FAR"]
        for h in history:
            lines.append(f"Learner: {h.question}")
            lines.append("Tutor: " + " ".join(h.answer))
    n = len(history) + 1
    lines += ["", "QUESTION", f'Learner: "{question.strip()}"', f"This is turn {n} of this doubt."]
    if n == 2:
        lines.append(
            "If the learner says your answer did not help or asks the same thing again: explain it more "
            "simply than before, with shorter sentences, everyday words and a new angle or an everyday "
            "analogy. Never repeat your earlier sentences."
        )
    elif n >= 3:
        lines.append(
            f"The learner has had {n - 1} answers in this doubt. If they are still unsure: go back to "
            "basics, one small step per sentence, with a concrete example (simple numbers or a familiar "
            "situation), and name the one idea they are missing. Never repeat earlier sentences."
        )
    lines.append(
        f"Time for this answer: at most {round(cap_s)} seconds (about {words_for(cap_s)} words); "
        "use less when less is enough."
    )
    lines.append(f"Language of the narration: {language_line(gen.request.language)}")
    return "\n".join(lines)


def _clock(t: float) -> str:
    s = max(0, round(t))
    return f"{s // 60}:{s % 60:02d}"
