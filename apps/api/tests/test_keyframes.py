"""Keyframes without a browser (ADR-068): the visual critic's pictures drawn on the server by the
player's own scene components and resvg (`packages/engine-worker/dist/frames.cjs`), for where no
browser is installed. A picture that cannot be drawn never fails a scene."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import pytest

from animator import REPO_ROOT
from animator.engine_client import EngineWorkerPool
from animator.export.jobs import BrowserKeyframes, KeyframeError, SvgKeyframes, keyframes_for
from animator.llm import LLMRequest
from animator.llm.types import ImagePart
from tests.conftest import NODE
from tests.test_pipeline import settings
from tests.test_teaching import Teaching, corner_scene, run_teaching

FRAMES = REPO_ROOT / "packages" / "engine-worker" / "dist" / "frames.cjs"


@pytest.fixture
def frames_script() -> Path:
    if not FRAMES.is_file():
        pytest.fail("frames bundle missing: run `pnpm run build:worker` first")
    return FRAMES


def size_of(png: bytes) -> tuple[int, int]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", png[16:24])
    return w, h


def images_of(req: LLMRequest) -> list[bytes]:
    return [p.png for m in req.messages for p in m.parts if isinstance(p, ImagePart)]


async def test_a_scene_is_drawn_without_a_browser(frames_script: Path) -> None:
    frames = SvgKeyframes(frames_script, NODE)
    pngs = await frames.keyframes([corner_scene()], 0, [1.0, 5.9], finish="rich")
    assert [size_of(p) for p in pngs] == [(1280, 720), (1280, 720)]
    assert pngs[0] != pngs[1]  # the dot has appeared by the second
    # The scenes before it are compiled first, as the player does.
    again = await frames.keyframes([corner_scene(), corner_scene()], 1, [1.0])
    assert size_of(again[0]) == (1280, 720)


async def test_a_scene_that_cannot_be_drawn_says_why(frames_script: Path) -> None:
    frames = SvgKeyframes(frames_script, NODE)
    with pytest.raises(KeyframeError, match="not a valid scene"):
        await frames.keyframes([{"nonsense": True}], 0, [1.0])


async def test_a_drawing_that_hangs_is_stopped(tmp_path: Path) -> None:
    hang = tmp_path / "hang.cjs"
    hang.write_text("setTimeout(() => {}, 60000);", encoding="utf-8")
    frames = SvgKeyframes(hang, NODE, timeout_s=1.0)
    with pytest.raises(KeyframeError, match="no keyframes within"):
        await frames.keyframes([corner_scene()], 0, [1.0])


def test_the_settings_choose_how_keyframes_are_drawn(tmp_path: Path) -> None:
    svg = keyframes_for(settings(animator_keyframes="svg", animator_frames_script=FRAMES))
    assert isinstance(svg, SvgKeyframes) and svg.script == FRAMES
    missing = settings(animator_keyframes="svg", animator_frames_script=tmp_path / "none.cjs")
    assert keyframes_for(missing) is None  # nothing to draw with: the critic reads scenes as text
    browser = keyframes_for(settings())
    assert browser is None or isinstance(browser, BrowserKeyframes)  # needs the built web app


async def test_the_critic_sees_the_scenes_drawn_without_a_browser(
    pool: EngineWorkerPool, frames_script: Path
) -> None:
    script = Teaching()
    script.scene = lambda i, a, r: corner_scene()
    gen, _ = await run_teaching(pool, script, SvgKeyframes(frames_script, NODE))
    assert gen.status == "complete", gen.error
    critic = next(r for r in script.calls if r.label == "critic scene 1")
    images = images_of(critic)
    assert critic.stage == "visual_critic" and images
    assert all(size_of(p) == (1280, 720) for p in images)


class Broken:
    """A keyframe source that always fails."""

    def __init__(self) -> None:
        self.calls = 0

    async def keyframes(
        self, scenes: list[dict[str, Any]], index: int, times: list[float], finish: str = ""
    ) -> Any:
        self.calls += 1
        raise KeyframeError("the renderer is broken")


async def test_pictures_that_cannot_be_drawn_never_fail_a_scene(pool: EngineWorkerPool) -> None:
    script = Teaching()
    script.scene = lambda i, a, r: corner_scene()
    broken = Broken()
    gen, _ = await run_teaching(pool, script, broken)
    assert gen.status == "complete", gen.error
    assert broken.calls >= 1 and all(s.status == "ready" for s in gen.scenes)
    critic = next(r for r in script.calls if r.label == "critic scene 1")
    assert critic.stage == "critic" and not images_of(critic)  # read as text instead
