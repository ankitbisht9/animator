"""Spoken narration for a finished scene (ADR-037): the scene's sentences are synthesized, the scene
is fitted to the speech, and the scene gets a narration record (per beat: its audio, where its part
starts, when each caption word is said) that the player and the export use.

One take (ADR-043, the default): the whole scene is spoken in one recording, so the voice flows from
sentence to sentence with only the pauses its own punctuation makes. Each beat plays its part of that
recording (cut in the middle of the pause between two sentences), and the scene ends a moment
(`ANIMATOR_TTS_SCENE_PAUSE_S`) after its last word. Sentence by sentence (ADR-041) synthesizes each
beat on its own.

Fitting moves beats, not actions. Times inside a beat move proportionally, so an action keeps its place
in the sentence; beat references (`beat:b2`), `after` / `with` chains, delays and action durations are
unchanged, so motion keeps its speed. The fitted scene is compiled and validated again; if it fails, the
original is kept.
"""

from __future__ import annotations

import asyncio
import bisect
import copy
import itertools
import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from animator.tts import SpeechCache, Spoken, TTSProvider, caption_words, speech_text, word_times
from animator.tts.prosody import Delivery
from animator.tts.script import Script
from animator.tts.types import SpeechContext
from animator_contracts.protocol import Beat

log = logging.getLogger("animator.narration")

MAX_SCENE_DURATION = 120.0
LAST_BEAT_PAD_S = 0.6  # a moment after the last sentence before the next scene starts
TAKE_TAIL_S = 0.25  # the fade of a scene's last word, kept in its part of the recording


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


# ---------------------------------------------------------------------- one take per scene (ADR-043)


def take_script(texts: Sequence[str], scripts: Mapping[str, Script]) -> Script | None:
    """The speech script of a whole scene: its sentences' scripts one after another (a sentence
    without one is read as written). None when no sentence has a script. The tone is the first
    sentence's (only `eleven_v3` uses it, as one tag at the start)."""
    if not any(t in scripts for t in texts):
        return None
    directed = any(s.directed for s in scripts.values())
    spoken: list[str] = []
    stress: set[int] = set()
    pauses: set[int] = set()
    deliveries: list[Delivery] = []
    for t in texts:
        script = scripts.get(t)
        offset = len(spoken)
        spoken += script.spoken if script is not None else speech_text(t).split(" ")
        if not directed:
            continue
        # Voice modulation (ADR-045): each sentence keeps its own delivery inside the take.
        if script is not None and script.directed:
            stress |= {offset + i for i in script.stress}
            pauses |= {offset + i for i in script.pause_before}
            deliveries += [Delivery(offset + d.start, d.tone, d.pace) for d in script.deliveries]
        else:
            deliveries.append(Delivery(offset))
    tone = scripts[texts[0]].tone if texts[0] in scripts else "neutral"
    return Script(tuple(spoken), tone, frozenset(stress), frozenset(pauses), tuple(deliveries))


async def speak_scene(
    speech: SpeechCache,
    texts: Sequence[str],
    voice: str | None = None,
    provider: TTSProvider | None = None,
    scripts: Mapping[str, Script] | None = None,
) -> Spoken | None:
    """The whole scene's narration as one recording (ADR-043): the voice reads every sentence in one
    go, so it does not start afresh at each sentence. None when the scene has nothing to say."""
    said = [t for t in texts if caption_words(t)]
    if not said:
        return None
    return await speech.speak(" ".join(said), voice, provider, take_script(said, scripts or {}))


@dataclass(frozen=True)
class Part:
    """A beat's part of its scene's recording."""

    offset: float  # where it starts in the recording (seconds)
    length: float  # how much of the recording it plays
    words: tuple[tuple[float, float], ...]  # when each caption word is said, from the part's start
    said: float  # when its last word ends, from the part's start


def split_take(beats: Sequence[Beat], take: Spoken, tail_s: float = TAKE_TAIL_S) -> dict[str, Part]:
    """Each spoken beat's part of the scene's recording. Two sentences are cut in the middle of the
    pause the voice made between them; the last part ends `tail_s` after the last word."""
    spoken = [b for b in beats if caption_words(b.text)]
    counts = [len(caption_words(b.text)) for b in spoken]
    times = word_times([w for b in spoken for w in caption_words(b.text)], take.marks, take.duration_s)
    groups: list[list[tuple[float, float]]] = []
    i = 0
    for n in counts:
        groups.append(times[i : i + n])
        i += n
    cuts = [0.0]
    for before, after in itertools.pairwise(groups):
        end, start = before[-1][1], after[0][0]
        cuts.append(max(cuts[-1], (end + start) / 2 if start > end else start))
    out: dict[str, Part] = {}
    for k, (b, words) in enumerate(zip(spoken, groups, strict=True)):
        c = cuts[k]
        e = cuts[k + 1] if k + 1 < len(cuts) else min(take.duration_s, words[-1][1] + tail_s)
        out[b.id] = Part(
            offset=round(c, 3),
            length=round(max(0.0, e - c), 3),
            words=tuple((round(a - c, 3), round(z - c, 3)) for a, z in words),
            said=round(words[-1][1] - c, 3),
        )
    return out


def take_needs(parts: Mapping[str, Part], beats: Sequence[Beat], scene_pause_s: float) -> dict[str, float]:
    """Time each beat needs: exactly its part of the recording, so the next sentence follows without
    a break; the scene's last sentence is followed by `scene_pause_s` of quiet before the next scene."""
    order = [b.id for b in beats if b.id in parts]
    last = order[-1] if order else None
    return {bid: parts[bid].said + scene_pause_s if bid == last else parts[bid].length for bid in order}


def take_record(
    beats: Sequence[Beat], parts: Mapping[str, Part], take: Spoken, provider: str, voice: str
) -> dict[str, Any]:
    """The narration record of a scene spoken in one take: every beat plays the same recording, from
    its `offset`, for `duration` seconds."""
    out = []
    for b in beats:
        p = parts.get(b.id)
        if p is None:
            continue
        out.append(
            {
                "id": b.id,
                "start": round(b.start, 3),
                "audio": take.file,
                "offset": p.offset,
                "duration": p.length,
                "words": [[a, e] for a, e in p.words],
                "timed": take.marks is not None,
            }
        )
    return {"provider": provider, "voice": voice, "beats": out}


# ---------------------------------------------------------------------- voice and picture together (ADR-045)

SYNC_LEAD_S = 0.15  # a highlight starts this much before its word is said, so they land together

# Parts of object ids that do not name the thing (a "high_label" is about "high").
_GENERIC = {
    "label",
    "labels",
    "text",
    "arrow",
    "arrows",
    "box",
    "icon",
    "highlight",
    "glow",
    "pulse",
    "title",
    "card",
    "note",
    "badge",
    "readout",
    "line",
    "group",
    "shape",
    "main",
    "example",
    "next",
    "new",
    "old",
    "left",
    "right",
    "top",
    "bottom",
    "the",
    "and",
    "for",
    "with",
    "part",
    "item",
    "marker",
    "tag",
}


def _name_tokens(object_id: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", object_id.lower()) if len(t) >= 3 and t not in _GENERIC]


def _names(tokens: Sequence[str], word: str) -> bool:
    w = "".join(c for c in word.casefold() if c.isalnum())
    return any(w == t or (min(len(w), len(t)) >= 4 and (w.startswith(t) or t.startswith(w))) for t in tokens)


def sync_highlights(
    dsl: dict[str, Any],
    words: Mapping[str, Sequence[tuple[str, float]]],
    stressed: Mapping[str, frozenset[int]],
) -> dict[str, Any] | None:
    """The scene with each beat's highlights landing on the moment the thing they highlight is named
    (Mayer's temporal contiguity, ADR-045). A `highlight` started from a beat (`beat:b2`) whose target
    is named in that beat's sentence ("high_label" and the word "high") starts as that word is said
    (a stressed occurrence first; several highlights of the same thing take its occurrences in turn).
    Highlights of things not named stay as they are. `words`: per beat, each caption word and when it
    starts (seconds from the beat's start). None when nothing moves; the engine validates the result."""
    out = copy.deepcopy(dsl)
    used: dict[tuple[str, int], bool] = {}
    changed = False
    actions = [
        a
        for a in out.get("timeline") or []
        if a.get("action") == "highlight"
        and isinstance(a.get("start"), dict)
        and isinstance(a["start"].get("at"), str)
        and a["start"]["at"].startswith("beat:")
    ]
    actions.sort(key=lambda a: float(a["start"].get("delay") or 0.0))
    for action in actions:
        bid = action["start"]["at"][5:]
        beat = words.get(bid) or []
        tokens = _name_tokens(str(action.get("target", "")))
        found = [
            i for i, (w, _) in enumerate(beat) if tokens and _names(tokens, w) and not used.get((bid, i))
        ]
        if not found:
            continue
        first = next((i for i in found if i in stressed.get(bid, frozenset())), found[0])
        used[(bid, first)] = True
        delay = round(max(0.0, beat[first][1] - SYNC_LEAD_S), 2)
        if abs(float(action["start"].get("delay") or 0.0) - delay) > 0.05:
            action["start"]["delay"] = delay
            changed = True
    return out if changed else None


# ---------------------------------------------------------------------- sentence by sentence (ADR-041)


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
