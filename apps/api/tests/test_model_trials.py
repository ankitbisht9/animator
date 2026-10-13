"""Model trials (ADR-056): an admin makes a lesson with another model, through OpenRouter, to compare it
with the normal setup. The OpenRouter client against a scripted HTTP server; the router that sends only
trial lessons to it; the runner, the pipeline API and the platform with a scripted trial model."""

from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path
from typing import Any, cast

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from animator.api.app import create_app
from animator.config import Settings
from animator.jobs.store import MemoryStore
from animator.llm import (
    CallContext,
    CallRecord,
    FakeLLM,
    LLMAuthError,
    LLMBadOutput,
    LLMIncomplete,
    LLMRefusal,
    LLMRequest,
    LLMUnavailable,
    Message,
    PriceTable,
    Usage,
)
from animator.llm.catalog import Price
from animator.llm.openrouter_client import OpenRouterLLM, misfit, parse_output
from animator.llm.trials import TrialLog, TrialRouter, trial_log
from animator.llm.types import ImagePart
from animator.platform import catalog as catalog_module
from tests.test_llm import PRICES, SCHEMA, request
from tests.test_pipeline import Script, settings
from tests.test_platform import admin_client, csrf, platform_settings, register, wait_for


@pytest.fixture(autouse=True)
def quick_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(catalog_module, "SYNC_EVERY_S", 0.1)


FLASH = "qwen/qwen3.5-flash-02-23"
PRO = "deepseek/deepseek-v4-pro"
SMALL = "qwen/qwen3.5-9b"
TRIALS = f"{FLASH}, {PRO},{SMALL},{FLASH}"

# What OpenRouter lists (GET /models), trimmed to what the client reads.
LISTING = {
    "data": [
        {
            "id": FLASH,
            "architecture": {"input_modalities": ["text", "image", "video"]},
            "supported_parameters": ["max_tokens", "reasoning", "response_format", "structured_outputs"],
            "pricing": {"prompt": "0.000000065", "completion": "0.00000026"},
            "top_provider": {"max_completion_tokens": 65536},
        },
        {
            "id": PRO,
            "architecture": {"input_modalities": ["text"]},
            "supported_parameters": ["max_tokens", "reasoning", "response_format", "structured_outputs"],
            "pricing": {"prompt": "0.00000095", "completion": "0.0000019", "input_cache_read": "0.00000008"},
            "top_provider": {"max_completion_tokens": 32000},
        },
        {
            "id": SMALL,
            "architecture": {"input_modalities": ["text", "image"]},
            "supported_parameters": ["max_tokens", "response_format", "structured_outputs"],
            "pricing": {"prompt": "0.0000001", "completion": "0.00000015"},
            "top_provider": {"max_completion_tokens": None},
        },
    ]
}


def completion(
    text: str = '{"a": 1}',
    model: str = FLASH,
    finish: str = "stop",
    cost: float | None = 0.00042,
    **extra: Any,
) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "prompt_tokens": 1000,
        "completion_tokens": 300,
        "total_tokens": 1300,
        "prompt_tokens_details": {"cached_tokens": 200},
        "completion_tokens_details": {"reasoning_tokens": 100},
    }
    if cost is not None:
        usage["cost"] = cost
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if "refusal" in extra:
        message = {"role": "assistant", "content": None, "refusal": extra.pop("refusal")}
    return {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": usage,
        **extra,
    }


def error(status: int, message: str, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": message, "code": status}}, headers=headers)


def sse(*chunks: dict[str, Any]) -> httpx.Response:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def chunk(content: str | None = None, finish: str | None = None, **extra: Any) -> dict[str, Any]:
    delta = {"content": content} if content is not None else {}
    choices = [] if "usage" in extra else [{"index": 0, "delta": delta, "finish_reason": finish}]
    return {
        "id": "gen-2",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": FLASH,
        "choices": choices,
        **extra,
    }


class OpenRouter:
    """A scripted OpenRouter: the model list, and chat answers in order (keeping each request)."""

    def __init__(self, *answers: httpx.Response | dict[str, Any]) -> None:
        self.answers = list(answers)
        self.bodies: list[dict[str, Any]] = []
        self.auth: list[str] = []
        self.listings = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.method == "GET" and req.url.path.endswith("/models"):
            self.listings += 1
            return httpx.Response(200, json=LISTING)
        assert req.url.path.endswith("/chat/completions")
        self.bodies.append(json.loads(req.content))
        self.auth.append(req.headers.get("authorization", ""))
        item = self.answers.pop(0)
        return item if isinstance(item, httpx.Response) else httpx.Response(200, json=item)


def router_client(
    server: OpenRouter, *, key: str | None = "sk-or-test", retries: int = 2
) -> tuple[OpenRouterLLM, list[float]]:
    s = Settings(
        _env_file=None, openrouter_api_key=key, animator_trial_models=TRIALS, openai_max_retries=retries
    )
    slept: list[float] = []

    async def sleep(t: float) -> None:
        slept.append(t)

    http = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return OpenRouterLLM(s, PRICES, http_client=http, sleep=sleep), slept


def ctx() -> tuple[CallContext, list[CallRecord]]:
    records: list[CallRecord] = []
    return CallContext(on_record=records.append), records


# ---------------------------------------------------------------------- settings


def test_trial_models_need_a_key_and_are_listed_once() -> None:
    assert Settings(_env_file=None, animator_trial_models=TRIALS).trial_models == []
    s = Settings(_env_file=None, openrouter_api_key="sk-or-test", animator_trial_models=TRIALS)
    assert s.trial_models == [FLASH, PRO, SMALL]
    assert Settings(_env_file=None, openrouter_api_key="sk-or-test").trial_models == []
    assert Settings(_env_file=None, openrouter_api_key="  ", animator_trial_models=FLASH).trial_models == []


# ---------------------------------------------------------------------- the OpenRouter client


async def test_a_call_asks_for_the_schema_strictly_and_reports_openrouters_cost() -> None:
    server = OpenRouter(completion(model=PRO, cost=0.00123))
    llm, _ = router_client(server)
    c, records = ctx()
    out = await llm.generate(request([PRO], effort="xhigh"), c)

    body = server.bodies[0]
    assert server.auth == ["Bearer sk-or-test"]
    assert body["model"] == PRO
    assert body["messages"] == [{"role": "system", "content": "rules"}, {"role": "user", "content": "topic"}]
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "Plan", "strict": True, "schema": SCHEMA},
    }
    assert body["max_tokens"] == 32000  # the stage asks for 40k; the model's limit is lower
    assert body["provider"] == {"require_parameters": True}  # only providers that honour the schema
    assert body["usage"] == {"include": True}
    assert body["reasoning"] == {"effort": "high", "exclude": True}

    assert out.data == {"a": 1} and out.model == PRO and out.cost_usd == pytest.approx(0.00123)
    assert out.usage == Usage(
        input_tokens=1000, cached_input_tokens=200, output_tokens=300, reasoning_tokens=100
    )
    assert [(r.status, r.model, r.cost_usd, r.generation_id) for r in records] == [("ok", PRO, 0.00123, "g1")]
    assert server.listings == 1


async def test_a_model_without_reasoning_gets_none_and_its_listed_price_when_no_cost_is_reported() -> None:
    server = OpenRouter(completion(model=SMALL, cost=None))
    llm, _ = router_client(server)
    out = await llm.generate(request([SMALL]), ctx()[0])
    body = server.bodies[0]
    assert "reasoning" not in body and body["max_tokens"] == 40_000
    listed = PriceTable({SMALL: Price(0.1, 0.1, 0.15)}).cost(SMALL, out.usage)
    assert out.cost_usd == pytest.approx(listed)


async def test_effort_none_turns_reasoning_off() -> None:
    server = OpenRouter(completion())
    llm, _ = router_client(server)
    await llm.generate(request([FLASH], effort="none"), ctx()[0])
    assert server.bodies[0]["reasoning"] == {"enabled": False}


async def test_answers_that_cannot_be_used_are_failed_attempts() -> None:
    server = OpenRouter(
        completion('```json\n{"a": 2}\n```'),
        completion('{"a": 3', finish="length"),
        completion(refusal="I can't help with that"),
    )
    llm, _ = router_client(server)
    c, records = ctx()
    assert (await llm.generate(request([FLASH]), c)).data == {"a": 2}  # a fenced answer is unwrapped
    with pytest.raises(LLMIncomplete):
        await llm.generate(request([FLASH]), c)
    with pytest.raises(LLMRefusal):
        await llm.generate(request([FLASH]), c)
    assert [r.status for r in records] == ["ok", "incomplete", "refusal"]
    assert records[1].cost_usd == pytest.approx(0.00042)  # a cut-off answer is still paid for


async def test_rate_limits_and_provider_failures_are_retried() -> None:
    server = OpenRouter(
        error(429, "rate limited", {"retry-after": "2"}),
        # OpenRouter reports a provider that failed mid-answer inside a 200.
        httpx.Response(200, json={"error": {"message": "upstream provider failed", "code": 502}}),
        completion(),
    )
    llm, slept = router_client(server)
    c, records = ctx()
    out = await llm.generate(request([FLASH]), c)
    assert out.data == {"a": 1} and out.retries == 2 and slept[0] == 2.0
    assert len(server.bodies) == 3 and [r.status for r in records] == ["ok"]


async def test_used_up_credits_unknown_models_and_bad_keys_are_not_retried() -> None:
    for status, kind in ((402, LLMUnavailable), (404, LLMUnavailable), (401, LLMAuthError)):
        server = OpenRouter(error(status, "no"))
        llm, slept = router_client(server)
        c, records = ctx()
        with pytest.raises(kind):
            await llm.generate(request([FLASH]), c)
        assert len(server.bodies) == 1 and slept == [] and len(records) == 1
    server = OpenRouter(error(402, "Insufficient credits"))
    llm, _ = router_client(server)
    with pytest.raises(LLMUnavailable, match="credits are used up"):
        await llm.generate(request([FLASH]), ctx()[0])


async def test_a_rejected_reasoning_parameter_is_dropped_and_retried() -> None:
    server = OpenRouter(error(400, "reasoning is not supported by this provider"), completion())
    llm, _ = router_client(server)
    assert (await llm.generate(request([FLASH]), ctx()[0])).data == {"a": 1}
    assert "reasoning" in server.bodies[0] and "reasoning" not in server.bodies[1]


async def test_a_streamed_answer_feeds_its_text_and_reads_the_cost() -> None:
    usage = {"prompt_tokens": 900, "completion_tokens": 120, "total_tokens": 1020, "cost": 0.0002}
    server = OpenRouter(sse(chunk('{"a"'), chunk(": 7}"), chunk(finish="stop"), chunk(usage=usage)))
    llm, _ = router_client(server)

    class Sink:
        def __init__(self) -> None:
            self.text = ""

        def reset(self) -> None:
            self.text = ""

        def feed(self, text: str) -> None:
            self.text += text

    sink = Sink()
    c, records = ctx()
    out = await llm.generate(request([FLASH]), c.streaming(sink))
    assert server.bodies[0]["stream"] is True and server.bodies[0]["stream_options"] == {
        "include_usage": True
    }
    assert out.data == {"a": 7} and sink.text == '{"a": 7}'
    assert out.cost_usd == pytest.approx(0.0002) and out.usage.output_tokens == 120
    assert [r.status for r in records] == ["ok"]


async def test_without_a_key_nothing_is_sent() -> None:
    server = OpenRouter()
    llm, _ = router_client(server, key=None)
    with pytest.raises(LLMAuthError, match="OPENROUTER_API_KEY"):
        await llm.generate(request([FLASH]), ctx()[0])
    assert server.bodies == [] and server.listings == 0


async def test_what_a_model_can_read_comes_from_the_list_fetched_once() -> None:
    server = OpenRouter()
    llm, _ = router_client(server)
    assert await llm.sees_images(FLASH) and await llm.sees_images(SMALL)
    assert not await llm.sees_images(PRO) and not await llm.sees_images("someone/unknown")
    assert server.listings == 1


def test_parse_output() -> None:
    assert parse_output(' {"a": 1} ') == {"a": 1}
    assert parse_output('```\n{"a": 1}\n```') == {"a": 1}
    assert parse_output('<think>{"no": 0} first...</think>\n{"a": 1}') == {"a": 1}
    assert parse_output('Here it is: {"a": {"b": 2}} Hope it helps.') == {"a": {"b": 2}}
    for bad in ("", "[1, 2]", "no json here", '{"a": '):
        with pytest.raises(LLMBadOutput):
            parse_output(bad)


def test_misfit_names_what_the_answer_misses() -> None:
    assert misfit({"a": 1}, SCHEMA) is None
    assert misfit({"question": "why?"}, SCHEMA) == "missing a; not in the schema: question"
    assert misfit({"anything": 1}, {"type": "object"}) is None  # nothing to compare with


async def test_a_provider_that_does_not_keep_to_the_schema_is_stepped_up_for_good() -> None:
    thinking = {"effort": "high", "exclude": True}
    server = OpenRouter(
        completion('{"question": "Why does ice float?"}'),  # JSON of its own: the schema was not applied
        completion('{"a": 5}'),  # told the schema: fine
        completion("false"),  # a reasoning answer its provider could not keep to JSON
        completion('{"a": 6}'),  # told, and no reasoning: fine
        completion('{"a": 7}'),
        completion('{"b": 8}'),
    )
    llm, slept = router_client(server)
    c, records = ctx()
    first = await llm.generate(request([FLASH]), c)
    assert first.data == {"a": 5} and first.retries == 1
    plain, told = (b["messages"][0]["content"] for b in server.bodies)
    assert plain == "rules" and server.bodies[0]["reasoning"] == thinking
    assert told.startswith("rules\n\n# Output format\n") and told.endswith(
        json.dumps(SCHEMA, separators=(",", ":"))
    )
    assert server.bodies[1]["reasoning"] == thinking
    assert [r.status for r in records] == ["bad_output", "ok"]
    assert records[0].cost_usd == pytest.approx(0.00042)  # the ignored answer is paid for, and counted

    second = await llm.generate(request([FLASH]), c)
    assert second.data == {"a": 6} and second.retries == 1
    assert server.bodies[2]["messages"][0]["content"] == told  # told at once now
    assert server.bodies[3]["messages"][0]["content"] == told
    assert server.bodies[3]["reasoning"] == {"enabled": False}

    assert (await llm.generate(request([FLASH]), c)).data == {"a": 7}
    assert server.bodies[4]["reasoning"] == {"enabled": False}  # kept from then on
    with pytest.raises(LLMBadOutput, match="does not follow the schema: missing a"):
        await llm.generate(request([FLASH]), c)  # at the last step a wrong answer is the stage's to handle
    assert len(server.bodies) == 6 and slept == []


async def test_a_provider_failing_to_keep_a_stream_to_json_steps_the_model_up() -> None:
    abnormal = "Upstream error from Alibaba: Model output became abnormal while generating a JSON response"
    server = OpenRouter(
        sse({"error": {"message": abnormal, "code": 502}}),
        sse(chunk('{"a": 3}'), chunk(finish="stop")),
    )
    llm, slept = router_client(server)
    c, records = ctx()

    class Sink:
        text = ""

        def reset(self) -> None:
            self.text = ""

        def feed(self, text: str) -> None:
            self.text += text

    out = await llm.generate(request([FLASH]), c.streaming(Sink()))
    assert out.data == {"a": 3} and out.retries == 1 and slept == [] and [r.status for r in records] == ["ok"]
    assert server.bodies[1]["messages"][0]["content"].startswith("rules\n\n# Output format\n")


async def test_a_provider_that_applies_the_schema_never_gets_it_in_the_instructions() -> None:
    server = OpenRouter(completion(model=PRO), completion(model=PRO))
    llm, _ = router_client(server)
    for _ in range(2):
        await llm.generate(request([PRO]), ctx()[0])
    assert [b["messages"][0]["content"] for b in server.bodies] == ["rules", "rules"]


# ---------------------------------------------------------------------- the router


class FakeTrial:
    """Stands in for OpenRouterLLM: scripted answers, and whether the model reads images."""

    def __init__(self, handler: Any = None, vision: bool = False, cost: float = 0.0) -> None:
        self.llm = FakeLLM(handler or (lambda r: {"a": 9}), model="via-openrouter", cost=cost)
        self.vision = vision

    @property
    def requests(self) -> list[LLMRequest]:
        return self.llm.requests

    async def sees_images(self, model: str) -> bool:
        return self.vision

    async def generate(self, request: LLMRequest, c: CallContext | None = None) -> Any:
        return await self.llm.generate(request, c)


def trial_router(vision: bool = False, log: TrialLog | None = None) -> tuple[TrialRouter, FakeLLM, FakeTrial]:
    normal = FakeLLM(lambda r: {"a": 1})
    trial = FakeTrial(vision=vision)
    return TrialRouter(normal, cast(OpenRouterLLM, trial), [FLASH, PRO], log), normal, trial


def lesson_request(gen_id: str, images: bool = False) -> LLMRequest:
    message = Message.user("look", ImagePart(b"\x89PNG")) if images else Message.user("look")
    return dataclasses.replace(request(["m-normal"]), generation_id=gen_id, messages=[message])


async def test_only_lessons_assigned_a_trial_model_go_to_it() -> None:
    router, normal, trial = trial_router()
    router.assign("g1", PRO)
    router.assign("g3", "someone/not-a-trial-model")

    assert (await router.generate(lesson_request("g1"))).data == {"a": 9}
    assert (await router.generate(lesson_request("g2"))).data == {"a": 1}
    assert (await router.generate(lesson_request("g3"))).data == {"a": 1}
    assert [r.models for r in trial.requests] == [[PRO]]
    assert [r.models for r in normal.requests] == [["m-normal"], ["m-normal"]]

    router.assign("g1", None)  # back to normal
    await router.generate(lesson_request("g1"))
    assert len(trial.requests) == 1 and router.model_for("g1") is None


async def test_images_stay_on_the_normal_model_when_the_trial_model_cannot_read_them() -> None:
    router, normal, trial = trial_router(vision=False)
    router.assign("g1", PRO)
    await router.generate(lesson_request("g1", images=True))
    assert len(normal.requests) == 1 and normal.requests[0].models == ["m-normal"] and trial.requests == []

    router, normal, trial = trial_router(vision=True)
    router.assign("g1", FLASH)
    await router.generate(lesson_request("g1", images=True))
    assert normal.requests == [] and trial.requests[0].models == [FLASH]


async def test_the_trial_log_keeps_trial_lessons_across_restarts(tmp_path: Path) -> None:
    log = TrialLog(tmp_path / "model_trials.json")
    assert log.models() == {}
    log.record("g1", PRO)
    log.record("g2", "someone/removed-since")
    assert TrialLog(tmp_path / "model_trials.json").models() == {"g1": PRO, "g2": "someone/removed-since"}
    assert trial_log(tmp_path) is trial_log(tmp_path)

    router, normal, trial = trial_router(log=TrialLog(tmp_path / "model_trials.json"))  # after a restart
    await router.generate(lesson_request("g1"))  # a doubt about a trial lesson
    await router.generate(lesson_request("g2"))  # its model is no longer offered: the normal one
    assert [r.models for r in trial.requests] == [[PRO]] and len(normal.requests) == 1


async def test_other_attributes_are_the_normal_clients() -> None:
    router, normal, _ = trial_router()
    assert router.requests is normal.requests


# ---------------------------------------------------------------------- the pipeline API and the platform


def trial_settings(tmp_path: Path) -> Settings:
    return settings(
        openai_api_key="sk-proj-secret-value-that-must-never-leak",
        openrouter_api_key="sk-or-secret-value-that-must-never-leak",
        animator_trial_models=f"{FLASH},{PRO}",
        animator_data_dir=tmp_path / "data",
    )


def test_a_lesson_made_with_a_trial_model_through_the_pipeline_api(
    tmp_path: Path, worker_script: Path
) -> None:
    normal = FakeLLM(Script())
    trial = FakeTrial(Script(), cost=0.001)
    router = TrialRouter(normal, cast(OpenRouterLLM, trial), [FLASH, PRO])
    app = create_app(
        settings=trial_settings(tmp_path), llm=router, store=MemoryStore(), resume_on_start=False
    )
    with TestClient(app) as client:
        cfg = client.get("/api/config").json()
        assert cfg["trial_models"] == [FLASH, PRO] and "secret" not in json.dumps(cfg)
        bad = client.post("/api/generations", json={"topic": "Archimedes' principle", "trial_model": SMALL})
        assert bad.status_code == 422

        res = client.post("/api/generations", json={"topic": "Archimedes' principle", "trial_model": PRO})
        assert res.status_code == 201
        gen_id = res.json()["id"]
        final = wait_generation(client, gen_id)
        assert final["status"] == "complete"
        assert final["models"]["storyboard"] == [PRO] and final["request"]["trial_model"] == PRO
        assert trial.requests and all(r.models == [PRO] and r.generation_id == gen_id for r in trial.requests)
        assert normal.requests == []
        assert final["metrics"]["cost_usd"] == pytest.approx(0.001 * len(trial.requests))
        assert trial_log(tmp_path / "data").models() == {gen_id: PRO}

        plain = client.post("/api/generations", json={"topic": "Pendulums"}).json()["id"]
        assert wait_generation(client, plain)["status"] == "complete"
        assert normal.requests and all(r.generation_id == plain for r in normal.requests)
        assert trial_log(tmp_path / "data").models() == {gen_id: PRO}


def wait_generation(client: TestClient, gen_id: str, timeout: float = 20) -> dict[str, Any]:
    end = time.time() + timeout
    while time.time() < end:
        got: dict[str, Any] = client.get(f"/api/generations/{gen_id}").json()
        if got["status"] in ("complete", "failed", "cancelled"):
            return got
        time.sleep(0.05)
    raise AssertionError(f"{gen_id} did not finish")


def test_only_admins_make_trial_lessons_and_see_their_model(tmp_path: Path, worker_script: Path) -> None:
    normal = FakeLLM(Script())
    trial = FakeTrial(Script(), cost=0.002)
    router = TrialRouter(normal, cast(OpenRouterLLM, trial), [FLASH, PRO])
    app = create_app(
        settings=trial_settings(tmp_path),
        llm=router,
        store=MemoryStore(),
        resume_on_start=False,
        platform=platform_settings(tmp_path),
    )
    with TestClient(app) as c:
        register(c, "asha@example.org")
        r = c.post(
            "/api/v1/lessons", json={"topic": "Archimedes' principle", "trial_model": FLASH}, headers=csrf(c)
        )
        assert r.status_code == 403  # learners cannot

        admin = admin_client(c)
        r = admin.post(
            "/api/v1/lessons",
            json={"topic": "Archimedes' principle", "trial_model": SMALL},
            headers=csrf(admin),
        )
        assert r.status_code == 422  # not offered (ANIMATOR_TRIAL_MODELS)
        r = admin.post(
            "/api/v1/lessons",
            json={"topic": "Archimedes' principle", "trial_model": FLASH},
            headers=csrf(admin),
        )
        assert r.status_code == 201, r.text
        lid = r.json()["id"]
        assert r.json()["trial_model"] == FLASH
        done = wait_for(admin, lid)
        assert done["trial_model"] == FLASH and done["cost_usd"] == pytest.approx(0.002 * len(trial.requests))
        assert trial.requests and {r.generation_id for r in trial.requests} == {lid} and normal.requests == []

        listed = admin.get("/api/v1/admin/lessons").json()["items"]
        assert [x["trial_model"] for x in listed if x["id"] == lid] == [FLASH]
        spend = admin.get("/api/v1/admin/usage/lessons").json()["items"]
        assert [x["trial_model"] for x in spend if x["id"] == lid] == [FLASH]

        r = admin.post("/api/v1/lessons", json={"topic": "Pendulums"}, headers=csrf(admin))
        plain = wait_for(admin, r.json()["id"])
        assert plain["trial_model"] is None and normal.requests
