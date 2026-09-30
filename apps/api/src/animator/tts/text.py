"""Narration text for speech, and when each caption word is said.

The caption bar draws a beat's text word by word (engine `text/layout.ts`: markup removed, words
split at spaces and line breaks). `word_times` gives every one of those words a start and end in
the beat's audio, from the provider's word marks where they match and estimated from word length
and punctuation pauses where they do not (or when the provider reports no timings at all).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence

from animator.tts.types import WordMark

_SCRIPT = re.compile(r"[_^]\{([^}]+)\}")
_SPACES = re.compile(r"[ \n]+")
_CLOSERS = "\"')]}»”’"  # closing quotes and brackets after a sentence's last mark  # noqa: RUF001
_DASHES = ("-", "–", "—")  # noqa: RUF001


def strip_markup(text: str) -> str:
    """The caption's plain text: `**emphasis**` markers (when balanced) and `_{sub}` / `^{sup}`
    wrappers removed, like the engine's `stripMarkup`."""
    if text.count("**") % 2 == 0:
        text = text.replace("**", "")
    return _SCRIPT.sub(r"\1", text)


def caption_words(text: str) -> list[str]:
    """The words the caption highlights one by one, in order."""
    return [w for w in _SPACES.split(strip_markup(text)) if w]


def speech_text(text: str) -> str:
    """What the voice reads: the caption's words on one line."""
    return " ".join(caption_words(text))


def _norm(s: str) -> str:
    return "".join(c for c in s.casefold() if c.isalnum())


def _weight(word: str) -> float:
    return len(_norm(word)) + 2.0  # even a one-letter word takes a moment to say


def _pause(word: str) -> float:
    """Silence after a word, in the same units as `_weight`."""
    end = word.rstrip(_CLOSERS)[-1:]
    if end in (".", "!", "?"):
        return 6.0
    if end in (",", ";", ":") or word in _DASHES:
        return 3.0
    return 0.0


def _align(words: Sequence[str], marks: Sequence[WordMark]) -> tuple[list[float | None], list[float | None]]:
    """Match the marks to the words by their letters and digits, in order. A mark that covers
    several words ("9.8 m/s²" said as one unit) is split between them by length."""
    owner: list[int] = []
    parts: list[str] = []
    for i, w in enumerate(words):
        n = _norm(w)
        parts.append(n)
        owner.extend([i] * len(n))
    stream = "".join(parts)
    start: list[float | None] = [None] * len(words)
    end: list[float | None] = [None] * len(words)
    p = 0
    for m in marks:
        n = _norm(m.text)
        if not n:
            continue
        q = stream.find(n, p, p + len(n) + 40)
        if q < 0:
            continue  # said differently ("nine point eight" for "9.8"): estimated below
        counts = Counter(owner[q : q + len(n)])
        t = m.start
        span = max(0.0, m.end - m.start)
        for i in sorted(counts):
            t1 = t + span * counts[i] / len(n)
            s0, e0 = start[i], end[i]
            start[i] = t if s0 is None else min(s0, t)
            end[i] = t1 if e0 is None else max(e0, t1)
            t = t1
        p = q + len(n)
    return start, end


def word_times(
    words: Sequence[str], marks: Sequence[WordMark] | None, duration: float
) -> list[tuple[float, float]]:
    """(start, end) of every word in seconds from the start of its audio, non-decreasing and
    inside [0, duration]."""
    n = len(words)
    if n == 0:
        return []
    start: list[float | None] = [None] * n
    end: list[float | None] = [None] * n
    if marks:
        start, end = _align(words, marks)
    # Unmatched runs are spread over the time between their matched neighbours by length and
    # punctuation. Without any marks, allow for the silence providers put before and after speech.
    lead = min(0.1, 0.03 * duration)
    tail = min(0.25, 0.05 * duration)
    i = 0
    while i < n:
        if start[i] is not None:
            i += 1
            continue
        j = i
        while j < n and start[j] is None:
            j += 1
        before = end[i - 1] if i > 0 else None
        after = start[j] if j < n else None
        lo = before if before is not None else lead
        hi = max(lo, after if after is not None else duration - tail)
        units = [(_weight(words[k]), _pause(words[k]) if k < n - 1 else 0.0) for k in range(i, j)]
        total = sum(w + p for w, p in units) or 1.0
        t = lo
        for k, (w, p) in zip(range(i, j), units, strict=True):
            t1 = t + (hi - lo) * w / total
            start[k], end[k] = t, t1
            t = t1 + (hi - lo) * p / total
        i = j
    out: list[tuple[float, float]] = []
    last = 0.0
    for s, e in zip(start, end, strict=True):
        s0 = min(duration, max(last, float(s or 0.0)))
        e0 = min(duration, max(s0, float(e if e is not None else s0)))
        out.append((round(s0, 3), round(e0, 3)))
        last = s0
    return out
