"""ffmpeg / ffprobe: encode PNG frames from stdin, concatenate segments, mux audio, probe.

Encoding is H.264 (libx264), yuv420p, CRF, faststart, with BT.709 conversion and tags so
colours match the browser in standard players. `+bitexact` keeps version strings out of the
container, so identical frames give byte-identical files in the same environment.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from animator.export.plan import ExportSettings

BITEXACT = ["-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact"]
# RGB (sRGB) frames -> limited-range BT.709 YUV 4:2:0, tagged as such.
COLOR = [
    "-vf",
    "scale=out_color_matrix=bt709:out_range=tv:flags=bicubic+accurate_rnd+full_chroma_int,format=yuv420p",
    "-colorspace",
    "bt709",
    "-color_primaries",
    "bt709",
    "-color_trc",
    "bt709",
    "-color_range",
    "tv",
]


class FfmpegError(RuntimeError):
    pass


def find_ffmpeg(configured: str = "ffmpeg") -> str:
    path = shutil.which(configured)
    if path is None:
        raise FfmpegError(f"ffmpeg not found (FFMPEG_PATH={configured!r}); install ffmpeg and put it on PATH")
    return path


def find_ffprobe(ffmpeg: str) -> str:
    sibling = Path(ffmpeg).with_name("ffprobe" + Path(ffmpeg).suffix)
    if sibling.exists():
        return str(sibling)
    path = shutil.which("ffprobe")
    if path is None:
        raise FfmpegError("ffprobe not found next to ffmpeg or on PATH")
    return path


def ffmpeg_version(ffmpeg: str) -> str:
    out = subprocess.run([ffmpeg, "-hide_banner", "-version"], capture_output=True, text=True, check=True)
    return out.stdout.splitlines()[0].strip() if out.stdout else "unknown"


def encoder_args(ffmpeg: str, settings: ExportSettings, outputs: dict[str, Path]) -> list[str]:
    """One ffmpeg process reading PNG frames on stdin and writing every requested format."""
    args = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "image2pipe",
        "-framerate",
        str(settings.fps),
        "-c:v",
        "png",
        "-i",
        "-",
    ]
    for fmt, path in outputs.items():
        if fmt == "mp4":
            args += [
                "-map",
                "0:v",
                "-c:v",
                "libx264",
                "-preset",
                settings.preset,
                "-crf",
                str(settings.crf),
                *COLOR,
                "-movflags",
                "+faststart",
                *BITEXACT,
                str(path),
            ]
        elif fmt == "webm":
            args += [
                "-map",
                "0:v",
                "-c:v",
                "libvpx-vp9",
                "-crf",
                str(min(63, settings.crf + 13)),
                "-b:v",
                "0",
                "-deadline",
                "good",
                "-cpu-used",
                "4",
                "-row-mt",
                "1",
                *COLOR,
                *BITEXACT,
                str(path),
            ]
        else:
            raise FfmpegError(f"unsupported format '{fmt}'")
    return args


class SegmentEncoder:
    """`async with SegmentEncoder(...) as enc: await enc.write(png)`; raises FfmpegError on failure.

    Leaving the block normally finishes the encode; leaving it with an exception kills ffmpeg
    and deletes the partial outputs.
    """

    def __init__(self, ffmpeg: str, settings: ExportSettings, outputs: dict[str, Path]) -> None:
        self.args = encoder_args(ffmpeg, settings, outputs)
        self.outputs = outputs
        self._proc: asyncio.subprocess.Process | None = None
        self._stderr: asyncio.Task[bytes] | None = None
        self.frames = 0

    async def __aenter__(self) -> Self:
        for p in self.outputs.values():
            p.parent.mkdir(parents=True, exist_ok=True)
        self._proc = await asyncio.create_subprocess_exec(
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._proc.stderr is not None
        self._stderr = asyncio.create_task(self._proc.stderr.read())
        return self

    async def write(self, png: bytes) -> None:
        proc = self._proc
        assert proc is not None and proc.stdin is not None, "encoder not started"
        try:
            proc.stdin.write(png)
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as e:
            raise FfmpegError(f"ffmpeg stopped reading frames: {await self._error_text()}") from e
        self.frames += 1

    async def _error_text(self) -> str:
        if self._proc is not None and self._proc.returncode is None:
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=10)
            except TimeoutError:
                self._proc.kill()
        err = await self._stderr if self._stderr is not None else b""
        return err.decode("utf-8", "replace").strip()[-2000:] or "(no output)"

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object
    ) -> None:
        proc = self._proc
        assert proc is not None and proc.stdin is not None
        if exc_type is not None:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            if self._stderr is not None:
                await self._stderr
            for p in self.outputs.values():
                p.unlink(missing_ok=True)
            return
        proc.stdin.close()
        code = await proc.wait()
        err = await self._stderr if self._stderr is not None else b""
        if code != 0:
            for p in self.outputs.values():
                p.unlink(missing_ok=True)
            raise FfmpegError(f"ffmpeg exited with {code}: {err.decode('utf-8', 'replace').strip()[-2000:]}")


async def run_ffmpeg(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        raise FfmpegError(
            f"ffmpeg exited with {proc.returncode}: {err.decode('utf-8', 'replace').strip()[-2000:]}"
        )


def _concat_list_line(path: Path) -> str:
    escaped = str(path.resolve()).replace("\\", "/").replace("'", "'\\''")
    return f"file '{escaped}'\n"


async def concat(ffmpeg: str, parts: list[Path], out: Path) -> None:
    """Join segments without re-encoding (they share codec settings)."""
    if not parts:
        raise FfmpegError("nothing to concatenate")
    out.parent.mkdir(parents=True, exist_ok=True)
    listing = out.with_name(out.name + ".concat.txt")
    listing.write_text("".join(_concat_list_line(p) for p in parts), encoding="utf-8")
    tmp = out.with_name(out.stem + ".partial" + out.suffix)
    args = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0"]
    args += ["-i", str(listing), "-map", "0", "-c", "copy"]
    if out.suffix == ".mp4":
        args += ["-movflags", "+faststart"]
    args += [*BITEXACT, str(tmp)]
    try:
        await run_ffmpeg(args)
        tmp.replace(out)
    finally:
        listing.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)


async def mux_audio(ffmpeg: str, video: Path, audio: Path, out: Path, duration: float) -> None:
    """Add an audio track: padded with silence or trimmed to the video's exact duration."""
    codec = ["-c:a", "aac", "-b:a", "192k"] if out.suffix == ".mp4" else ["-c:a", "libopus", "-b:a", "128k"]
    tmp = out.with_name(out.stem + ".partial" + out.suffix)
    args = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video),
        "-i",
        str(audio),
    ]
    args += ["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", *codec, "-af", "apad", "-t", f"{duration:.6f}"]
    if out.suffix == ".mp4":
        args += ["-movflags", "+faststart"]
    args += [*BITEXACT, str(tmp)]
    try:
        await run_ffmpeg(args)
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)


@dataclass(frozen=True)
class Probe:
    codec: str
    width: int
    height: int
    pix_fmt: str
    fps: float
    frames: int
    duration: float
    has_audio: bool
    color_space: str


def probe(ffprobe: str, path: Path) -> Probe:
    """Container facts, with frames counted by reading every packet (catches truncation)."""
    res = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-count_packets",
            "-show_entries",
            "stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate,nb_read_packets,color_space"
            ":format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        raise FfmpegError(f"ffprobe failed on {path.name}: {res.stderr.strip()[-1000:]}")
    data = json.loads(res.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise FfmpegError(f"{path.name} has no video stream")
    num, _, den = str(video.get("r_frame_rate", "0/1")).partition("/")
    return Probe(
        codec=str(video.get("codec_name", "")),
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        pix_fmt=str(video.get("pix_fmt", "")),
        fps=float(num) / float(den or 1),
        frames=int(video.get("nb_read_packets", 0)),
        duration=float(data.get("format", {}).get("duration", 0.0)),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        color_space=str(video.get("color_space", "")),
    )


def mp4_top_level_boxes(path: Path) -> list[str]:
    """Top-level MP4 box types in file order (faststart puts 'moov' before 'mdat')."""
    boxes: list[str] = []
    with path.open("rb") as f:
        while True:
            header = f.read(8)
            if len(header) < 8:
                break
            size = int.from_bytes(header[:4], "big")
            kind = header[4:8].decode("latin-1")
            if size == 1:
                size = int.from_bytes(f.read(8), "big")
                f.seek(size - 16, 1)
            elif size == 0:
                boxes.append(kind)
                break
            else:
                f.seek(size - 8, 1)
            boxes.append(kind)
    return boxes
