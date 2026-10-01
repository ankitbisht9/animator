"""Voice modulation (ADR-045): how each part of the narration is spoken, not only what is spoken.

A teacher varies their delivery with the meaning: a question sounds like a question, a key term is
stressed, a definition or an equation is said more slowly, a discovery has more energy, and there is
a moment of silence before an important point and at every full stop. Voices read a whole scene with
one pattern, and they sometimes run two sentences together.

Two layers, after the speech script has marked the delivery (`Script.stress`, `.pause_before`,
`.deliveries`):

1. **Text** (`render`): what the voice model itself can act on. Models that take audio tags (Eleven v3
   and v4) get the sentence's tone as a tag where the tone changes ("[curious]", "[excitedly]") and
   the stressed words of English in capitals (their documented emphasis). Other models get the words
   as they are.
2. **Timing** (`shape`): applied to the audio, with the word timings the provider reported, so it is
   exact for every model: a pause at least as long as a teacher's at each full stop and comma and
   before a key point (silence inserted where the voice ran on), and a slower tempo (pitch unchanged)
   for sentences marked slow. Every word time moves with the audio, so the captions stay in sync.

Anything that fails here leaves the speech as the provider made it.
"""

from __future__ import annotations

import array
import itertools
import logging
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from animator.tts.types import Speech, WordMark

log = logging.getLogger("animator.tts")

RATE = 44100  # Hz, mono 16-bit: what the shaped speech is processed at

# The tone of a sentence as a delivery tag (models that take audio tags). Neutral sentences get none.
TONE_TAGS = {
    "curious": "[curious]",
    "excited": "[excited]",
    "warm": "[warmly]",
    "encouraging": "[encouragingly]",
    "thoughtful": "[thoughtfully]",
    "serious": "[seriously]",
}

_SENTENCE_END = re.compile(r"[.?!\u0964\u0965]['\"\u201d\u2019)\]*]*$")
_CLAUSE_END = re.compile(r"[,;:\u2014\u2013]['\"\u201d\u2019)\]*]*$")
_LATIN = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class Delivery:
    """How a stretch of the narration is spoken: from word `start` (0-based) on, until the next one."""

    start: int
    tone: str = "neutral"
    pace: str = "normal"  # "normal" | "slow"


@dataclass(frozen=True)
class Shaping:
    """The timing a teacher keeps (seconds): the least pause after a full stop, after a comma, before
    a key point; and the tempo of a slow sentence (0.9 = 10% slower, pitch unchanged)."""

    sentence_pause: float = 0.4
    comma_pause: float = 0.18
    key_pause: float = 0.35
    slow_tempo: float = 0.9

    @property
    def key(self) -> str:
        return f"{self.sentence_pause}|{self.comma_pause}|{self.key_pause}|{self.slow_tempo}"


# ---------------------------------------------------------------------- the text layer


def tags_supported(model: str) -> bool:
    """Eleven v3 and v4 act on audio tags and capitals; earlier models would read them out."""
    return model.startswith(("eleven_v3", "eleven_v4"))


def render(
    words: Sequence[str],
    stress: frozenset[int] = frozenset(),
    deliveries: Sequence[Delivery] = (),
    tags: bool = False,
) -> str:
    """The text the voice reads. With `tags`, a delivery tag where the tone changes (never for a
    neutral stretch, never twice in a row) and the stressed English words in capitals."""
    out: list[str] = []
    starts = {d.start: d for d in deliveries}
    last_tone = "neutral"
    for i, word in enumerate(words):
        if not word.strip():
            continue
        if tags and i in starts:
            tone = starts[i].tone
            if tone != last_tone and tone in TONE_TAGS:
                out.append(TONE_TAGS[tone])
            last_tone = tone
        if tags and i in stress and _LATIN.search(word) and len(re.sub(r"[^A-Za-z]", "", word)) > 1:
            word = word.upper()
        out.append(word)
    return " ".join(out)


# ---------------------------------------------------------------------- the timing layer


def plan_pauses(words: Sequence[str], pause_before: frozenset[int], shaping: Shaping) -> list[float]:
    """The least silence after each word (the last one gets none)."""
    out = []
    for i, word in enumerate(words):
        if i == len(words) - 1:
            out.append(0.0)
            continue
        least = 0.0
        if _SENTENCE_END.search(word):
            least = shaping.sentence_pause
        elif _CLAUSE_END.search(word):
            least = shaping.comma_pause
        if i + 1 in pause_before:
            least = max(least, shaping.key_pause)
        out.append(least)
    return out


@dataclass
class _Timeline:
    """Old time -> new time, piecewise linear (knots in increasing old time)."""

    knots: list[tuple[float, float]]

    def __call__(self, t: float) -> float:
        k = self.knots
        if t <= k[0][0]:
            return t + (k[0][1] - k[0][0])
        for (a0, b0), (a1, b1) in itertools.pairwise(k):
            if t <= a1:
                return b0 if a1 <= a0 else b0 + (t - a0) * (b1 - b0) / (a1 - a0)
        return t + (k[-1][1] - k[-1][0])


def shape(
    speech: Speech,
    marks: Sequence[WordMark],
    words: Sequence[str],
    pause_before: frozenset[int] = frozenset(),
    deliveries: Sequence[Delivery] = (),
    shaping: Shaping | None = None,
    ffmpeg: str = "ffmpeg",
) -> tuple[Speech, tuple[WordMark, ...]] | None:
    """The speech with a teacher's timing, and the caption-word marks moved with it. `marks` are one
    per caption word (`words`), in order. None when nothing needs changing or it cannot be done."""
    shaping = shaping or Shaping()
    exe = shutil.which(ffmpeg) or (ffmpeg if ffmpeg and shutil.which(ffmpeg) else None)
    if exe is None or len(marks) != len(words) or not marks:
        return None
    try:
        pcm = _decode(exe, speech.audio)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("voice timing: could not decode the speech (%s)", e)
        return None
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    duration = len(samples) / RATE

    # 1. Slow stretches (definitions, equations): their audio is stretched, pitch unchanged.
    slow = _slow_spans(marks, deliveries, duration)
    pieces: list[tuple[float, float, float]] = []  # (old start, old end, tempo)
    t = 0.0
    for a, b in slow:
        if a > t:
            pieces.append((t, a, 1.0))
        pieces.append((a, b, shaping.slow_tempo))
        t = b
    if t < duration:
        pieces.append((t, duration, 1.0))

    # 2. Pauses: where the voice left less silence than a teacher would, silence is added just after
    #    the word (inside the gap there is, so nothing is cut).
    least = plan_pauses(words, pause_before, shaping)
    inserts: list[tuple[float, float]] = []  # (old time, seconds of silence)
    for i in range(len(marks) - 1):
        gap = marks[i + 1].start - marks[i].end
        add = least[i] - max(0.0, gap)
        if add > 0.02:
            inserts.append((marks[i].end + min(max(0.0, gap), 0.02), round(add, 3)))
    if not slow and not inserts:
        return None

    out = array.array("h")
    knots: list[tuple[float, float]] = [(0.0, 0.0)]
    for start, end, tempo in pieces:
        cuts = [(p, s) for p, s in inserts if start <= p < end]
        edges = [start, *[p for p, _ in cuts], end]
        for k in range(len(edges) - 1):
            a, b = edges[k], edges[k + 1]
            chunk = samples[int(a * RATE) : int(b * RATE)]
            if tempo != 1.0 and len(chunk) > RATE // 20:
                chunk = _stretch(exe, chunk, tempo)
            if k > 0:
                _fade_in(chunk)
            if k < len(edges) - 2:
                _fade_out(chunk)
            new_a = len(out) / RATE
            out.extend(chunk)
            knots.append((a, new_a))
            knots.append((b, len(out) / RATE))
            if k < len(cuts):
                out.extend(array.array("h", bytes(2 * int(cuts[k][1] * RATE))))
    timeline = _Timeline(sorted(set(knots)))
    try:
        audio, fmt = _encode(exe, out.tobytes())
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("voice timing: could not encode the speech (%s)", e)
        return None
    moved = tuple(
        WordMark(m.text, round(timeline(m.start), 4), round(max(timeline(m.start), timeline(m.end)), 4))
        for m in marks
    )
    return Speech(audio, fmt, len(out) / RATE, moved), moved


def _slow_spans(
    marks: Sequence[WordMark], deliveries: Sequence[Delivery], duration: float
) -> list[tuple[float, float]]:
    """The time spans of the stretches marked slow, from just before their first word to just after
    their last."""
    spans = []
    ordered = sorted(deliveries, key=lambda d: d.start)
    for k, d in enumerate(ordered):
        if d.pace != "slow" or d.start >= len(marks):
            continue
        last = (ordered[k + 1].start if k + 1 < len(ordered) else len(marks)) - 1
        a = max(0.0, marks[d.start].start - 0.02)
        b = min(duration, marks[min(last, len(marks) - 1)].end + 0.02)
        if b - a > 0.3:
            spans.append((a, b))
    return spans


def _fade_in(chunk: array.array[int], n: int = 220) -> None:  # 5 ms: no click where silence was added
    for i in range(min(n, len(chunk))):
        chunk[i] = int(chunk[i] * i / n)


def _fade_out(chunk: array.array[int], n: int = 220) -> None:
    m = len(chunk)
    for i in range(min(n, m)):
        chunk[m - 1 - i] = int(chunk[m - 1 - i] * i / n)


def _decode(exe: str, audio: bytes) -> bytes:
    args = [exe, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", "pipe:0"]
    args += ["-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(RATE), "pipe:1"]
    return subprocess.run(args, input=audio, capture_output=True, check=True, timeout=60).stdout


def _stretch(exe: str, chunk: array.array[int], tempo: float) -> array.array[int]:
    args = [exe, "-hide_banner", "-nostdin", "-loglevel", "error"]
    args += ["-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "pipe:0", "-filter:a", f"atempo={tempo}"]
    args += ["-f", "s16le", "-ar", str(RATE), "-ac", "1", "pipe:1"]
    raw = subprocess.run(args, input=chunk.tobytes(), capture_output=True, check=True, timeout=60).stdout
    out = array.array("h")
    out.frombytes(raw[: len(raw) - len(raw) % 2])
    return out


def _encode(exe: str, pcm: bytes) -> tuple[bytes, str]:
    args = [exe, "-hide_banner", "-nostdin", "-loglevel", "error"]
    args += ["-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "pipe:0"]
    args += ["-codec:a", "libmp3lame", "-b:a", "128k", "-f", "mp3", "pipe:1"]
    return subprocess.run(args, input=pcm, capture_output=True, check=True, timeout=60).stdout, "mp3"
