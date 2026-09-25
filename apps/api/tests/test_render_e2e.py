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
FRAMES = {
    "engine-tour": [0, 15, 45, 75, 105, 150, 239, 270, 330, 420],
    "gallery": [20, 90, 200, 290, 330, 420, 560, 640, 700, 880],
    # Gate A: one reviewed key moment per scene (global time = scene start + local time).
    "archimedes": [360, 990, 1440, 2190, 2880, 3600, 4245, 4830, 5850, 6480, 7335, 8184],
}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def session() -> AsyncIterator[RenderSession]:
    if not (WEB_DIST / "index.html").exists():
        pytest.fail("web app not built: run `make build-web`")
    with StaticSite() as site:
        async with async_playwright() as pw:
            s = await RenderSession(site.url, Viewport(960, 540)).start(pw)
            yield s
            await s.close()


async def _load(session: RenderSession, lesson: str) -> None:
    summary = await session.load(load_fixture_lesson(lesson))
    bad = [(sc["id"], sc["errors"]) for sc in summary["scenes"] if not sc["ok"]]
    assert not bad, bad


@pytest.mark.parametrize("lesson", sorted(FRAMES))
async def test_frames_are_pixel_deterministic_regardless_of_order(
    session: RenderSession, lesson: str
) -> None:
    await _load(session, lesson)
    frames = FRAMES[lesson]
    forward = {f: hashlib.sha256(await session.frame_png(f, FPS)).hexdigest() for f in frames}
    backward = {f: hashlib.sha256(await session.frame_png(f, FPS)).hexdigest() for f in reversed(frames)}
    assert forward == backward


def _diff_ratio(a: bytes, b: bytes) -> float:
    x = np.asarray(Image.open(io.BytesIO(a)).convert("RGB")).astype(np.int16)
    y = np.asarray(Image.open(io.BytesIO(b)).convert("RGB")).astype(np.int16)
    if x.shape != y.shape:
        return 1.0
    return float((np.abs(x - y).max(axis=2) > 8).mean())


@pytest.mark.parametrize("lesson", sorted(FRAMES))
async def test_golden_frames(session: RenderSession, lesson: str) -> None:
    await _load(session, lesson)
    update = os.environ.get("UPDATE_GOLDENS") == "1"
    if not update and not GOLDEN_DIR.exists():
        pytest.skip(
            f"no golden frames for platform '{sys.platform}' yet "
            "(generated in the pinned Docker env, Phase 6)"
        )
    failures = []
    for frame in FRAMES[lesson]:
        png = await session.frame_png(frame, FPS)
        golden = GOLDEN_DIR / f"{lesson}-{frame:04d}.png"
        if update:
            golden.parent.mkdir(parents=True, exist_ok=True)
            golden.write_bytes(png)
            continue
        if not golden.exists():
            failures.append(f"missing golden {golden.name}: run with UPDATE_GOLDENS=1")
            continue
        ratio = _diff_ratio(png, golden.read_bytes())
        if ratio >= 0.002:
            failures.append(f"{golden.name}: {ratio:.4%} of pixels differ")
    assert not failures, failures
