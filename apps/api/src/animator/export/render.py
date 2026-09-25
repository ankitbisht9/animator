"""Drive the /render page in headless Chromium: load a lesson, paint frames, capture PNGs.

The page runs the same `evaluate` + `<SceneView>` as the browser player (P2).
Each captured frame is a fresh mount, so pixels never depend on paint history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import Browser, Page, Playwright

from animator import REPO_ROOT

CHROMIUM_ARGS = [
    "--disable-gpu",
    "--force-color-profile=srgb",
    "--font-render-hinting=none",
    "--disable-lcd-text",
    "--hide-scrollbars",
]

FIXTURES = REPO_ROOT / "fixtures" / "scenes"


def load_fixture_lesson(slug: str) -> dict[str, Any]:
    """{title, scenes} from fixtures/scenes/<slug>/ (lesson.json + NN-*.json in name order)."""
    folder = FIXTURES / slug
    if not folder.is_dir():
        raise FileNotFoundError(f"no fixture lesson '{slug}' in {FIXTURES}")
    meta_path = folder / "lesson.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"title": slug}
    scenes = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(folder.glob("*.json"))
        if p.name != "lesson.json"
    ]
    return {"title": meta.get("title", slug), "scenes": scenes}


@dataclass
class Viewport:
    width: int = 1920
    height: int = 1080


class RenderSession:
    """One Chromium page on the /render route."""

    def __init__(self, site_url: str, viewport: Viewport | None = None) -> None:
        self.site_url = site_url
        self.viewport = viewport or Viewport()
        self._browser: Browser | None = None
        self._page: Page | None = None

    async def start(self, pw: Playwright) -> RenderSession:
        self._browser = await pw.chromium.launch(args=CHROMIUM_ARGS)
        self._page = await self._browser.new_page(
            viewport={"width": self.viewport.width, "height": self.viewport.height}, device_scale_factor=1
        )
        await self._page.goto(f"{self.site_url}/render")
        await self._page.wait_for_function("window.__ready === true")
        return self

    @property
    def page(self) -> Page:
        assert self._page is not None, "session not started"
        return self._page

    async def load(self, lesson: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = await self.page.evaluate("p => window.__loadLesson(p)", lesson)
        await self.page.wait_for_function("window.__ready === true")
        return summary

    async def frame_png(self, frame: int, fps: int) -> bytes:
        await self.page.evaluate("([f, fps]) => window.__renderFrame(f, fps)", [frame, fps])
        return await self.page.screenshot(type="png")

    async def at_png(self, scene_index: int, local_t: float) -> bytes:
        await self.page.evaluate("([i, t]) => window.__renderAt(i, t)", [scene_index, local_t])
        return await self.page.screenshot(type="png")

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()


def keyframe_times(scene: dict[str, Any]) -> list[tuple[str, float]]:
    """Scene start, each beat end, scene end (what the visual critic inspects)."""
    duration = float(scene["duration"])
    out: list[tuple[str, float]] = [("start", min(0.3, duration))]
    for beat in scene.get("beats", []):
        end = min(float(beat["end"]), duration) - 0.05
        out.append((f"beat-{beat['id']}", max(0.0, end)))
    out.append(("end", duration))
    seen: set[float] = set()
    unique: list[tuple[str, float]] = []
    for name, t in out:
        key = round(t, 3)
        if key not in seen:
            seen.add(key)
            unique.append((name, t))
    return unique


def write_png(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
