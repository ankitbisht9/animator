"""A small pool of engine workers with health checks and restart-and-retry.

Every engine call is stateless and idempotent (the request carries the scene and
any previous SceneState), so retrying on a fresh worker after a crash is safe.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from .errors import EngineUnavailable, WorkerCrashed, WorkerTimeout
from .worker import EngineWorker

log = logging.getLogger("animator.engine_pool")

WorkerFactory = Callable[[int], EngineWorker]


class EngineWorkerPool:
    def __init__(
        self,
        factory: WorkerFactory,
        *,
        size: int = 2,
        call_timeout: float = 30.0,
        max_restarts: int = 5,
        retries: int = 1,
    ) -> None:
        self._factory = factory
        self.size = size
        self.call_timeout = call_timeout
        self.max_restarts = max_restarts
        self.retries = retries
        self.restarts = 0
        self._workers: list[EngineWorker] = []
        self._lock = asyncio.Lock()

    async def start(self) -> dict[str, Any]:
        self._workers = [self._factory(i) for i in range(self.size)]
        infos = await asyncio.gather(*(w.start() for w in self._workers))
        return infos[0]

    @property
    def workers(self) -> list[EngineWorker]:
        return list(self._workers)

    def _pick(self) -> int:
        alive = [i for i, w in enumerate(self._workers) if w.alive]
        candidates = alive or list(range(len(self._workers)))
        return min(candidates, key=lambda i: self._workers[i].in_flight)

    async def _replace(self, index: int, dead: EngineWorker) -> None:
        async with self._lock:
            if self._workers[index] is not dead:
                return  # another caller already replaced it
            if self.restarts >= self.max_restarts:
                raise EngineUnavailable(f"engine worker restart budget ({self.max_restarts}) exhausted")
            self.restarts += 1
            log.warning(
                "restarting engine worker %d (restart %d/%d)", index, self.restarts, self.max_restarts
            )
            await dead.kill()
            fresh = self._factory(index)
            await fresh.start()
            self._workers[index] = fresh

    async def call(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> Any:
        if not self._workers:
            raise EngineUnavailable("engine worker pool is not started")
        deadline = timeout if timeout is not None else self.call_timeout
        last: Exception | None = None
        for _attempt in range(self.retries + 1):
            index = self._pick()
            worker = self._workers[index]
            try:
                if not worker.alive:
                    await self._replace(index, worker)
                    worker = self._workers[index]
                return await worker.call(method, params, timeout=deadline)
            except (WorkerCrashed, WorkerTimeout) as exc:
                last = exc
                log.warning("engine call %s failed on worker %d: %s", method, index, exc)
                await self._replace(index, worker)
        raise EngineUnavailable(f"engine call {method} failed after {self.retries + 1} attempts: {last}")

    async def health(self) -> list[bool]:
        async def ping(w: EngineWorker) -> bool:
            try:
                res = await w.call("health.ping", {}, timeout=5)
                return bool(res.get("ok"))
            except Exception:
                return False

        return list(await asyncio.gather(*(ping(w) for w in self._workers)))

    async def close(self) -> None:
        await asyncio.gather(*(w.close() for w in self._workers), return_exceptions=True)
        self._workers = []
