"""Export jobs for generated lessons (API-triggered, one at a time per generation), and keyframe
rendering for the visual critic. Both reuse the export service's browser rendering (P2)."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from animator.config import Settings
from animator.export.exporter import ExportError
from animator.export.ffmpeg import FfmpegError
from animator.export.plan import ExportSettings
from animator.export.render import BrowserPool, Viewport, build_id
from animator.export.service import export_lesson
from animator.export.site import WEB_DIST, StaticSite, ensure_web_build


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

    def start(self, gen_id: str, lesson: dict[str, Any], settings: ExportSettings) -> ExportJob:
        running = self.jobs.get(gen_id)
        if running is not None and running.state == "running":
            return running
        job = ExportJob(gen_id)
        self.jobs[gen_id] = job

        async def run() -> None:
            try:
                out = self.settings.resolve(self.settings.export_output_dir)
                res = await export_lesson(lesson, gen_id, settings, out_dir=out, log=job.log.append)
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

    async def keyframes(self, scenes: list[dict[str, Any]], index: int, times: list[float]) -> list[bytes]:
        async with self._lock:
            if self._pool is None:
                await ensure_web_build(self.dist)
                site = StaticSite(self.dist)
                self._site = site.__enter__()
                pool = BrowserPool(site.url, self.viewport, 1, build_id(self.dist))
                self._pool = await pool.__aenter__()
            await self._pool.load({"title": "critic", "scenes": scenes})
            return [await self._pool.renderers[0].at_png(index, t) for t in times]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.__aexit__(None, None, None)
            self._pool = None
        if self._site is not None:
            self._site.__exit__(None, None, None)
            self._site = None
