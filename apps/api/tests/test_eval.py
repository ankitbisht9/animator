"""Eval metrics from a real (scripted-LLM) generation."""

from __future__ import annotations

import copy
from typing import Any

from animator.engine_client import EngineWorkerPool
from animator.eval import generation_metrics, summarize
from animator.llm import LLMRequest
from tests.test_pipeline import SCENES, Script, broken, run


async def test_metrics_reflect_corrections_and_cost(pool: EngineWorkerPool) -> None:
    def scene(i: int, attempt: int, req: LLMRequest) -> Any:
        return broken(SCENES[i]) if i == 0 and attempt == 1 else copy.deepcopy(SCENES[i])

    gen, _ = await run(pool, Script(scene))
    m = generation_metrics(gen)
    assert m["scenes"] == 4 and m["status"] == "complete"
    assert m["first_pass_valid"] == 0.75  # scene 1 needed one correction
    assert m["ready_within_3_corrections"] == 1.0 and m["degraded_rate"] == 0.0
    assert m["corrections_per_scene"] == 0.25
    assert m["video_s"] == sum(s["duration"] for s in SCENES[:4])
    assert m["cost_per_video_min"] is not None and m["cost_per_video_min"] > 0

    total = summarize([{**m, "topic_id": "a"}, {**m, "topic_id": "b"}])
    assert total["completed"] == 2 and total["first_pass_valid"] == 0.75
