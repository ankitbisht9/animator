"""Export jobs for generated lessons (API-triggered, one at a time per generation), and keyframe
rendering for the visual critic: in the export service's browser (P2), or without a browser (ADR-068)."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from animator.config import Settings
from animator.export.exporter import AudioSource, ExportError
from animator.export.ffmpeg import FfmpegError
from animator.export.plan import ExportSettings
from animator.export.render import BrowserPool, Viewport, build_id
from animator.export.service import export_lesson
from animator.export.site import WEB_DIST, StaticSite, ensure_web_build

log = logging.getLogger("animator.export")


@dataclass
class ExportJob:
    generation_id: str
    state: Literal["running", "done", "failed"] = "running"
    log: list[str] = field(default_factory=list)
    video: Path | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "state": self.state,
            "log": self.log[-50:],
            "video": self.video.name if self.video else None,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class ExportJobs:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.jobs: dict[str, ExportJob] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def get(self, gen_id: str) -> ExportJob | None:
        return self.jobs.get(gen_id)

    def start(
        self, gen_id: str, lesson: dict[str, Any], settings: ExportSettings, audio: AudioSource | None = None
    ) -> ExportJob:
        running = self.jobs.get(gen_id)
        if running is not None and running.state == "running":
            return running
        job = ExportJob(gen_id)
        self.jobs[gen_id] = job

        async def run() -> None:
            try:
                out = self.settings.resolve(self.settings.export_output_dir)
                res = await export_lesson(
                    lesson, gen_id, settings, out_dir=out, audio=audio, log=job.log.append
                )
                job.video = res.outputs.get("mp4")
                job.state = "done"
            except (ExportError, FfmpegError, FileNotFoundError, ValueError) as e:
                job.state = "failed"
                job.error = str(e)
            except Exception as e:
                job.state = "failed"
                job.error = f"{type(e).__name__}: {e}"
            finally:
                job.finished_at = time.time()

        self._tasks[gen_id] = asyncio.create_task(run(), name=f"export-{gen_id}")
        return job

    async def close(self) -> None:
        for t in self._tasks.values():
            t.cancel()
        for t in self._tasks.values():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t


class BrowserKeyframes:
    """One headless browser on the built web app, started on first use (visual critic)."""

    def __init__(self, dist: Path = WEB_DIST, viewport: Viewport | None = None) -> None:
        self.dist = dist
        self.viewport = viewport or Viewport(1280, 720)
        self._site: StaticSite | None = None
        self._pool: BrowserPool | None = None
        self._lock = asyncio.Lock()

    async def keyframes(
        self, scenes: list[dict[str, Any]], index: int, times: list[float], finish: str = "classic"
    ) -> list[bytes]:
        async with self._lock:
            if self._pool is None:
                await ensure_web_build(self.dist)
                site = StaticSite(self.dist)
                self._site = site.__enter__()
                pool = BrowserPool(site.url, self.viewport, 1, build_id(self.dist))
                self._pool = await pool.__aenter__()
            await self._pool.load({"title": "critic", "scenes": scenes, "finish": finish})
            return [await self._pool.renderers[0].at_png(index, t) for t in times]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.__aexit__(None, None, None)
            self._pool = None
        if self._site is not None:
            self._site.__exit__(None, None, None)
            self._site = None


class KeyframeError(RuntimeError):
    """The browserless renderer could not draw a scene."""


class SvgKeyframes:
    """Keyframes without a browser (ADR-068): `node dist/frames.cjs` draws the scene with the player's own
    components as SVG and rasterizes it with resvg (WebAssembly), the scenes before it compiled first as
    the player does. One drawing at a time, each in its own process: nothing stays in memory between the
    critic's looks (about 130 MB while it draws, under a second for three keyframes)."""

    def __init__(self, script: Path, node: str = "node", timeout_s: float = 60.0, width: int = 1280) -> None:
        self.script = script
        self.node = node
        self.timeout_s = timeout_s
        self.width = width
        self._lock = asyncio.Lock()

    async def keyframes(
        self, scenes: list[dict[str, Any]], index: int, times: list[float], finish: str = "classic"
    ) -> list[bytes]:
        request = {"scenes": scenes, "index": index, "times": times, "finish": finish, "width": self.width}
        async with self._lock:
            proc = await asyncio.create_subprocess_exec(
                self.node,
                str(self.script),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(
                    proc.communicate(json.dumps(request).encode("utf-8")), self.timeout_s
                )
            except BaseException as e:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await asyncio.shield(proc.wait())
                if isinstance(e, TimeoutError):
                    raise KeyframeError(f"no keyframes within {self.timeout_s:.0f} s") from None
                raise
        try:
            reply = json.loads(out or b"{}")
        except json.JSONDecodeError as e:
            raise KeyframeError(f"unreadable reply: {err.decode(errors='replace')[:300]}") from e
        if not reply.get("ok"):
            raise KeyframeError(str(reply.get("error") or err.decode(errors="replace")[:300] or "failed"))
        return [base64.b64decode(f) for f in reply["frames"]]

    async def close(self) -> None:
        return None


def keyframes_for(settings: Settings) -> BrowserKeyframes | SvgKeyframes | None:
    """The visual critic's keyframes as the settings ask (ANIMATOR_KEYFRAMES), when they can be drawn
    here: the built web app for the browser, the built frames script without one."""
    if settings.animator_keyframes == "svg":
        script = settings.resolve(settings.animator_frames_script)
        if script.is_file():
            timeout = max(60.0, 2 * settings.engine_worker_call_timeout_s)
            return SvgKeyframes(script, settings.engine_worker_node, timeout)
        log.warning("no keyframes for the visual critic: %s is missing (pnpm run build:worker)", script)
        return None
    if (WEB_DIST / "index.html").exists():
        return BrowserKeyframes()
    log.warning("no keyframes for the visual critic: the web app is not built (pnpm run build:web)")
    return None
