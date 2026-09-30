"""The narration track of an exported lesson (TTS on, ADR-037): every beat's audio placed at its time
in the lesson, as one WAV that the exporter muxes into the video (padded or trimmed to the video's
exact duration). Beats never overlap after retiming; if they did, the later beat cuts the earlier
one, as in the player."""

from __future__ import annotations

import asyncio
import wave
from pathlib import Path
from typing import Any

from animator.export.exporter import ExportError
from animator.export.ffmpeg import FfmpegError, find_ffmpeg
from animator.tts.cache import AUDIO_NAME

RATE = 24000  # Hz, mono 16-bit: what every provider's speech is decoded to


class NarrationAudio:
    def __init__(self, directory: Path, ffmpeg: str = "ffmpeg", concurrency: int = 6) -> None:
        self.directory = directory
        self.ffmpeg = ffmpeg
        self.concurrency = concurrency

    def _placed(self, lesson: dict[str, Any]) -> list[tuple[float, Path]]:
        narration = lesson.get("narration") or []
        placed: list[tuple[float, Path]] = []
        start = 0.0
        for i, dsl in enumerate(lesson.get("scenes") or []):
            record = narration[i] if i < len(narration) else None
            for beat in (record or {}).get("beats") or []:
                name = str(beat.get("audio", ""))
                path = self.directory / name
                if not AUDIO_NAME.match(name) or not path.is_file():
                    raise ExportError(
                        f"narration audio '{name}' is missing from {self.directory} (the speech cache was "
                        "cleared?); export again with ANIMATOR_TTS_ENABLED=0 for a video without voice",
                        i,
                    )
                placed.append((start + float(beat.get("start", 0.0)), path))
            start += float((dsl or {}).get("duration") or 0.0)
        return placed

    async def track(self, lesson: dict[str, Any], duration: float, workdir: Path) -> Path | None:
        placed = self._placed(lesson)
        if not placed:
            return None
        ffmpeg = find_ffmpeg(self.ffmpeg)
        sem = asyncio.Semaphore(self.concurrency)

        async def decode(path: Path) -> bytes:
            async with sem:
                return await _decode(ffmpeg, path)

        pcms = await asyncio.gather(*(decode(p) for _, p in placed))
        track = bytearray(round(duration * RATE) * 2)
        for (t, _), pcm in zip(placed, pcms, strict=True):
            at = round(t * RATE) * 2
            end = min(len(track), at + len(pcm))
            if end > at:
                track[at:end] = pcm[: end - at]
        out = workdir / "narration.wav"
        await asyncio.to_thread(_write_wav, out, bytes(track))
        return out


def _write_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


async def _decode(ffmpeg: str, path: Path) -> bytes:
    """Any audio file -> raw mono 16-bit PCM at RATE."""
    args = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path)]
    args += ["-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(RATE), "-"]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise FfmpegError(f"decoding {path.name} failed: {err.decode(errors='replace').strip()[:300]}")
    return out
