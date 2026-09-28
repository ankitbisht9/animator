"""The export service: serve the built web app, start the browser pool, run the exporter."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from animator.config import get_settings
from animator.export.exporter import AudioSource, Exporter, ExportResult
from animator.export.ffmpeg import find_ffmpeg
from animator.export.plan import ExportSettings
from animator.export.render import BrowserPool, Viewport, build_id
from animator.export.site import WEB_DIST, StaticSite


async def export_lesson(
    lesson: dict[str, Any],
    name: str,
    settings: ExportSettings | None = None,
    out_dir: Path | None = None,
    scenes: Sequence[int] | None = None,
    audio: AudioSource | None = None,
    log: Callable[[str], None] = print,
    dist: Path = WEB_DIST,
) -> ExportResult:
    app = get_settings()
    s = settings or ExportSettings.from_settings(app)
    ffmpeg = find_ffmpeg(app.ffmpeg_path)
    out = out_dir or app.resolve(app.export_output_dir)
    with StaticSite(dist) as site:
        viewport = Viewport(s.width, s.height)
        async with BrowserPool(site.url, viewport, s.workers, build_id(dist)) as pool:
            return await Exporter(s, ffmpeg, log).export(pool, lesson, out, name, scenes, audio)
