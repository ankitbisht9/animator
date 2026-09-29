"""Serve the built web app (apps/web/dist) on a free local port, with SPA fallback."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Callable
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import Any

from animator import REPO_ROOT

log = logging.getLogger("animator.export")

WEB_DIST = REPO_ROOT / "apps" / "web" / "dist"
# What the built web app is made from: the engine and the player draw every exported frame.
WEB_SOURCES = [
    REPO_ROOT / "packages" / "engine" / "src",
    REPO_ROOT / "packages" / "components" / "src",
    REPO_ROOT / "apps" / "web" / "src",
    REPO_ROOT / "apps" / "web" / "index.html",
    REPO_ROOT / "apps" / "web" / "vite.config.ts",
]
_build_lock = asyncio.Lock()


def _newest(paths: list[Path]) -> float:
    newest = 0.0
    for p in paths:
        if p.is_file():
            newest = max(newest, p.stat().st_mtime)
        elif p.is_dir():
            for root, _, files in os.walk(p):
                for name in files:
                    newest = max(newest, (Path(root) / name).stat().st_mtime)
    return newest


def web_build_stale(dist: Path = WEB_DIST, sources: list[Path] = WEB_SOURCES) -> bool:
    """Whether the built web app is missing or older than its source (so an exported frame could be
    drawn by an older engine than the one that validated the scene)."""
    index = dist / "index.html"
    return not index.exists() or _newest(sources) > index.stat().st_mtime


async def ensure_web_build(dist: Path = WEB_DIST, say: Callable[[str], None] = log.info) -> None:
    """Rebuild the web app (a few seconds) when its source changed since the last build."""
    if dist != WEB_DIST:
        return  # a prebuilt app handed in on purpose (tests, a container image)
    async with _build_lock:
        if not await asyncio.to_thread(web_build_stale, dist):
            return
        say("The web app build is older than its source: rebuilding it (a few seconds).")
        proc = await asyncio.create_subprocess_shell(
            "pnpm run build:web",
            cwd=REPO_ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            tail = out.decode(errors="replace").strip().splitlines()[-5:]
            raise RuntimeError("building the web app failed: " + " | ".join(tail))


# Explicit types: on Windows the mimetypes registry can map .js to text/plain,
# which browsers refuse for module scripts.
_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".png": "image/png",
}


class _Handler(SimpleHTTPRequestHandler):
    # The base class declares this as an instance attribute, so no ClassVar here.
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, **_TYPES}  # noqa: RUF012

    def send_head(self) -> Any:
        path = Path(self.translate_path(self.path))
        if not path.exists():
            # Client-side routes (/render, /lesson/x) fall back to the app shell.
            self.path = "/index.html"
        return super().send_head()

    def log_message(self, format: str, *args: Any) -> None:
        pass


class StaticSite:
    """Context manager: `with StaticSite() as site: site.url`."""

    def __init__(self, root: Path = WEB_DIST) -> None:
        if not (root / "index.html").exists():
            raise FileNotFoundError(f"web app not built at {root}; run `make build-web`")
        self.root = root
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address[:2]
        return f"http://{host!s}:{port}"

    def __enter__(self) -> StaticSite:
        handler = partial(_Handler, directory=str(self.root))
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._server.serve_forever, name="static-site", daemon=True)
        self._thread.start()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
