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

from animator.tts.prosody import Shaping, render, shape, tags_supported
from animator.tts.script import Script
from animator.tts.text import caption_words, speech_text, word_times
from animator.tts.types import Speech, SpeechContext, TTSError, TTSProvider, WordMark

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
        providers: dict[str, TTSProvider] | None = None,
        prosody: Shaping | None = None,
        ffmpeg: str = "ffmpeg",
    ) -> None:
        self.provider = provider
        # Voice modulation (ADR-045): the delivery rendered for the voice model, and a teacher's
        # timing applied to the audio. None: speech as the provider makes it.
        self.prosody = prosody
        self.ffmpeg = ffmpeg
        self.directory = directory
        self.retries = retries
        self.concurrency = max(1, concurrency)
        # Lesson language -> provider, where it is not the main one (Hinglish on edge while English
        # lessons use Kokoro, ADR-039), and lesson language -> voice (ADR-038).
        self.providers = {k.strip().lower(): v for k, v in (providers or {}).items()}
        self.voices = {"hinglish": self.provider_for("hinglish").hinglish_voice}
        self.voices.update({k.strip().lower(): v for k, v in (voices or {}).items() if v})
        # One queue per provider. With concurrency 1, a local server (Kokoro on a CPU) works through
        # the sentences in scene order, so the first scene's speech is ready first.
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._inflight: dict[str, asyncio.Task[Spoken]] = {}

    def provider_for(self, language: str) -> TTSProvider:
        """The provider a lesson in `language` speaks with."""
        return self.providers.get(language.strip().lower(), self.provider)

    def voice_for(self, language: str) -> str:
        """The voice a lesson in `language` speaks with."""
        return self.voices.get(language.strip().lower()) or self.provider_for(language).voice

    def _sem(self, provider: TTSProvider) -> asyncio.Semaphore:
        sem = self._sems.get(provider.name)
        if sem is None:
            # ANIMATOR_TTS_CONCURRENCY is for the main provider (1 for a CPU Kokoro); another cloud
            # provider serving one language keeps its usual parallelism.
            n = self.concurrency if provider is self.provider or provider.name == "kokoro" else 8
            sem = self._sems[provider.name] = asyncio.Semaphore(max(n, 1))
        return sem

    def _id(self, text: str, voice: str, provider: TTSProvider) -> str:
        return hashlib.sha256(f"{provider.cache_key(voice)}\n{text}".encode()).hexdigest()[:32]

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

    async def speak(
        self,
        text: str,
        voice: str | None = None,
        provider: TTSProvider | None = None,
        script: Script | None = None,
        context: SpeechContext | None = None,
    ) -> Spoken:
        """The narration beat `text` (caption markup allowed) as cached speech, in `voice` of
        `provider` (defaults: the main provider and its voice). With a `script` (ADR-041) the voice
        reads the script (Hindi in Devanagari, spoken punctuation, a tone) and the word times are
        mapped back onto the caption's words. `context` (the sentences around it) helps voices that
        can use it and is not part of the cache key."""
        prov = provider or self.provider
        directed = self.prosody is not None and script is not None and script.directed
        if directed:
            assert script is not None
            plain = render(
                script.spoken,
                script.stress,
                script.deliveries,
                tags=tags_supported(getattr(prov, "model", "")),
            )
        else:
            plain = script.text if script is not None else speech_text(text)
        if not plain:
            raise TTSError("nothing to say")
        v = voice or prov.voice
        tone = script.tone if script is not None and script.tone != "neutral" else None
        if directed:
            tone = None  # the tones are in the text, where each sentence's starts
        ctx = SpeechContext(
            previous=context.previous if context else None,
            following=context.following if context else None,
            tone=tone,
        )
        keyed = plain if script is None else f"{plain}\n#caption:{text}\n#tone:{tone or ''}"
        if self.prosody is not None:
            keyed += f"\n#delivery:{script.delivery_key if script else ''}\n#timing:{self.prosody.key}"
        sid = self._id(keyed, v, prov)
        hit = self._load(sid)
        if hit is not None:
            return hit
        task = self._inflight.get(sid)
        if task is None:
            task = asyncio.create_task(
                self._synthesize(
                    sid,
                    plain,
                    v,
                    prov,
                    text if script is not None or self.prosody is not None else None,
                    script,
                    ctx,
                ),
                name=f"tts-{sid[:8]}",
            )
            self._inflight[sid] = task
            task.add_done_callback(self._done(sid))
        return await asyncio.shield(task)

    def _done(self, sid: str) -> Callable[[asyncio.Task[Spoken]], None]:
        def forget(_task: asyncio.Task[Spoken]) -> None:
            self._inflight.pop(sid, None)

        return forget

    def prefetch(
        self, texts: Iterable[str], voice: str | None = None, provider: TTSProvider | None = None
    ) -> None:
        """Start synthesizing now (while the scene is still being written); failures are ignored
        here and retried when the speech is needed."""
        for text in texts:
            if not speech_text(text):
                continue
            task = asyncio.ensure_future(self.speak(text, voice, provider))
            task.add_done_callback(_consume)

    async def _synthesize(
        self,
        sid: str,
        text: str,
        voice: str,
        provider: TTSProvider,
        caption: str | None = None,
        script: Script | None = None,
        context: SpeechContext | None = None,
    ) -> Spoken:
        last: Exception | None = None
        async with self._sem(provider):
            for attempt in range(1 + max(0, self.retries)):
                try:
                    speech = await provider.synthesize(text, voice, context)
                    break
                except Exception as e:
                    last = e
                    if attempt < self.retries:
                        await asyncio.sleep(0.5)
            else:
                raise last if isinstance(last, TTSError) else TTSError(str(last)) from last
        if speech.duration_s <= 0:
            raise TTSError(f"{provider.name}: audio has no duration")
        file = f"{sid}.{speech.format}"
        self.directory.mkdir(parents=True, exist_ok=True)
        _write(self.directory / file, speech.audio)
        found = speech.marks
        if script is not None and caption is not None:
            found = caption_marks(caption, script, speech)
        if self.prosody is not None and caption is not None and speech.marks:
            speech, found = await self._shape(speech, found, caption, script)
        marks = [[m.text, round(m.start, 4), round(m.end, 4)] for m in found] if found else None
        meta = {
            "file": file,
            "duration_s": round(speech.duration_s, 4),
            "marks": marks,
            "provider": provider.name,
            "voice": voice,
            "text": text,
            "caption": caption,
            "tone": context.tone if context else None,
        }
        _write(self.directory / f"{sid}.json", json.dumps(meta, ensure_ascii=False).encode("utf-8"))
        return Spoken(file, speech.duration_s, found)

    async def _shape(
        self, speech: Speech, found: tuple[WordMark, ...] | None, caption: str, script: Script | None
    ) -> tuple[Speech, tuple[WordMark, ...] | None]:
        """A teacher's timing on the audio (ADR-045): a pause at every full stop and comma and before
        a key point, slow sentences slower. The caption words' times move with it. Unchanged speech
        when there is nothing to do or it cannot be done."""
        assert self.prosody is not None
        words = caption_words(caption)
        times = word_times(words, found, speech.duration_s)
        marks = [WordMark(w, s, e) for w, (s, e) in zip(words, times, strict=True)]
        directed = script is not None and script.directed
        try:
            shaped = await asyncio.to_thread(
                shape,
                speech,
                marks,
                words,
                script.pause_before if directed and script else frozenset(),
                script.deliveries if directed and script else (),
                self.prosody,
                self.ffmpeg,
            )
        except Exception as e:  # never let the timing break the voice
            log.warning("voice timing failed (%s): speech kept as made", e)
            return speech, found
        if shaped is None:
            return speech, found
        return shaped

    async def close(self) -> None:
        for task in list(self._inflight.values()):
            task.cancel()
        for provider in {id(p): p for p in [self.provider, *self.providers.values()]}.values():
            await provider.close()


def caption_marks(caption: str, script: Script, speech: Speech) -> tuple[WordMark, ...] | None:
    """The times of the script's entries, as marks on the caption's words (entry k is caption word k)."""
    words = caption_words(caption)
    if len(words) != len(script.spoken):
        return speech.marks
    said = [k for k, entry in enumerate(script.spoken) if entry.strip()]
    times = word_times([script.spoken[k] for k in said], speech.marks, speech.duration_s)
    return tuple(WordMark(words[k], t0, t1) for k, (t0, t1) in zip(said, times, strict=True)) or None


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
