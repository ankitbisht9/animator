"""Drive the /render page in headless Chromium: load a lesson, paint frames, capture PNGs.

The page runs the same `evaluate` + `<SceneView>` as the browser player (P2).
Each captured frame is a fresh mount, so pixels never depend on paint history.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from playwright.async_api import Browser, CDPSession, Page, Playwright, async_playwright

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
        self._cdp: CDPSession | None = None

    async def start(self, pw: Playwright) -> RenderSession:
        self._browser = await pw.chromium.launch(args=CHROMIUM_ARGS)
        self._page = await self._browser.new_page(
            viewport={"width": self.viewport.width, "height": self.viewport.height}, device_scale_factor=1
        )
        await self._page.goto(f"{self.site_url}/render")
        await self._page.wait_for_function("window.__ready === true")
        self._cdp = await self._page.context.new_cdp_session(self._page)
        return self

    @property
    def page(self) -> Page:
        assert self._page is not None, "session not started"
        return self._page

    async def load(self, lesson: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = await self.page.evaluate("p => window.__loadLesson(p)", lesson)
        await self.page.wait_for_function("window.__ready === true")
        return summary

    async def capture(self) -> bytes:
        """The painted viewport as PNG. Chromium's fast PNG encoder gives the same pixels as
        `page.screenshot` (and identical bytes run to run) in about 40% of the time."""
        assert self._cdp is not None, "session not started"
        shot = await self._cdp.send("Page.captureScreenshot", {"format": "png", "optimizeForSpeed": True})
        return base64.b64decode(shot["data"])

    async def frame_png(self, frame: int, fps: int) -> bytes:
        await self.page.evaluate("([f, fps]) => window.__renderFrame(f, fps)", [frame, fps])
        return await self.capture()

    async def at_png(self, scene_index: int, local_t: float) -> bytes:
        await self.page.evaluate("([i, t]) => window.__renderAt(i, t)", [scene_index, local_t])
        return await self.capture()

    @property
    def browser_version(self) -> str:
        return self._browser.version if self._browser is not None else "unknown"

    async def close(self) -> None:
        if self._browser is not None:
            # A crashed browser is already gone; closing it again must not mask the real error.
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None


class BrowserPool:
    """N independent Chromium processes on /render, each holding the same loaded lesson.

    Frames are pure functions of time, so any session can paint any frame (ADR-006);
    separate browser processes give real parallelism.
    """

    def __init__(self, site_url: str, viewport: Viewport, size: int, build_id: str = "unknown") -> None:
        self.site_url = site_url
        self.viewport = viewport
        self.size = size
        self.build_id = build_id
        self._pw: Playwright | None = None
        self._sessions: list[RenderSession] = []
        self._lesson: dict[str, Any] | None = None

    @property
    def renderers(self) -> list[RenderSession]:
        return self._sessions

    @property
    def browser_version(self) -> str:
        return self._sessions[0].browser_version if self._sessions else "unknown"

    async def _open(self) -> None:
        assert self._pw is not None
        pw = self._pw
        self._sessions = list(
            await asyncio.gather(
                *(RenderSession(self.site_url, self.viewport).start(pw) for _ in range(self.size))
            )
        )

    async def _close_sessions(self) -> None:
        await asyncio.gather(*(s.close() for s in self._sessions))
        self._sessions = []

    async def __aenter__(self) -> Self:
        self._pw = await async_playwright().start()
        try:
            await self._open()
        except BaseException:
            await self._close_sessions()
            await self._pw.stop()
            raise
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self._close_sessions()
        if self._pw is not None:
            await self._pw.stop()

    async def load(self, lesson: dict[str, Any]) -> dict[str, Any]:
        """Load the lesson into every session; they must all agree on the summary."""
        self._lesson = lesson
        summaries = await asyncio.gather(*(s.load(lesson) for s in self._sessions))
        first = summaries[0]
        if any(x != first for x in summaries[1:]):
            raise RuntimeError("render sessions disagree on the loaded lesson")
        return first

    async def restart(self) -> None:
        """Replace every browser (e.g. after a crash) and reload the current lesson."""
        await self._close_sessions()
        await self._open()
        if self._lesson is not None:
            await self.load(self._lesson)


def build_id(dist: Path) -> str:
    """Identity of a web build: index.html names every hashed asset bundle."""
    return hashlib.sha256((dist / "index.html").read_bytes()).hexdigest()[:16]


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
