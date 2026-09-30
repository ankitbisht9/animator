"""The text-to-speech interface every provider implements (ADR-037).

A provider turns one narration beat (plain text) into audio, with the time each word is said when
the provider reports it. Everything else (caching, word alignment with the caption, retiming the
scene, playback, export) is provider-independent, so a provider is swapped by configuration alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class WordMark:
    """A spoken word (or a few words the provider reports together) and when it is said, in seconds
    from the start of the audio."""

    text: str
    start: float
    end: float


@dataclass(frozen=True)
class Speech:
    audio: bytes
    format: str  # file extension of `audio`: "mp3" or "wav"
    duration_s: float
    # Word timings from the provider; None when it does not report them (they are then estimated).
    marks: tuple[WordMark, ...] | None = None


class TTSError(RuntimeError):
    """Speech could not be synthesized (network, quota, bad key, bad voice). Never fails a lesson."""


class TTSProvider(Protocol):
    name: str
    voice: str  # the default voice
    hinglish_voice: str  # the voice for Hinglish lessons (ADR-038)
    model: str

    def cache_key(self, voice: str) -> str:
        """Everything that changes the audio besides the text (cache identity)."""
        ...

    async def synthesize(self, text: str, voice: str | None = None) -> Speech: ...

    async def close(self) -> None: ...
