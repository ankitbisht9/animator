"""Contract tests between Python and the engine worker.

- Live worker answers equal the committed contracts (version manifest, LLM schema, catalog).
- Golden request/response fixtures in fixtures/rpc (regenerate with UPDATE_GOLDENS=1).
- Typed client round trips, continuity across two scenes, invalid params.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from animator import REPO_ROOT
from animator.engine_client import EngineClient, EngineRpcError, EngineWorkerPool

from .conftest import make_worker

CONTRACTS = REPO_ROOT / "contracts"
RPC = REPO_ROOT / "fixtures" / "rpc"


@pytest.fixture
async def client(worker_script: Path) -> AsyncIterator[EngineClient]:
    pool = EngineWorkerPool(lambda i: make_worker(worker_script, i), size=1, call_timeout=60)
    await pool.start()
    yield EngineClient(pool)
    await pool.close()


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


async def test_version_matches_contract(client: EngineClient) -> None:
    v = await client.version()
    assert v.model_dump() == _json(CONTRACTS / "engine.json")


async def test_llm_schema_matches_contract(client: EngineClient) -> None:
    assert await client.schema("llm") == _json(CONTRACTS / "dsl.llm.schema.json")


async def test_catalog_matches_contract(client: EngineClient) -> None:
    catalog = await client.catalog()
    assert catalog.model_dump(mode="json", by_alias=True, exclude_none=True) == _json(
        CONTRACTS / "catalog.json"
    )
    assert {c.type for c in catalog.components} >= {"container", "block", "vector_arrow", "equation"}


@pytest.mark.parametrize("name", sorted(p.stem for p in RPC.glob("*.json")))
async def test_golden_rpc(client: EngineClient, name: str) -> None:
    path = RPC / f"{name}.json"
    fixture = _json(path)
    result = await client.pool.call(fixture["method"], fixture["params"], timeout=60)
    for key in fixture.get("omit", []):
        result.pop(key, None)
    result = json.loads(json.dumps(result))
    if os.environ.get("UPDATE_GOLDENS") == "1":
        fixture["result"] = result
        path.write_text(
            json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        return
    assert "result" in fixture, f"{path.name} has no recorded result: run with UPDATE_GOLDENS=1"
    assert result == fixture["result"]


async def test_invalid_params_are_rejected(client: EngineClient) -> None:
    with pytest.raises(EngineRpcError) as info:
        await client.pool.call("scene.sample", {"scene": {}, "times": []})
    assert info.value.code == -32602


async def test_continuity_across_scenes(client: EngineClient) -> None:
    first = {
        "dsl_version": "1.0",
        "id": "s1",
        "title": "One",
        "duration": 3,
        "narration": {"beats": [{"id": "b", "text": "A block."}]},
        "objects": [{"id": "blk", "type": "block", "place": {"slot": "stage.left"}}],
        "timeline": [{"action": "move", "target": "blk", "to": {"slot": "stage.right"}}],
    }
    second = {
        "dsl_version": "1.0",
        "id": "s2",
        "title": "Two",
        "duration": 3,
        "narration": {"beats": [{"id": "b", "text": "Same block."}]},
        "timeline": [{"action": "highlight", "target": "blk"}],
    }
    a = await client.compile(first)
    assert a.ok and a.endState is not None
    assert a.stateSummary and "blk" in a.stateSummary
    b = await client.compile(second, prev_state=a.endState)
    assert b.ok, b.issues
    assert b.endState is not None
    assert b.endState.objects["blk"].x == pytest.approx(a.endState.objects["blk"].x)
    # Redeclaring the carried id as another type is an identity error.
    bad = {**second, "objects": [{"id": "blk", "type": "ball", "place": {"slot": "stage.center"}}]}
    v = await client.validate(bad, prev_state=a.endState)
    assert not v.ok
    assert "IDENTITY_TYPE_CHANGE" in {i.code for i in v.issues}


async def test_state_at_and_sample(client: EngineClient) -> None:
    scene = _json(RPC / "compile-buoyancy.json")["params"]["scene"]
    mid = await client.state_at(scene, 2.5)
    end = await client.state_at(scene, 6)
    assert mid.state and end.state
    assert mid.state.objects["tank"].params["level"] != end.state.objects["tank"].params["level"]
    frames = await client.sample(scene, [0, 6])
    assert frames.ok and len(frames.frames) == 2
