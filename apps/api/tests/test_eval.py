"""Eval metrics from a real (scripted-LLM) generation."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from animator.engine_client import EngineWorkerPool
from animator.eval import generation_metrics, summarize
from animator.eval.__main__ import parse_sets
from animator.llm import LLMRequest
from tests.test_pipeline import SCENES, Script, broken, run


def test_setting_overrides_take_their_settings_types() -> None:
    """`--set` values become what the setting holds: "false" switches a switch off (a string would
    pass as true), numbers are numbers; an unknown setting or a value it cannot take stops the run."""
    assert parse_sets(
        ["ANIMATOR_LESSON_FLOW=false", "ANIMATOR_TEACHING_CRITIC_ROUNDS=2", "ANIMATOR_MODEL_TEACHER=m-x"]
    ) == {
        "animator_lesson_flow": False,
        "animator_teaching_critic_rounds": 2,
        "animator_model_teacher": "m-x",
    }
    with pytest.raises(SystemExit, match="no setting named ANIMATOR_NOPE"):
        parse_sets(["ANIMATOR_NOPE=1"])
    with pytest.raises(SystemExit, match="ANIMATOR_KEYFRAMES"):
        parse_sets(["ANIMATOR_KEYFRAMES=pdf"])
    with pytest.raises(SystemExit, match="KEY=VALUE"):
        parse_sets(["ANIMATOR_LESSON_FLOW"])


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
