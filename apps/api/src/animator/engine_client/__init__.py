"""Client for the TypeScript engine worker (the single implementation of all deterministic logic)."""

from __future__ import annotations

from animator.config import Settings

from .errors import (
    EngineError,
    EngineRpcError,
    EngineUnavailable,
    ProtocolMismatch,
    WorkerCrashed,
    WorkerTimeout,
)
from .pool import EngineWorkerPool
from .worker import EngineWorker


def pool_from_settings(settings: Settings, *, extra_env: dict[str, str] | None = None) -> EngineWorkerPool:
    script = settings.resolve(settings.engine_worker_script)

    def factory(index: int) -> EngineWorker:
        return EngineWorker(
            settings.engine_worker_node, script, name=f"engine-worker-{index}", extra_env=extra_env
        )

    return EngineWorkerPool(
        factory,
        size=settings.engine_worker_pool_size,
        call_timeout=settings.engine_worker_call_timeout_s,
        max_restarts=settings.engine_worker_max_restarts,
    )


__all__ = [
    "EngineError",
    "EngineRpcError",
    "EngineUnavailable",
    "EngineWorker",
    "EngineWorkerPool",
    "ProtocolMismatch",
    "WorkerCrashed",
    "WorkerTimeout",
    "pool_from_settings",
]
