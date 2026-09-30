"""Spoken narration for a finished scene (ADR-037): every beat is synthesized, the scene is
stretched where a beat's speech is longer than the beat, and the scene gets a narration record
(audio file per beat, when each caption word is said) that the player and the export use.

Retiming only ever lengthens beats. Times inside a stretched beat move proportionally, so an action
keeps its place in the sentence; beat references (`beat:b2`), `after` / `with` chains, delays and
action durations are unchanged, so motion keeps its speed and every action still ends inside the
scene. The retimed scene is compiled and validated again; if it fails, the original is kept.
"""

from __future__ import annotations

import asyncio
import bisect
import copy
import logging
import math
from collections.abc import Mapping, Sequence
from typing import Any

from animator.tts import SpeechCache, Spoken, caption_words, word_times
from animator_contracts.protocol import Beat

log = logging.getLogger("animator.narration")

MAX_SCENE_DURATION = 120.0
LAST_BEAT_PAD_S = 0.6  # a moment after the last sentence before the next scene starts


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def retime(
    dsl: dict[str, Any],
    beats: Sequence[Beat],
    need: Mapping[str, float],
    max_duration: float = MAX_SCENE_DURATION,
) -> dict[str, Any] | None:
    """The scene with every beat at least `need[beat id]` seconds long, or None when every beat is
    long enough already (or the scene would get longer than `max_duration`)."""
    if not beats:
        return None
    order = sorted(beats, key=lambda b: b.start)
    old = [order[0].start]
    new = [order[0].start]
    grew = False
    for b in order:
        span = max(0.0, b.end - b.start)
        want = max(span, need.get(b.id, 0.0))
        grew = grew or want > span + 1e-3
        old.append(max(old[-1], b.end))
        new.append(new[-1] + want)
    if not grew:
        return None
    duration = float(dsl["duration"])
    shift = new[-1] - old[-1]

    def warp(t: float) -> float:
        if t <= old[0]:
            return t
        if t >= old[-1]:
            return t + shift
        k = bisect.bisect_right(old, t) - 1
        k = min(k, len(old) - 2)
        a0, a1, b0, b1 = old[k], old[k + 1], new[k], new[k + 1]
        return b0 if a1 <= a0 else b0 + (t - a0) * (b1 - b0) / (a1 - a0)

    new_duration = math.ceil((warp(duration) + 0.01) * 10) / 10
    if new_duration > max_duration:
        return None
    out = copy.deepcopy(dsl)
    out["duration"] = new_duration
    starts = {b.id: b.start for b in beats}
    for beat in (out.get("narration") or {}).get("beats") or []:
        if beat.get("id") in starts:
            beat["at"] = round(warp(starts[beat["id"]]), 3)
    for action in out.get("timeline") or []:
        start = action.get("start")
        if isinstance(start, dict) and _num(start.get("at")):
            start["at"] = round(warp(float(start["at"])), 3)
    for key in ("cues", "interactions"):
        for item in out.get(key) or []:
            if isinstance(item, dict) and _num(item.get("at")):
                item["at"] = round(warp(float(item["at"])), 3)
    return out


async def speak_beats(
    speech: SpeechCache, beats: Sequence[Beat], voice: str | None = None
) -> dict[str, Spoken]:
    """Speech for every beat that has words (all at once; most are cached by the prefetch)."""
    todo = [b for b in beats if caption_words(b.text)]
    results = await asyncio.gather(*(speech.speak(b.text, voice) for b in todo))
    return {b.id: s for b, s in zip(todo, results, strict=True)}


def needs(spoken: Mapping[str, Spoken], beats: Sequence[Beat], pause_s: float) -> dict[str, float]:
    last = beats[-1].id if beats else None
    return {
        bid: s.duration_s + (max(pause_s, LAST_BEAT_PAD_S) if bid == last else pause_s)
        for bid, s in spoken.items()
    }


def narration_record(
    speech: SpeechCache, beats: Sequence[Beat], spoken: Mapping[str, Spoken], voice: str | None = None
) -> dict[str, Any]:
    """What the player and the export need: per beat, its audio, start and caption word times."""
    out = []
    for b in beats:
        s = spoken.get(b.id)
        if s is None:
            continue
        words = caption_words(b.text)
        times = word_times(words, s.marks, s.duration_s)
        out.append(
            {
                "id": b.id,
                "start": round(b.start, 3),
                "audio": s.file,
                "duration": round(s.duration_s, 3),
                "words": [[a, e] for a, e in times],
                "timed": s.marks is not None,
            }
        )
    return {"provider": speech.provider.name, "voice": voice or speech.provider.voice, "beats": out}
