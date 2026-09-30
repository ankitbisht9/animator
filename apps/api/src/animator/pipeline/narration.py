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

from animator.tts import SpeechCache, Spoken, TTSProvider, caption_words, speech_text, word_times
from animator.tts.script import Script
from animator.tts.types import SpeechContext
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
    floor: Mapping[str, float] | None = None,
    keep: float = 0.0,
) -> dict[str, Any] | None:
    """The scene with every beat at least `need[beat id]` seconds long, or None when every beat is
    long enough already (or the scene would get longer than `max_duration`).

    With `floor` (fitting, ADR-041) a spoken beat also gets shorter, down to its speech or to
    `floor[beat id]` (the time its own actions need), so no long silence is left after a sentence.
    `keep` is the share of that silence left in place (0.5: halfway, a gentler change of timing)."""
    if not beats:
        return None
    order = sorted(beats, key=lambda b: b.start)
    old = [order[0].start]
    new = [order[0].start]
    changed = False
    for b in order:
        span = max(0.0, b.end - b.start)
        if floor is None:
            want = max(span, need.get(b.id, 0.0))
        elif b.id in need:
            want = max(need[b.id], floor.get(b.id, 0.0), need[b.id] + keep * (span - need[b.id]))
        else:
            want = span  # a beat with nothing to say keeps its time
        changed = changed or abs(want - span) > 1e-3
        old.append(max(old[-1], b.end))
        new.append(new[-1] + want)
    if not changed:
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


async def speak_texts(
    speech: SpeechCache,
    texts: Sequence[str],
    voice: str | None = None,
    provider: TTSProvider | None = None,
    scripts: Mapping[str, Script] | None = None,
) -> list[Spoken | None]:
    """Speech for a scene's sentences, in order (all at once; most are cached by the prefetch). Each
    is spoken knowing the sentences around it, so the voice flows from one to the next."""
    scripts = scripts or {}
    said = [scripts[t].text if t in scripts else speech_text(t) for t in texts]

    async def one(i: int) -> Spoken | None:
        if not caption_words(texts[i]):
            return None
        context = SpeechContext(
            previous=said[i - 1] if i > 0 else None,
            following=said[i + 1] if i + 1 < len(said) else None,
        )
        return await speech.speak(texts[i], voice, provider, scripts.get(texts[i]), context)

    return list(await asyncio.gather(*(one(i) for i in range(len(texts)))))


async def speak_beats(
    speech: SpeechCache,
    beats: Sequence[Beat],
    voice: str | None = None,
    provider: TTSProvider | None = None,
    scripts: Mapping[str, Script] | None = None,
) -> dict[str, Spoken]:
    """Speech for every beat that has words."""
    spoken = await speak_texts(speech, [b.text for b in beats], voice, provider, scripts)
    return {b.id: s for b, s in zip(beats, spoken, strict=True) if s is not None}


def said_until(s: Spoken, text: str) -> float:
    """When the sentence's last word ends in its audio. Voices leave silence after it (edge about
    0.9 s), which is not speech: counting it put 1.3 s of silence between two sentences."""
    times = word_times(caption_words(text), s.marks, s.duration_s)
    return times[-1][1] if times else s.duration_s


def needs(spoken: Mapping[str, Spoken], beats: Sequence[Beat], pause_s: float) -> dict[str, float]:
    """Time each beat needs: until its last word is said, then the pause before the next sentence
    (a longer one after the scene's last sentence)."""
    last = beats[-1].id if beats else None
    text = {b.id: b.text for b in beats}
    return {
        bid: said_until(s, text[bid]) + (max(pause_s, LAST_BEAT_PAD_S) if bid == last else pause_s)
        for bid, s in spoken.items()
        if bid in text
    }


def floors(dsl: dict[str, Any], beats: Sequence[Beat]) -> dict[str, float]:
    """The least time each beat keeps when fitted: every action started from it (`beat:b2`, with its
    delay) must still start inside it."""
    out = {b.id: 0.0 for b in beats}
    for action in dsl.get("timeline") or []:
        start = action.get("start") or {}
        at = start.get("at") if isinstance(start, dict) else None
        if isinstance(at, str) and at.startswith("beat:") and at[5:] in out:
            delay = start.get("delay")
            d = float(delay) if isinstance(delay, (int, float)) and not isinstance(delay, bool) else 0.0
            out[at[5:]] = max(out[at[5:]], d + 0.3)
    return out


def narration_record(
    beats: Sequence[Beat], spoken: Mapping[str, Spoken], provider: str, voice: str
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
    return {"provider": provider, "voice": voice, "beats": out}
