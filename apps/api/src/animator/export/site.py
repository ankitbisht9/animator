"""Serve the built web app (apps/web/dist) on a free local port, with SPA fallback."""

from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import TracebackType
from typing import Any

from animator import REPO_ROOT

WEB_DIST = REPO_ROOT / "apps" / "web" / "dist"

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
