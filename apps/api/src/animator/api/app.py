"""FastAPI application.

Phase 0 surface: a health endpoint that proves the engine worker pool is up and
speaking the expected protocol. Generation endpoints arrive in Phase 7.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from animator.config import get_settings
from animator.engine_client import EngineWorkerPool, pool_from_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    pool = pool_from_settings(settings)
    app.state.engine_version = await pool.start()
    app.state.engine = pool
    try:
        yield
    finally:
        await pool.close()


app = FastAPI(title="Animator API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    pool: EngineWorkerPool = app.state.engine
    workers = await pool.health()
    return {
        "ok": all(workers),
        "engine": app.state.engine_version,
        "workers": workers,
        "restarts": pool.restarts,
    }
