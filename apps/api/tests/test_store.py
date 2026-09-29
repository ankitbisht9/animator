"""Persistence on Windows: files briefly locked by another process (a virus scan, the search
indexer, a concurrent reader) must not fail a lesson."""

from __future__ import annotations

import asyncio
import copy
import os
import time
from pathlib import Path
from typing import Any

import pytest

from animator.engine_client import EngineClient, EngineWorkerPool
from animator.export.site import web_build_stale
from animator.jobs import store as store_module
from animator.jobs.runner import GenerationRunner
from animator.jobs.store import FileStore, MemoryStore
from animator.llm import FakeLLM
from animator.pipeline.generator import Pipeline
from animator.pipeline.models import Generation, GenerationRequest
from tests.test_pipeline import SCENES, Script, settings


async def test_a_briefly_locked_file_is_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_module, "_LOCK_WAIT_S", 0.001)
    real_replace = Path.replace
    denied = {"n": 2}

    def replace(self: Path, target: Any) -> Path:
        if denied["n"] > 0:
            denied["n"] -= 1
            raise PermissionError(5, "Access is denied", str(target))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", replace)
    store = FileStore(tmp_path)
    gen = Generation(request=GenerationRequest(topic="Archimedes' principle"))
    await store.save(gen)
    got = await store.get(gen.id)
    assert got is not None and got.id == gen.id and denied["n"] == 0


async def test_a_file_that_stays_locked_still_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_module, "_LOCK_WAIT_S", 0.001)

    def replace(self: Path, target: Any) -> Path:
        raise PermissionError(5, "Access is denied", str(target))

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(PermissionError):
        await FileStore(tmp_path).save(Generation(request=GenerationRequest(topic="Archimedes' principle")))


class FlakyStore(MemoryStore):
    """Fails the first few saves the way a locked file does."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    async def save(self, gen: Generation) -> None:
        if self.failures > 0:
            self.failures -= 1
            raise PermissionError(5, "Access is denied")
        await super().save(gen)


async def test_a_failed_save_does_not_fail_the_lesson(pool: EngineWorkerPool) -> None:
    store = FlakyStore(failures=3)
    s = settings()
    script = Script(lambda i, a, r: copy.deepcopy(SCENES[i]))
    runner = GenerationRunner(store, Pipeline(EngineClient(pool), FakeLLM(script), s), s)
    gen = await runner.start(GenerationRequest(topic="Archimedes' principle"))
    await asyncio.wait_for(runner._tasks[gen.id], timeout=120)
    done = await runner.get(gen.id)
    assert done is not None and done.status == "complete", done and done.error
    saved = await store.get(gen.id)
    assert saved is not None and saved.status == "complete"  # a later save wrote everything


def test_the_export_notices_a_web_build_older_than_its_source(tmp_path: Path) -> None:
    dist, src = tmp_path / "dist", tmp_path / "src"
    dist.mkdir()
    src.mkdir()
    assert web_build_stale(dist, [src])  # never built
    (src / "engine.ts").write_text("x")
    (dist / "index.html").write_text("<html>")
    old, new = time.time() - 100, time.time()
    os.utime(src / "engine.ts", (old, old))
    os.utime(dist / "index.html", (new, new))
    assert not web_build_stale(dist, [src])
    os.utime(src / "engine.ts", (new + 10, new + 10))  # the engine changed after the build
    assert web_build_stale(dist, [src])
