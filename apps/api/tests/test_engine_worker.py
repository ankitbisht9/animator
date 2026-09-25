"""Engine worker process supervision: handshake, crash/restart, timeouts, protocol mismatch."""

from __future__ import annotations

from pathlib import Path

import pytest

from animator.engine_client import (
    EngineRpcError,
    EngineUnavailable,
    EngineWorker,
    EngineWorkerPool,
    ProtocolMismatch,
    WorkerCrashed,
    WorkerTimeout,
)
from animator.engine_client import worker as worker_mod
from animator_contracts import PROTOCOL_VERSION

from .conftest import make_worker


async def test_handshake_reports_versions(worker: EngineWorker) -> None:
    assert worker.version_info is not None
    assert worker.version_info["protocolVersion"] == PROTOCOL_VERSION


async def test_health_ping(worker: EngineWorker) -> None:
    res = await worker.call("health.ping")
    assert res["ok"] is True


async def test_unknown_method_is_rpc_error(worker: EngineWorker) -> None:
    with pytest.raises(EngineRpcError) as info:
        await worker.call("does.not.exist")
    assert info.value.code == -32601


async def test_crash_fails_in_flight_call(worker: EngineWorker) -> None:
    with pytest.raises(WorkerCrashed):
        await worker.call("debug.crash")
    assert not worker.alive


async def test_timeout_kills_worker(worker: EngineWorker) -> None:
    with pytest.raises(WorkerTimeout):
        await worker.call("debug.hang", timeout=0.5)
    assert not worker.alive


async def test_pool_restarts_after_crash_and_serves_next_call(pool: EngineWorkerPool) -> None:
    with pytest.raises(EngineUnavailable):
        # debug.crash kills every worker it lands on, so retries exhaust.
        await pool.call("debug.crash")
    assert pool.restarts >= 1
    res = await pool.call("version")
    assert res["protocolVersion"] == PROTOCOL_VERSION
    assert all(await pool.health())


async def test_pool_retries_on_a_fresh_worker(pool: EngineWorkerPool) -> None:
    victim = pool.workers[0]
    await victim.kill()
    res = await pool.call("health.ping")
    assert res["ok"] is True


async def test_restart_budget_is_enforced(worker_script: Path) -> None:
    p = EngineWorkerPool(lambda i: make_worker(worker_script, i, debug=True), size=1, max_restarts=1)
    await p.start()
    try:
        with pytest.raises(EngineUnavailable):
            await p.call("debug.crash")
        with pytest.raises(EngineUnavailable):
            await p.call("debug.crash")
    finally:
        await p.close()


async def test_protocol_mismatch_is_rejected(worker_script: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker_mod, "PROTOCOL_VERSION", "99.0.0")
    w = make_worker(worker_script)
    with pytest.raises(ProtocolMismatch):
        await w.start()
    assert not w.alive


async def test_missing_bundle_is_a_clear_error(tmp_path: Path) -> None:
    w = EngineWorker("node", tmp_path / "nope.cjs")
    with pytest.raises(FileNotFoundError, match="make setup"):
        await w.start()
