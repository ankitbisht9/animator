"""LLM layer, offline: the real OpenAI SDK against a mock HTTP transport (spec Section 19:
refusal, truncation, malformed JSON, 429/5xx retry, budget hard stop, plus model fallbacks)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from animator.config import Settings
from animator.llm import (
    Budget,
    BudgetExceeded,
    CallContext,
    CallRecord,
    FakeLLM,
    LLMAuthError,
    LLMBadOutput,
    LLMError,
    LLMIncomplete,
    LLMRefusal,
    LLMRequest,
    LLMUnavailable,
    Message,
    ModelCatalog,
    PriceTable,
    RecordingLLM,
    ReplayLLM,
    Usage,
)
from animator.llm.openai_client import OpenAILLM

ROOT = Path(__file__).resolve().parents[3]
PRICES = PriceTable.load(ROOT / "config" / "pricing.json")
MODELS = ModelCatalog.load(ROOT / "config" / "models.json")
SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "integer"}},
    "required": ["a"],
    "additionalProperties": False,
}


def request(models: list[str] | None = None, effort: str = "high") -> LLMRequest:
    return LLMRequest(
        stage="educational_planner",
        prompt_version="t1",
        instructions="rules",
        messages=[Message.user("topic")],
        schema_name="Plan",
        schema=SCHEMA,
        models=models or ["gpt-6-sol"],
        effort=effort,
        max_output_tokens=40_000,
        generation_id="g1",
        label="plan",
    )


def response(
    text: str = '{"a": 1}', model: str = "gpt-6-sol", status: str = "completed", **extra: Any
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "output_text", "text": text, "annotations": []}]
    if "refusal" in extra:
        content = [{"type": "refusal", "refusal": extra.pop("refusal")}]
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "model": model,
        "status": status,
        "output": [
            {"type": "message", "id": "m1", "role": "assistant", "status": "completed", "content": content}
        ],
        "usage": {
            "input_tokens": 1000,
            "input_tokens_details": {"cached_tokens": 200},
            "output_tokens": 300,
            "output_tokens_details": {"reasoning_tokens": 100},
            "total_tokens": 1300,
        },
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        **extra,
    }


def error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"message": message, "type": "x", "code": code}}, headers=headers
    )


class Script:
    """Serves responses in order and keeps the request bodies."""

    def __init__(
        self, *items: httpx.Response | dict[str, Any] | Callable[[dict[str, Any]], httpx.Response]
    ) -> None:
        self.items = list(items)
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        self.bodies.append(body)
        item = self.items.pop(0)
        if callable(item):
            return item(body)
        return item if isinstance(item, httpx.Response) else httpx.Response(200, json=item)


def client(script: Script, *, key: str | None = "sk-test", retries: int = 2) -> tuple[OpenAILLM, list[float]]:
    settings = Settings(_env_file=None, openai_api_key=key, openai_max_retries=retries)
    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)

    http = httpx.AsyncClient(transport=httpx.MockTransport(script))
    return OpenAILLM(settings, PRICES, MODELS, http_client=http, sleep=sleep), slept


def ctx() -> tuple[CallContext, list[CallRecord], Budget]:
    records: list[CallRecord] = []
    budget = Budget(max_usd=5.0, max_tokens=1_000_000)
    return CallContext(budget=budget, on_record=records.append), records, budget


# ---------------------------------------------------------------------- happy path


async def test_structured_call_returns_data_usage_and_cost() -> None:
    script = Script(response())
    llm, _ = client(script)
    c, records, budget = ctx()
    res = await llm.generate(request(), c)
    assert res.data == {"a": 1}
    assert res.usage == Usage(1000, 200, 300, 100)
    # 800 uncached x $2 + 200 cached x $0.2 + 300 output x $10, per million
    assert res.cost_usd == pytest.approx((800 * 2 + 200 * 0.2 + 300 * 10) / 1e6)
    body = script.bodies[0]
    assert body["text"]["format"] == {"type": "json_schema", "name": "Plan", "schema": SCHEMA, "strict": True}
    assert body["reasoning"] == {"effort": "high"}
    assert body["store"] is False
    assert records[0].status == "ok" and records[0].stage == "educational_planner"
    assert budget.spent_tokens == 1300
    assert "sk-test" not in json.dumps([r.to_json() for r in records])


# ---------------------------------------------------------------------- model fallbacks


async def test_falls_back_when_a_model_is_not_available_and_remembers_it() -> None:
    script = Script(
        error(
            404, "model_not_found", "The model `gpt-6-astra` does not exist or you do not have access to it."
        ),
        response(model="gpt-5"),
        response(model="gpt-5"),
    )
    llm, _ = client(script)
    res = await llm.generate(request(["gpt-6-astra", "gpt-5"]))
    assert res.model == "gpt-5" and res.models_tried == ["gpt-6-astra", "gpt-5"]
    await llm.generate(request(["gpt-6-astra", "gpt-5"]))
    assert [b["model"] for b in script.bodies] == ["gpt-6-astra", "gpt-5", "gpt-5"]  # no second try of astra


async def test_non_reasoning_fallback_gets_no_effort_and_a_clamped_output_limit() -> None:
    script = Script(response(model="gpt-4o"))
    llm, _ = client(script)
    await llm.generate(request(["gpt-4o"]))
    assert "reasoning" not in script.bodies[0]
    assert script.bodies[0]["max_output_tokens"] == 16384


async def test_unsupported_reasoning_parameter_is_dropped_and_retried() -> None:
    script = Script(
        error(400, "unsupported_parameter", "Unsupported parameter: 'reasoning.effort'"), response()
    )
    llm, _ = client(script)
    await llm.generate(request(["some-new-model"]))
    assert "reasoning" in script.bodies[0] and "reasoning" not in script.bodies[1]


async def test_all_models_unavailable_fails_loudly() -> None:
    script = Script(
        error(404, "model_not_found", "no such model"), error(403, "model_not_found", "no access to model")
    )
    llm, _ = client(script)
    c, records, _ = ctx()
    with pytest.raises(LLMUnavailable, match="none of the configured models"):
        await llm.generate(request(["a-model", "b-model"]), c)
    assert records[-1].status == "unavailable"


# ---------------------------------------------------------------------- retries and hard failures


async def test_rate_limit_is_retried_honouring_retry_after() -> None:
    script = Script(error(429, "rate_limit_exceeded", "slow down", {"retry-after": "2"}), response())
    llm, slept = client(script)
    c, records, _ = ctx()
    res = await llm.generate(request(), c)
    assert slept == [2.0] and res.retries == 1 and records[-1].retries == 1


async def test_server_errors_are_retried_with_backoff_then_give_up() -> None:
    script = Script(*[error(500, "server_error", "boom") for _ in range(3)])
    llm, slept = client(script, retries=2)
    with pytest.raises(LLMUnavailable, match="after 2 retries"):
        await llm.generate(request())
    assert len(slept) == 2 and all(s > 0 for s in slept)


@pytest.mark.parametrize(
    ("resp", "exc", "match"),
    [
        (
            error(
                401,
                "account_deactivated",
                "The OpenAI account associated with this API key has been deactivated.",
            ),
            LLMAuthError,
            "deactivated",
        ),
        (error(429, "insufficient_quota", "You exceeded your current quota"), LLMUnavailable, "quota"),
    ],
)
async def test_bad_key_and_empty_quota_are_not_retried(
    resp: httpx.Response, exc: type[Exception], match: str
) -> None:
    script = Script(resp)
    llm, slept = client(script)
    with pytest.raises(exc, match=match):
        await llm.generate(request(["gpt-6-sol", "gpt-5"]))
    assert len(script.bodies) == 1 and slept == []


async def test_missing_key_is_a_clear_error() -> None:
    llm, _ = client(Script(), key=None)
    with pytest.raises(LLMAuthError, match="OPENAI_API_KEY is not set"):
        await llm.generate(request())


# ---------------------------------------------------------------------- bad responses count as failures


async def test_refusal_is_a_failed_attempt() -> None:
    llm, _ = client(Script(response(refusal="I can't help with that.")))
    c, records, _ = ctx()
    with pytest.raises(LLMRefusal):
        await llm.generate(request(), c)
    assert records[-1].status == "refusal" and records[-1].usage.output_tokens == 300  # still paid for


async def test_truncated_response_is_a_failed_attempt() -> None:
    llm, _ = client(
        Script(
            response(text='{"a": 1', status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        )
    )
    with pytest.raises(LLMIncomplete) as err:
        await llm.generate(request())
    assert err.value.reason == "max_output_tokens"


@pytest.mark.parametrize("text", ['{"a": 1', "[1, 2]"])
async def test_malformed_output_is_a_failed_attempt(text: str) -> None:
    llm, _ = client(Script(response(text=text)))
    with pytest.raises(LLMBadOutput):
        await llm.generate(request())


# ---------------------------------------------------------------------- budget


async def test_budget_is_a_hard_stop_before_the_next_call() -> None:
    script = Script(response(), response())
    llm, _ = client(script)
    records: list[CallRecord] = []
    budget = Budget(max_usd=0.004, max_tokens=10_000_000)
    c = CallContext(budget=budget, on_record=records.append)
    await llm.generate(request(), c)  # costs ~$0.0046: crosses the line
    with pytest.raises(BudgetExceeded):
        await llm.generate(request(), c)
    assert len(script.bodies) == 1 and records[-1].status == "budget"


# ---------------------------------------------------------------------- config tables


def test_effort_is_mapped_to_what_each_model_accepts() -> None:
    assert MODELS.effort_for("gpt-6-sol", "xhigh") == "xhigh"
    assert MODELS.effort_for("gpt-5", "none") == "minimal"
    assert MODELS.effort_for("gpt-5", "max") == "high"
    assert MODELS.effort_for("gpt-5-2025-08-07", "medium") == "medium"  # dated snapshot
    assert MODELS.effort_for("gpt-4o", "high") is None
    assert MODELS.effort_for("brand-new-model", "low") == "low"


def test_prices_resolve_snapshots_and_unknown_models() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert PRICES.cost("gpt-5-2025-08-07", usage) == pytest.approx(11.25)
    assert PRICES.cost("mystery", usage) is None


def test_stage_model_chain_appends_fallbacks() -> None:
    s = Settings(
        _env_file=None,
        animator_model_correction_final="gpt-6-astra",
        animator_model_fallbacks="gpt-5, gpt-4.1,gpt-6-astra",
    )
    assert s.stage_model("correction_final") == (["gpt-6-astra", "gpt-5", "gpt-4.1"], "high")
    with pytest.raises(ValueError, match="no model configured"):
        Settings(_env_file=None).stage_model("storyboard")


# ---------------------------------------------------------------------- record / replay


async def test_record_then_replay_offline(tmp_path: Path) -> None:
    fake = FakeLLM(lambda r: {"a": 7})
    await RecordingLLM(fake, tmp_path).generate(request())
    replay = ReplayLLM(tmp_path)
    assert (await replay.generate(request())).data == {"a": 7}
    changed = request()
    changed.instructions = "different rules"
    with pytest.raises(LLMError, match="no recording"):
        await replay.generate(changed)
