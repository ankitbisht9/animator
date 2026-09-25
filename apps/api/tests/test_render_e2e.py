"""Browser golden frames and pixel determinism through the real /render page (Chromium).

Needs the built web app (`make build-web`) and Playwright's Chromium (`make setup`).
Goldens are per-platform (anti-aliasing differs across OSes); regenerate with
UPDATE_GOLDENS=1. Linux goldens are produced in the pinned Docker export
environment (Phase 6); until then the Linux comparison is skipped, not faked.
"""

from __future__ import annotations

import hashlib
import io
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np
import pytest
import pytest_asyncio
from PIL import Image
from playwright.async_api import async_playwright

from animator.export.render import RenderSession, Viewport, load_fixture_lesson
from animator.export.site import WEB_DIST, StaticSite

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio(loop_scope="module")]

GOLDEN_DIR = Path(__file__).parent / "golden" / sys.platform
FPS = 30
FRAMES = [0, 15, 45, 75, 105, 150, 239, 270, 330, 420]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def session() -> AsyncIterator[RenderSession]:
    if not (WEB_DIST / "index.html").exists():
        pytest.fail("web app not built: run `make build-web`")
    with StaticSite() as site:
        async with async_playwright() as pw:
            s = await RenderSession(site.url, Viewport(960, 540)).start(pw)
            summary = await s.load(load_fixture_lesson("engine-tour"))
            assert all(sc["ok"] for sc in summary["scenes"]), summary
            yield s
            await s.close()


async def test_frames_are_pixel_deterministic_regardless_of_order(session: RenderSession) -> None:
    forward = {f: hashlib.sha256(await session.frame_png(f, FPS)).hexdigest() for f in FRAMES}
    backward = {f: hashlib.sha256(await session.frame_png(f, FPS)).hexdigest() for f in reversed(FRAMES)}
    assert forward == backward


def _diff_ratio(a: bytes, b: bytes) -> float:
    x = np.asarray(Image.open(io.BytesIO(a)).convert("RGB")).astype(np.int16)
    y = np.asarray(Image.open(io.BytesIO(b)).convert("RGB")).astype(np.int16)
    if x.shape != y.shape:
        return 1.0
    return float((np.abs(x - y).max(axis=2) > 8).mean())


@pytest.mark.parametrize("frame", FRAMES)
async def test_golden_frames(session: RenderSession, frame: int) -> None:
    png = await session.frame_png(frame, FPS)
    golden = GOLDEN_DIR / f"engine-tour-{frame:04d}.png"
    if os.environ.get("UPDATE_GOLDENS") == "1":
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_bytes(png)
        return
    if not GOLDEN_DIR.exists():
        pytest.skip(
            f"no golden frames for platform '{sys.platform}' yet "
            "(generated in the pinned Docker env, Phase 6)"
        )
    assert golden.exists(), f"missing golden {golden.name}: run with UPDATE_GOLDENS=1"
    ratio = _diff_ratio(png, golden.read_bytes())
    assert ratio < 0.002, f"{golden.name}: {ratio:.4%} of pixels differ"
