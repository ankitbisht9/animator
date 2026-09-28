"""Real export through headless Chromium and ffmpeg (needs the built web app).

The determinism test is the spec's: the same stored DSL, rendered twice, must give
identical frame hashes. Here it also gives a byte-identical MP4, with a different
number of browsers each time. In the pinned Docker environment this runs as-is.
"""

from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from animator.export.exporter import ExportResult
from animator.export.ffmpeg import ffmpeg_version, find_ffmpeg
from animator.export.plan import ExportSettings
from animator.export.render import BrowserPool, RenderSession, Viewport, build_id, load_fixture_lesson
from animator.export.service import export_lesson
from animator.export.site import WEB_DIST, StaticSite

pytestmark = pytest.mark.e2e

# Small and short: the property under test does not depend on resolution.


def settings(workers: int) -> ExportSettings:
    return ExportSettings(
        width=640, height=360, fps=10, crf=20, preset="veryfast", workers=workers, segment_retries=1
    )


async def run(tmp: Path, workers: int, lesson: dict[str, Any] | None = None) -> ExportResult:
    if not (WEB_DIST / "index.html").exists():
        pytest.fail("web app not built: run `make build-web`")
    return await export_lesson(
        lesson or load_fixture_lesson("engine-tour"),
        "tour",
        settings(workers),
        out_dir=tmp,
        log=lambda _: None,
    )


async def test_same_dsl_rendered_twice_gives_identical_frames_and_video(tmp_path: Path) -> None:
    a = await run(tmp_path / "a", workers=1)
    b = await run(tmp_path / "b", workers=3)
    assert a.complete and b.complete
    assert [s.frame_hashes for s in a.segments] == [s.frame_hashes for s in b.segments]
    assert a.frames_sha256 == b.frames_sha256
    assert a.outputs["mp4"].read_bytes() == b.outputs["mp4"].read_bytes()
    assert a.total_frames == 170  # 17 s at 10 fps, verified against ffprobe by the exporter


async def test_a_browser_crash_mid_segment_is_retried_with_identical_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean = await run(tmp_path / "clean", workers=2)

    original = RenderSession.at_png
    state = {"calls": 0, "crashed": False}

    async def crashing(self: RenderSession, scene_index: int, local_t: float) -> bytes:
        state["calls"] += 1
        if scene_index == 1 and not state["crashed"] and state["calls"] > 90:
            state["crashed"] = True
            await self._browser.close()  # type: ignore[union-attr]  # the browser dies mid-segment
        return await original(self, scene_index, local_t)

    monkeypatch.setattr(RenderSession, "at_png", crashing)
    res = await run(tmp_path / "crash", workers=2)
    assert state["crashed"]
    assert [s.attempts for s in res.segments] == [1, 2]
    assert res.frames_sha256 == clean.frames_sha256
    assert res.outputs["mp4"].read_bytes() == clean.outputs["mp4"].read_bytes()


def decode_frame(mp4: Path, index: int) -> np.ndarray:
    """Decode one frame the way players do: BT.709 limited range -> RGB."""
    out = subprocess.run(
        [
            find_ffmpeg(),
            "-v",
            "error",
            "-i",
            str(mp4),
            "-vf",
            # "\\," escapes the comma inside ffmpeg's filter syntax.
            f"select=eq(n\\,{index}),scale=in_color_matrix=bt709:in_range=tv,format=rgb24",
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-c:v",
            "png",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.asarray(Image.open(io.BytesIO(out)).convert("RGB")).astype(np.int16)


async def test_video_colours_match_the_browser(tmp_path: Path) -> None:
    res = await run(tmp_path, workers=2)
    seg = res.segments[1]  # a frame in the middle of scene 2
    frame = seg.first_frame + seg.frames // 2
    local_t = (frame - seg.first_frame) / 10  # scene 2 starts on a frame boundary
    with StaticSite(WEB_DIST) as site:
        async with BrowserPool(site.url, Viewport(640, 360), 1, build_id(WEB_DIST)) as pool:
            await pool.load(load_fixture_lesson("engine-tour"))
            png = await pool.renderers[0].at_png(1, local_t)
    browser = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(np.int16)
    video = decode_frame(res.outputs["mp4"], frame)
    mean = np.abs(browser - video).mean(axis=(0, 1))
    # A BT.601/709 mix-up shifts saturated colours by 10+ levels; lossy 4:2:0 coding stays well below 3.
    assert mean.max() < 3.0, f"mean per-channel difference {mean.round(2).tolist()}"


@pytest.mark.skipif(
    os.environ.get("ANIMATOR_PINNED_ENV") != "1",
    reason="TODO(Phase 6, ADR-026): the pinned Docker export image is written but not yet built and "
    "verified (blocked on disk space); run `make test-docker` to run this whole suite inside it",
)
def test_pinned_image_uses_the_pinned_tools() -> None:
    assert "7.1.5" in ffmpeg_version(find_ffmpeg())
    assert Path("/ms-playwright").is_dir()
