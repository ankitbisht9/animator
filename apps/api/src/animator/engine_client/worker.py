"""One supervised engine worker process speaking NDJSON JSON-RPC 2.0 over stdio."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import os
from pathlib import Path
from typing import Any

from animator_contracts import PROTOCOL_VERSION

from .errors import EngineRpcError, ProtocolMismatch, WorkerCrashed, WorkerTimeout

log = logging.getLogger("animator.engine_worker")

# Compiled scenes can be several MB on one line.
_STREAM_LIMIT = 64 * 1024 * 1024


def protocol_major(version: str) -> int:
    return int(version.split(".", 1)[0])


class EngineWorker:
    def __init__(
        self,
        node: str,
        script: Path,
        *,
        name: str = "engine-worker",
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.node = node
        self.script = script
        self.name = name
        self._extra_env = extra_env or {}
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._ids = itertools.count(1)
        self._reader: asyncio.Task[None] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self.version_info: dict[str, Any] | None = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def in_flight(self) -> int:
        return len(self._pending)

    async def start(self, *, handshake_timeout: float = 15.0) -> dict[str, Any]:
        if not self.script.exists():
            raise FileNotFoundError(
                f"engine worker bundle not found at {self.script}; run `make setup` (or `pnpm build:worker`)"
            )
        env = {**os.environ, **self._extra_env}
        # The worker needs no secrets; never hand it the OpenAI key.
        for secret in ("OPENAI_API_KEY", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
            env.pop(secret, None)
        self._proc = await asyncio.create_subprocess_exec(
            self.node,
            str(self.script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=_STREAM_LIMIT,
        )
        self._reader = asyncio.create_task(self._read_stdout(), name=f"{self.name}-stdout")
        self._stderr = asyncio.create_task(self._read_stderr(), name=f"{self.name}-stderr")
        info: dict[str, Any] = await self.call("version", {}, timeout=handshake_timeout)
        remote = str(info.get("protocolVersion", "0.0.0"))
        if protocol_major(remote) != protocol_major(PROTOCOL_VERSION):
            await self.close()
            raise ProtocolMismatch(f"worker speaks protocol {remote}, contracts expect {PROTOCOL_VERSION}")
        self.version_info = info
        return info

    async def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0) -> Any:
        proc = self._proc
        if proc is None or proc.returncode is not None or proc.stdin is None:
            raise WorkerCrashed(f"{self.name} is not running")
        req_id = next(self._ids)
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        line = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        try:
            async with self._write_lock:
                proc.stdin.write(line.encode("utf-8") + b"\n")
                await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            self._pending.pop(req_id, None)
            raise WorkerCrashed(f"{self.name} pipe closed: {exc}") from exc
        try:
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError as exc:
            # A stuck worker cannot be interrupted mid-compute; kill it so the pool restarts it.
            await self.kill()
            raise WorkerTimeout(f"{self.name}: {method} exceeded {timeout}s") from exc
        finally:
            self._pending.pop(req_id, None)

    async def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stdout = self._proc.stdout
        try:
            while True:
                raw = await stdout.readline()
                if not raw:
                    break
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    log.error("%s: unparseable output line (%d bytes)", self.name, len(raw))
                    continue
                req_id = msg.get("id") if isinstance(msg, dict) else None
                fut = self._pending.get(req_id) if isinstance(req_id, int) else None
                if fut is None or fut.done():
                    continue
                if "error" in msg:
                    err = msg["error"]
                    fut.set_exception(
                        EngineRpcError(err.get("code", -1), err.get("message", ""), err.get("data"))
                    )
                else:
                    fut.set_result(msg.get("result"))
        finally:
            code = await self._proc.wait()
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(WorkerCrashed(f"{self.name} exited with code {code}"))

    async def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        while True:
            raw = await self._proc.stderr.readline()
            if not raw:
                return
            log.warning("%s stderr: %s", self.name, raw.decode("utf-8", "replace").rstrip())

    async def kill(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._proc.kill()
            await self._proc.wait()

    async def close(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            if proc.stdin is not None:
                with contextlib.suppress(Exception):
                    proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except TimeoutError:
                await self.kill()
        for task in (self._reader, self._stderr):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await asyncio.wait_for(task, 5)
