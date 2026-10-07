"""Serving the built web app from the API (`PLATFORM_SERVE_WEB=true`): one origin in production, so
the cookies stay first-party and no cross-origin requests are needed. Hashed assets are cached for a
year; every other path that is not the API gets the app's page, with a strict Content Security Policy."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "media-src 'self' blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "worker-src 'self' blob:",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
    ]
)


class _Assets(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["cache-control"] = "public, max-age=31536000, immutable"
        return response


def serve(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.exists():
        raise RuntimeError(f"PLATFORM_SERVE_WEB is on but {index} is missing: run `pnpm build:web`")
    if (dist / "assets").is_dir():
        app.mount("/assets", _Assets(directory=dist / "assets"), name="assets")

    root = dist.resolve()
    files = {p.relative_to(root).as_posix() for p in root.iterdir() if p.is_file()}

    @app.get("/{path:path}", include_in_schema=False)
    async def page(path: str) -> FileResponse:
        if path.startswith("api/") or path == "api":
            raise HTTPException(404, "Not Found")
        if path in files:  # favicon and the like, at the top of the build
            return FileResponse(root / path)
        return FileResponse(index, headers={"content-security-policy": CSP, "cache-control": "no-cache"})
