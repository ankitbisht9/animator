"""Export a lesson to video: one encoded segment per scene, then one concatenated file.

    summary = pool.load(lesson)              # every scene must pass engine validation (P4, P5)
    for each scene segment:
        cached?  -> reuse (same scenes so far, settings, web build and engine version)
        else     -> render its frames on N browsers in parallel, feed ffmpeg in frame order,
                    verify the frame count; on failure restart the browsers and retry
    all segments present -> concatenate (no re-encode), optional audio track, verify, and
                            write the animation bundle and an export report.

Frames are rendered at scene-local times by the same `evaluate` + `<SceneView>` as the
player (P1, P2), so segment boundaries never change a pixel.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from animator.export.ffmpeg import (
    FfmpegError,
    Probe,
    SegmentEncoder,
    concat,
    ffmpeg_version,
    find_ffprobe,
    mp4_top_level_boxes,
    mux_audio,
    probe,
)
from animator.export.plan import (
    PIPELINE_VERSION,
    ExportSettings,
    Segment,
    frames_digest,
    plan_segments,
    segment_key,
    total_frames,
)

BUNDLE_FORMAT = "animator.animation"
BUNDLE_VERSION = 1


class FrameRenderer(Protocol):
    async def at_png(self, scene_index: int, local_t: float) -> bytes: ...


class RenderPool(Protocol):
    build_id: str

    @property
    def renderers(self) -> Sequence[FrameRenderer]: ...

    @property
    def browser_version(self) -> str: ...

    async def load(self, lesson: dict[str, Any]) -> dict[str, Any]: ...

    async def restart(self) -> None: ...


class AudioSource(Protocol):
    """Audio-track hook (spec Section 16). A TTS provider will implement this; the
    exporter pads or trims the track to the video's exact duration."""

    async def track(self, lesson: dict[str, Any], duration: float, workdir: Path) -> Path | None: ...


@dataclass(frozen=True)
class FileAudio:
    path: Path

    async def track(self, lesson: dict[str, Any], duration: float, workdir: Path) -> Path | None:
        if not self.path.is_file():
            raise ExportError(f"audio file not found: {self.path}")
        return self.path


def _keyed(lesson: dict[str, Any]) -> list[Any]:
    """What each segment's frames depend on: its scene, and its narration when there is a voice
    (the captions then follow the speech word by word)."""
    scenes: list[Any] = lesson["scenes"]
    narration = lesson.get("narration")
    if not narration:
        return scenes
    return [
        {"scene": sc, "narration": narration[i] if i < len(narration) else None}
        for i, sc in enumerate(scenes)
    ]


class ExportError(RuntimeError):
    def __init__(self, message: str, scene_index: int | None = None, scene_id: str | None = None) -> None:
        super().__init__(message)
        self.scene_index = scene_index
        self.scene_id = scene_id


@dataclass
class SegmentResult:
    index: int
    scene_id: str
    title: str
    first_frame: int
    frames: int
    key: str
    cached: bool
    attempts: int
    seconds: float
    frame_hashes: list[str]
    files: dict[str, str]
    sha256: dict[str, str]

    @property
    def fps(self) -> float:
        return self.frames / self.seconds if self.seconds > 0 else 0.0

    def manifest(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "index": self.index,
            "scene_id": self.scene_id,
            "title": self.title,
            "first_frame": self.first_frame,
            "frames": self.frames,
            "attempts": self.attempts,
            "seconds": round(self.seconds, 3),
            "files": self.files,
            "sha256": self.sha256,
            "frame_hashes": self.frame_hashes,
        }

    def summary(self) -> dict[str, Any]:
        out = {k: v for k, v in self.manifest().items() if k not in ("frame_hashes", "key")}
        out["cached"] = self.cached
        out["fps"] = None if self.cached else round(self.fps, 1)  # cached: rendered in an earlier run
        out["frames_sha256"] = frames_digest(self.frame_hashes)
        return out


@dataclass
class ExportResult:
    name: str
    root: Path
    complete: bool
    outputs: dict[str, Path]
    bundle: Path
    report: Path
    segments: list[SegmentResult]
    total_frames: int
    duration: float
    frames_sha256: str


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _describe(e: BaseException) -> str:
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    text = str(e).strip().splitlines()[0] if str(e).strip() else ""
    return f"{type(e).__name__}: {text}" if text else type(e).__name__


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


async def render_frames(
    renderers: Sequence[FrameRenderer],
    segment: Segment,
    fps: int,
    sink: Callable[[bytes], Awaitable[None]],
    frame_timeout_s: float,
) -> list[str]:
    """Render a segment's frames on every renderer in parallel and pass them to `sink`
    strictly in frame order. Returns the sha256 of each frame's PNG, in order."""
    end = segment.end_frame
    window = 4 * len(renderers)  # frames rendered ahead of the writer, per segment
    done: dict[int, bytes] = {}
    cursor = {"next": segment.first_frame, "written": segment.first_frame}
    cond = asyncio.Condition()
    hashes: list[str] = []

    async def work(renderer: FrameRenderer) -> None:
        while True:
            async with cond:
                await cond.wait_for(
                    lambda: cursor["next"] >= end or cursor["next"] - cursor["written"] < window
                )
                frame = cursor["next"]
                if frame >= end:
                    return
                cursor["next"] = frame + 1
            png = await asyncio.wait_for(
                renderer.at_png(segment.index, segment.local_time(frame, fps)), frame_timeout_s
            )
            async with cond:
                done[frame] = png
                cond.notify_all()

    async def write() -> None:
        while cursor["written"] < end:
            async with cond:
                await cond.wait_for(lambda: cursor["written"] in done)
                png = done.pop(cursor["written"])
            hashes.append(hashlib.sha256(png).hexdigest())
            await sink(png)
            async with cond:
                cursor["written"] += 1
                cond.notify_all()

    async with asyncio.TaskGroup() as tg:
        for r in renderers:
            tg.create_task(work(r))
        tg.create_task(write())
    return hashes


class Exporter:
    def __init__(
        self,
        settings: ExportSettings,
        ffmpeg: str,
        log: Callable[[str], None] = print,
        frame_timeout_s: float = 60.0,
    ) -> None:
        self.settings = settings
        self.ffmpeg = ffmpeg
        self.ffprobe = find_ffprobe(ffmpeg)
        self.log = log
        self.frame_timeout_s = frame_timeout_s

    # ------------------------------------------------------------------ public

    async def export(
        self,
        pool: RenderPool,
        lesson: dict[str, Any],
        out_dir: Path,
        name: str,
        scenes: Sequence[int] | None = None,
        audio: AudioSource | None = None,
    ) -> ExportResult:
        started = time.perf_counter()
        s = self.settings
        summary = await pool.load(lesson)
        bad = [(i, sc) for i, sc in enumerate(summary["scenes"]) if not sc["ok"]]
        if bad:
            detail = "; ".join(
                f"scene {i + 1} '{sc['id']}': {sc['errors'][0]['message'] if sc['errors'] else 'invalid'}"
                for i, sc in bad
            )
            i0, sc0 = bad[0]
            raise ExportError(f"cannot export, scenes failed validation: {detail}", i0, str(sc0["id"]))

        segments = plan_segments(summary["scenes"], s.fps)
        wanted = set(range(len(segments))) if scenes is None else set(scenes)
        root = out_dir / name
        seg_dir = root / "segments"
        self.log(
            f"export '{name}': {len(segments)} scenes, {summary['duration']:.1f} s, "
            f"{s.width}x{s.height} @ {s.fps} fps, {len(pool.renderers)} browsers"
        )

        results: list[SegmentResult] = []
        for seg in segments:
            key = segment_key(_keyed(lesson), seg.index, s, pool.build_id, str(summary["engineVersion"]))
            hit = self._cached(seg_dir, seg, key)
            if hit is not None:
                self.log(f"  scene {seg.index + 1:>2} {seg.scene_id}: cached ({seg.frame_count} frames)")
                results.append(hit)
            elif seg.index in wanted:
                results.append(await self._render_segment(pool, seg, key, seg_dir))

        duration = float(summary["duration"])
        n_total = total_frames(duration, s.fps)
        complete = len(results) == len(segments)
        outputs: dict[str, Path] = {}
        probes: dict[str, Probe] = {}
        if complete:
            outputs = await self._assemble(results, seg_dir, root, name, lesson, duration, audio)
            probes = {fmt: self._verify(path, n_total, fmt) for fmt, path in outputs.items()}
        else:
            missing = [seg.scene_id for seg in segments if seg.index not in {r.index for r in results}]
            self.log(f"  segments only: {len(missing)} scene(s) not exported yet ({', '.join(missing)})")

        digest = frames_digest([h for r in results for h in r.frame_hashes])
        bundle = root / f"{name}.animation.json"
        _write_json(bundle, make_bundle(lesson, summary))
        report = root / f"{name}.export.json"
        rendered = [r for r in results if not r.cached]
        render_s = sum(r.seconds for r in rendered)
        _write_json(
            report,
            {
                "name": name,
                "title": summary.get("title", lesson.get("title", name)),
                "complete": complete,
                "settings": {**asdict(s), "formats": list(s.formats)},
                "versions": {
                    "engine": summary.get("engineVersion"),
                    "dsl": summary.get("dslVersion"),
                    "browser": pool.browser_version,
                    "ffmpeg": ffmpeg_version(self.ffmpeg),
                    "web_build": pool.build_id,
                    "pipeline": PIPELINE_VERSION,
                },
                "totals": {
                    "scenes": len(segments),
                    "frames": n_total,
                    "duration_s": duration,
                    "rendered_frames": sum(r.frames for r in rendered),
                    "render_s": round(render_s, 2),
                    "render_fps": round(sum(r.frames for r in rendered) / render_s, 1) if render_s else None,
                    "wall_s": round(time.perf_counter() - started, 2),
                },
                "frames_sha256": digest,
                "segments": [r.summary() for r in results],
                "outputs": {
                    fmt: {
                        "file": path.name,
                        "bytes": path.stat().st_size,
                        "sha256": sha256_file(path),
                        "probe": asdict(probes[fmt]),
                    }
                    for fmt, path in outputs.items()
                },
            },
        )
        if complete:
            self.log(f"  done: {', '.join(str(p) for p in outputs.values())}")
        return ExportResult(name, root, complete, outputs, bundle, report, results, n_total, duration, digest)

    # ------------------------------------------------------------------ segments

    def _cached(self, seg_dir: Path, seg: Segment, key: str) -> SegmentResult | None:
        manifest = seg_dir / f"{seg.stem}.json"
        if not manifest.exists():
            return None
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if m.get("key") != key or m.get("frames") != seg.frame_count:
            return None
        files: dict[str, str] = m.get("files", {})
        if set(files) != set(self.settings.formats):
            return None
        for fmt, file in files.items():
            path = seg_dir / file
            if not path.exists() or sha256_file(path) != m.get("sha256", {}).get(fmt):
                return None
        return SegmentResult(
            index=seg.index,
            scene_id=seg.scene_id,
            title=seg.title,
            first_frame=seg.first_frame,
            frames=seg.frame_count,
            key=key,
            cached=True,
            attempts=0,
            seconds=0.0,
            frame_hashes=list(m.get("frame_hashes", [])),
            files=files,
            sha256=dict(m.get("sha256", {})),
        )

    async def _render_segment(self, pool: RenderPool, seg: Segment, key: str, seg_dir: Path) -> SegmentResult:
        s = self.settings
        files = {fmt: seg_dir / f"{seg.stem}.{fmt}" for fmt in s.formats}
        attempt = 0
        while True:
            attempt += 1
            t0 = time.perf_counter()
            try:
                async with SegmentEncoder(self.ffmpeg, s, files) as enc:
                    hashes = await render_frames(pool.renderers, seg, s.fps, enc.write, self.frame_timeout_s)
                for fmt, path in files.items():
                    got = probe(self.ffprobe, path).frames
                    if got != seg.frame_count:
                        raise FfmpegError(f"{fmt} segment has {got} frames, expected {seg.frame_count}")
                break
            except Exception as e:
                for p in files.values():
                    p.unlink(missing_ok=True)
                cause = _describe(e)
                if attempt > s.segment_retries:
                    raise ExportError(
                        f"scene {seg.index + 1} '{seg.scene_id}' failed after {attempt} attempt(s): {cause}",
                        seg.index,
                        seg.scene_id,
                    ) from e
                self.log(
                    f"  scene {seg.index + 1:>2} {seg.scene_id}: attempt {attempt} failed ({cause}); retrying"
                )
                await pool.restart()
        seconds = time.perf_counter() - t0
        result = SegmentResult(
            index=seg.index,
            scene_id=seg.scene_id,
            title=seg.title,
            first_frame=seg.first_frame,
            frames=seg.frame_count,
            key=key,
            cached=False,
            attempts=attempt,
            seconds=seconds,
            frame_hashes=hashes,
            files={fmt: p.name for fmt, p in files.items()},
            sha256={fmt: sha256_file(p) for fmt, p in files.items()},
        )
        _write_json(seg_dir / f"{seg.stem}.json", result.manifest())
        self.log(
            f"  scene {seg.index + 1:>2} {seg.scene_id}: {seg.frame_count} frames in {seconds:.1f} s "
            f"({result.fps:.1f} fps)"
        )
        return result

    # ------------------------------------------------------------------ final file

    async def _assemble(
        self,
        results: list[SegmentResult],
        seg_dir: Path,
        root: Path,
        name: str,
        lesson: dict[str, Any],
        duration: float,
        audio: AudioSource | None,
    ) -> dict[str, Path]:
        track = await audio.track(lesson, duration, root) if audio is not None else None
        outputs: dict[str, Path] = {}
        for fmt in self.settings.formats:
            parts = [seg_dir / r.files[fmt] for r in sorted(results, key=lambda r: r.index)]
            final = root / f"{name}.{fmt}"
            if track is None:
                await concat(self.ffmpeg, parts, final)
            else:
                silent = root / f"{name}.video.{fmt}"
                try:
                    await concat(self.ffmpeg, parts, silent)
                    await mux_audio(self.ffmpeg, silent, track, final, duration)
                finally:
                    silent.unlink(missing_ok=True)
            outputs[fmt] = final
        return outputs

    def _verify(self, path: Path, n_total: int, fmt: str) -> Probe:
        """Fail loudly unless the file has exactly the lesson's frames, size and duration."""
        s = self.settings
        p = probe(self.ffprobe, path)
        problems = []
        if p.frames != n_total:
            problems.append(f"{p.frames} frames, expected {n_total}")
        if (p.width, p.height) != (s.width, s.height):
            problems.append(f"size {p.width}x{p.height}, expected {s.width}x{s.height}")
        if abs(p.duration - n_total / s.fps) > 1.5 / s.fps:
            problems.append(f"duration {p.duration:.3f} s, expected {n_total / s.fps:.3f} s")
        if p.pix_fmt != "yuv420p":
            problems.append(f"pixel format {p.pix_fmt}")
        if fmt == "mp4":
            if p.codec != "h264":
                problems.append(f"codec {p.codec}")
            boxes = mp4_top_level_boxes(path)
            if "moov" in boxes and "mdat" in boxes and boxes.index("moov") > boxes.index("mdat"):
                problems.append("not faststart (moov after mdat)")
        if problems:
            raise ExportError(f"{path.name} failed verification: {'; '.join(problems)}")
        return p


# ---------------------------------------------------------------------- bundles


def make_bundle(lesson: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    """Everything needed to re-render or edit the animation later (spec Section 15)."""
    return {
        "format": BUNDLE_FORMAT,
        "version": BUNDLE_VERSION,
        "title": lesson.get("title", summary.get("title")),
        "dsl_version": summary.get("dslVersion"),
        "engine_version": summary.get("engineVersion"),
        "duration": summary.get("duration"),
        "themes": sorted({str(sc["themeId"]) for sc in summary["scenes"] if sc.get("themeId")}),
        "plan": lesson.get("plan"),
        "storyboard": lesson.get("storyboard"),
        "assets": lesson.get("assets", []),
        "scenes": lesson["scenes"],
    }


def load_bundle(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != BUNDLE_FORMAT:
        raise ExportError(f"{path.name} is not an animation bundle")
    if data.get("version") != BUNDLE_VERSION:
        raise ExportError(f"{path.name} has bundle version {data.get('version')}, expected {BUNDLE_VERSION}")
    if not isinstance(data.get("scenes"), list) or not data["scenes"]:
        raise ExportError(f"{path.name} has no scenes")
    return {
        "title": data.get("title") or path.stem,
        "scenes": data["scenes"],
        "plan": data.get("plan"),
        "storyboard": data.get("storyboard"),
        "assets": data.get("assets", []),
    }
