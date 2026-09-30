"""Synthesized speech on disk, by content: the same sentence in the same voice is synthesized once
(across scene drafts, scenes and lessons), and concurrent requests for it share one synthesis.

A cached utterance is two files in the cache directory: `<id>.<mp3|wav>` (served to the player as
`/api/tts/<id>.<ext>`) and `<id>.json` (duration and word marks).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from animator.tts.text import speech_text
from animator.tts.types import TTSError, TTSProvider, WordMark

log = logging.getLogger("animator.tts")

AUDIO_NAME = re.compile(r"^[0-9a-f]{32}\.(mp3|wav)$")


@dataclass(frozen=True)
class Spoken:
    file: str  # audio file name in the cache directory
    duration_s: float
    marks: tuple[WordMark, ...] | None


class SpeechCache:
    def __init__(
        self,
        provider: TTSProvider,
        directory: Path,
        concurrency: int = 6,
        retries: int = 1,
        voices: dict[str, str] | None = None,
    ) -> None:
        self.provider = provider
        self.directory = directory
        self.retries = retries
        # Lesson language -> voice, where it differs from the provider's default voice (ADR-038).
        self.voices = {"hinglish": provider.hinglish_voice}
        self.voices.update({k.lower(): v for k, v in (voices or {}).items() if v})
        # With concurrency 1, a local server (Kokoro on a CPU) works through the sentences in scene
        # order, so the first scene's speech is ready first.
        self._sem = asyncio.Semaphore(max(1, concurrency))
        self._inflight: dict[str, asyncio.Task[Spoken]] = {}

    def voice_for(self, language: str) -> str:
        """The voice a lesson in `language` speaks with."""
        return self.voices.get(language.strip().lower()) or self.provider.voice

    def _id(self, text: str, voice: str) -> str:
        return hashlib.sha256(f"{self.provider.cache_key(voice)}\n{text}".encode()).hexdigest()[:32]

    def audio_path(self, name: str) -> Path | None:
        """The cached audio file `name`, if the name is well-formed and the file exists."""
        if not AUDIO_NAME.match(name):
            return None
        path = self.directory / name
        return path if path.is_file() else None

    def _load(self, sid: str) -> Spoken | None:
        meta = self.directory / f"{sid}.json"
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not (self.directory / data.get("file", "")).is_file():
            return None
        marks = data.get("marks")
        return Spoken(
            data["file"],
            float(data["duration_s"]),
            tuple(WordMark(str(m[0]), float(m[1]), float(m[2])) for m in marks)
            if marks is not None
            else None,
        )

    async def speak(self, text: str, voice: str | None = None) -> Spoken:
        """The narration beat `text` (caption markup allowed) as cached speech, in `voice` (default:
        the provider's voice)."""
        plain = speech_text(text)
        if not plain:
            raise TTSError("nothing to say")
        v = voice or self.provider.voice
        sid = self._id(plain, v)
        hit = self._load(sid)
        if hit is not None:
            return hit
        task = self._inflight.get(sid)
        if task is None:
            task = asyncio.create_task(self._synthesize(sid, plain, v), name=f"tts-{sid[:8]}")
            self._inflight[sid] = task
            task.add_done_callback(self._done(sid))
        return await asyncio.shield(task)

    def _done(self, sid: str) -> Callable[[asyncio.Task[Spoken]], None]:
        def forget(_task: asyncio.Task[Spoken]) -> None:
            self._inflight.pop(sid, None)

        return forget

    def prefetch(self, texts: Iterable[str], voice: str | None = None) -> None:
        """Start synthesizing now (while the scene is still being written); failures are ignored
        here and retried when the speech is needed."""
        for text in texts:
            if not speech_text(text):
                continue
            task = asyncio.ensure_future(self.speak(text, voice))
            task.add_done_callback(_consume)

    async def _synthesize(self, sid: str, text: str, voice: str) -> Spoken:
        last: Exception | None = None
        async with self._sem:
            for attempt in range(1 + max(0, self.retries)):
                try:
                    speech = await self.provider.synthesize(text, voice)
                    break
                except Exception as e:
                    last = e
                    if attempt < self.retries:
                        await asyncio.sleep(0.5)
            else:
                raise last if isinstance(last, TTSError) else TTSError(str(last)) from last
        if speech.duration_s <= 0:
            raise TTSError(f"{self.provider.name}: audio has no duration")
        file = f"{sid}.{speech.format}"
        self.directory.mkdir(parents=True, exist_ok=True)
        _write(self.directory / file, speech.audio)
        marks = [[m.text, round(m.start, 4), round(m.end, 4)] for m in speech.marks] if speech.marks else None
        meta = {
            "file": file,
            "duration_s": round(speech.duration_s, 4),
            "marks": marks,
            "provider": self.provider.name,
            "voice": voice,
            "text": text,
        }
        _write(self.directory / f"{sid}.json", json.dumps(meta, ensure_ascii=False).encode("utf-8"))
        return Spoken(file, speech.duration_s, speech.marks)

    async def close(self) -> None:
        for task in list(self._inflight.values()):
            task.cancel()
        await self.provider.close()


def _write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    try:
        tmp.replace(path)
    except OSError:
        # Another process wrote the same content first (the name is the content's hash).
        with contextlib.suppress(OSError):
            tmp.unlink()
        if not path.exists():
            raise


def _consume(task: asyncio.Future[Spoken]) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.debug("speech prefetch failed: %s", task.exception())
