from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

# The platform's database is never the developer's in tests. The served app (`animator.api.app:app`,
# started by test_api_health) reads the real settings: without this it would migrate and write
# var/platform/platform.db. Set before any test module imports the app.
os.environ["PLATFORM_DATA_DIR"] = tempfile.mkdtemp(prefix="animator-platform-tests-")
os.environ["PLATFORM_DATABASE_URL"] = ""
# Nor is the saved-object warehouse (ADR-055): harvests, decisions and uses go to a temporary folder.
os.environ["ANIMATOR_OBJECTS_DIR"] = tempfile.mkdtemp(prefix="animator-objects-tests-")

import pytest

from animator import REPO_ROOT
from animator.engine_client import EngineWorker, EngineWorkerPool

WORKER_SCRIPT = REPO_ROOT / "packages" / "engine-worker" / "dist" / "worker.cjs"
NODE = shutil.which("node") or "node"


@pytest.fixture(scope="session")
def worker_script() -> Path:
    if not WORKER_SCRIPT.exists():
        pytest.fail("engine worker bundle missing: run `make build-worker` first")
    return WORKER_SCRIPT


def make_worker(script: Path, index: int = 0, *, debug: bool = False) -> EngineWorker:
    env = {"ENGINE_WORKER_DEBUG": "1"} if debug else None
    return EngineWorker(NODE, script, name=f"test-worker-{index}", extra_env=env)


@pytest.fixture
async def worker(worker_script: Path) -> AsyncIterator[EngineWorker]:
    w = make_worker(worker_script, debug=True)
    await w.start()
    yield w
    await w.close()


@pytest.fixture
async def pool(worker_script: Path) -> AsyncIterator[EngineWorkerPool]:
    p = EngineWorkerPool(
        lambda i: make_worker(worker_script, i, debug=True), size=2, call_timeout=10, max_restarts=3
    )
    await p.start()
    yield p
    await p.close()
