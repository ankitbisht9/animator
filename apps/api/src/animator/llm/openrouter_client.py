"""OpenRouter implementation of `LLMClient`, for model trials (ADR-056): an admin makes a lesson with
another model to compare it with the normal setup. Chat Completions with strict structured outputs.

- Routing: `provider.require_parameters` sends a call only to providers that support every parameter
  it uses (structured outputs above all), so a model is never silently answered without its schema.
- Reasoning: the stage's effort, as OpenRouter's `reasoning.effort`, for models that take it.
- A provider may accept a schema and not apply it, or fail to keep a reasoning model to JSON
  (Qwen3.5-Flash's only provider, Alibaba, does both). Such a model is stepped up, once per process
  and only when it happens: an answer without the schema's shape (or a provider's "JSON" failure) is
  asked again with the schema written in the instructions; if that fails too, also without reasoning.
  The model keeps that from then on. Schemas are large (a scene's is ~30k tokens): only where needed.
- Cost: what OpenRouter reports for the call (`usage.cost`, the provider's real price), else the price
  table, else OpenRouter's listed price.
- Retries, refusals, truncation and call records as in the OpenAI client.
The API key is never logged and never leaves this module.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx2 as httpx
import openai
from openai import AsyncOpenAI

from animator.config import Settings
from animator.llm.catalog import Price, PriceTable
from animator.llm.errors import (
    LLMAuthError,
    LLMBadOutput,
    LLMError,
    LLMIncomplete,
    LLMRefusal,
    LLMUnavailable,
)
from animator.llm.openai_client import _retry_after
from animator.llm.types import (
    CallContext,
    CallRecord,
    ImagePart,
    LLMRequest,
    LLMResult,
    Message,
    TextPart,
    Usage,
)

log = logging.getLogger("animator.llm.openrouter")

# OpenRouter's reasoning efforts; ours above "high" map to it.
_EFFORTS = {
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)
_THINK = re.compile(r"^\s*<think>.*?</think>", re.DOTALL)
# For a model whose provider does not apply the schema: the schema follows this in the instructions.
_SCHEMA_NOTE = (
    "\n\n# Output format\n"
    "Answer with one JSON object and nothing else: no prose, no code fence. It must follow this JSON "
    "Schema exactly: every required property, with exactly these names, and no other property.\n"
)


class _StreamBroken(Exception):
    """A response that ended without its end, or with a provider's error (retried like a dropped
    connection)."""


class _SchemaIgnored(Exception):
    """The answer does not have the schema's shape, and the model has not been stepped up fully yet."""


# How a model is asked (see the module's docstring): 0 as is, 1 with the schema in its instructions,
# 2 with the schema there and no reasoning.
_TOLD, _PLAIN = 1, 2


def _why(e: Exception) -> str:
    if isinstance(e, openai.APIError):
        body = e.body if isinstance(e.body, dict) else {}
        err = body.get("error", body) if isinstance(body, dict) else {}
        msg = err.get("message") if isinstance(err, dict) else None
        return str(msg or e.message or e)[:400]
    return str(e)[:400]


@dataclass(frozen=True)
class RouterModel:
    """What OpenRouter lists about a model."""

    id: str
    vision: bool = False
    reasoning: bool = False
    structured: bool = False
    max_output: int | None = None
    price: Price | None = None  # USD per 1M tokens


def _router_model(m: dict[str, Any]) -> RouterModel:
    params = set(m.get("supported_parameters") or [])
    modalities = set((m.get("architecture") or {}).get("input_modalities") or [])
    pricing = m.get("pricing") or {}

    def per_m(key: str) -> float | None:
        raw = pricing.get(key)
        try:
            v = float(raw) if raw is not None else -1.0
        except (TypeError, ValueError):
            return None
        return v * 1e6 if v >= 0 else None

    inp, out = per_m("prompt"), per_m("completion")
    cached = per_m("input_cache_read")
    price = (
        Price(inp, cached if cached is not None else inp, out)
        if inp is not None and out is not None
        else None
    )
    top = m.get("top_provider") or {}
    max_out = top.get("max_completion_tokens")
    return RouterModel(
        id=str(m.get("id")),
        vision="image" in modalities,
        reasoning="reasoning" in params,
        structured="structured_outputs" in params,
        max_output=int(max_out) if isinstance(max_out, int) and max_out > 0 else None,
        price=price,
    )


def _to_chat(m: Message) -> dict[str, Any]:
    role = "system" if m.role == "developer" else m.role
    if all(isinstance(p, TextPart) for p in m.parts):
        return {"role": role, "content": "\n\n".join(p.text for p in m.parts if isinstance(p, TextPart))}
    content: list[dict[str, Any]] = []
    for p in m.parts:
        if isinstance(p, TextPart):
            content.append({"type": "text", "text": p.text})
        elif isinstance(p, ImagePart):
            content.append({"type": "image_url", "image_url": {"url": p.data_url(), "detail": p.detail}})
    return {"role": role, "content": content}


def _usage(u: dict[str, Any] | None) -> tuple[Usage, float | None]:
    """Tokens and OpenRouter's reported cost (USD) from a `usage` object."""
    if not isinstance(u, dict):
        return Usage(), None
    prompt = u.get("prompt_tokens_details") or {}
    completion = u.get("completion_tokens_details") or {}
    cost = u.get("cost")
    return (
        Usage(
            input_tokens=int(u.get("prompt_tokens") or 0),
            cached_input_tokens=int(prompt.get("cached_tokens") or 0) if isinstance(prompt, dict) else 0,
            output_tokens=int(u.get("completion_tokens") or 0),
            reasoning_tokens=int(completion.get("reasoning_tokens") or 0)
            if isinstance(completion, dict)
            else 0,
        ),
        float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) else None,
    )


def parse_output(text: str) -> dict[str, Any]:
    """The JSON object a model answered: a leading <think> block and a code fence are removed, and
    words around the object are ignored. Raises LLMBadOutput."""
    body = _THINK.sub("", text, count=1).strip()
    m = _FENCE.match(body)
    if m:
        body = m.group(1)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        start, end = body.find("{"), body.rfind("}")
        try:
            data = json.loads(body[start : end + 1]) if 0 <= start < end else None
        except json.JSONDecodeError:
            data = None
        if data is None:
            raise LLMBadOutput(f"output is not valid JSON ({e.msg} at {e.pos})") from e
    if not isinstance(data, dict):
        raise LLMBadOutput("output is not a JSON object")
    return data


def misfit(data: dict[str, Any], schema: dict[str, Any]) -> str | None:
    """How an answer misses the schema's top level (required properties missing, or properties it does
    not allow), or None. A provider that applies the schema never misses it; one that ignores it does."""
    props = schema.get("properties")
    if not isinstance(props, dict):
        return None
    missing = [k for k in schema.get("required") or [] if k not in data]
    extra = [k for k in data if k not in props] if schema.get("additionalProperties") is False else []
    found = []
    if missing:
        found.append("missing " + ", ".join(missing[:12]))
    if extra:
        found.append("not in the schema: " + ", ".join(extra[:12]))
    return "; ".join(found) or None


@dataclass
class _Answer:
    text: str
    finish: str | None
    refusal: str | None
    model: str
    usage: Usage
    cost: float | None
    rid: str | None


class OpenRouterLLM:
    def __init__(
        self,
        settings: Settings,
        prices: PriceTable,
        http_client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.prices = prices
        self.max_retries = settings.openai_max_retries
        self._sleep = sleep
        self._sem = asyncio.Semaphore(settings.llm_max_concurrency)
        self._http = http_client
        self._models: dict[str, RouterModel] | None = None
        self._models_lock = asyncio.Lock()
        self._client: AsyncOpenAI | None = None
        # Models stepped up because their provider did not apply the schema (_TOLD, _PLAIN).
        self._level: dict[str, int] = {}
        if settings.has_openrouter_key:
            assert settings.openrouter_api_key is not None
            self._client = AsyncOpenAI(
                api_key=settings.openrouter_api_key.get_secret_value().strip(),
                base_url=settings.openrouter_base_url,
                timeout=settings.openrouter_timeout_s,
                max_retries=0,  # retries are ours: counted, logged, Retry-After aware
                http_client=http_client,
                default_headers={"X-Title": "Animator (model trial)"},
            )

    # ------------------------------------------------------------------ what OpenRouter lists

    async def model_info(self, model: str) -> RouterModel | None:
        """The model as OpenRouter lists it (fetched once per process; None when unknown or offline)."""
        async with self._models_lock:
            if self._models is None:
                self._models = await self._fetch_models()
        return self._models.get(model)

    async def _fetch_models(self) -> dict[str, RouterModel]:
        url = self.settings.openrouter_base_url.rstrip("/") + "/models"
        try:
            if self._http is not None:
                resp = await self._http.get(url, timeout=30)
            else:
                async with httpx.AsyncClient(timeout=30) as http:
                    resp = await http.get(url)
            resp.raise_for_status()
            return {
                str(m.get("id")): _router_model(m)
                for m in resp.json().get("data") or []
                if isinstance(m, dict)
            }
        except (httpx.HTTPError, ValueError, AttributeError):
            return {}  # unknown capabilities: calls are sent as they are, and fail loudly if refused

    async def sees_images(self, model: str) -> bool:
        info = await self.model_info(model)
        return info is not None and info.vision

    # ------------------------------------------------------------------ calls

    async def generate(self, request: LLMRequest, ctx: CallContext | None = None) -> LLMResult:
        ctx = ctx or CallContext()
        if ctx.budget is not None:
            try:
                ctx.budget.check()
            except LLMError as e:
                ctx.emit(self._record(request, None, "budget", Usage(), 0.0, 0, str(e)))
                raise
        if self._client is None:
            err = LLMAuthError("OPENROUTER_API_KEY is not set: add it to .env to make trial lessons")
            ctx.emit(self._record(request, None, "auth", Usage(), 0.0, 0, str(err)))
            raise err
        model = request.models[0]
        info = await self.model_info(model)
        started = time.perf_counter()
        retries = 0
        with_reasoning = True

        def failed(status: Any, err: LLMError) -> LLMError:
            ctx.emit(
                self._record(
                    request, model, status, Usage(), time.perf_counter() - started, retries, str(err)
                )
            )
            return err

        while True:
            level = self._level.get(model, 0)
            try:
                async with self._sem:
                    params = self._params(model, request, info, with_reasoning, level)
                    answer = await (self._create(params) if ctx.stream is None else self._stream(params, ctx))
            except openai.AuthenticationError as e:
                raise failed("auth", LLMAuthError(f"OpenRouter rejected the API key: {_why(e)}")) from e
            except openai.BadRequestError as e:
                msg = _why(e)
                if with_reasoning and "reasoning" in msg.lower():
                    with_reasoning = False  # this model's providers take no reasoning parameter
                    continue
                raise failed("error", LLMError(f"OpenRouter rejected the request for {model}: {msg}")) from e
            except (openai.NotFoundError, openai.PermissionDeniedError) as e:
                # 404: the model does not exist, or no provider supports what the call needs
                # (structured outputs). Nothing to retry: the trial model is the only one.
                text = f"OpenRouter cannot serve {model} for this call: {_why(e)}"
                raise failed("unavailable", LLMUnavailable(text)) from e
            except (openai.APIError, _StreamBroken) as e:
                # Rate limits, server errors, dropped connections, a provider's failure inside a 200 or
                # in the middle of a stream.
                status = getattr(e, "status_code", None)
                why = _why(e)
                if status == 402:
                    raise failed(
                        "unavailable", LLMUnavailable(f"OpenRouter credits are used up: {why}")
                    ) from e
                if status is None and "json" in why.lower() and level < _PLAIN:
                    # The provider could not keep the model to JSON (Alibaba aborts a reasoning answer).
                    self._step_up(model, why)
                    retries += 1
                    continue
                retryable = status is None or status in (408, 409, 429) or status >= 500
                if not retryable or retries >= self.max_retries:
                    text = f"OpenRouter call failed after {retries} retries: {why}"
                    raise failed("unavailable", LLMUnavailable(text)) from e
                delay = _retry_after(e)
                if delay is None:
                    delay = min(60.0, 1.5 * 2**retries) * random.uniform(0.5, 1.5)  # noqa: S311 - jitter
                retries += 1
                await self._sleep(delay)
                continue
            try:
                return self._finish(model, request, ctx, answer, info, started, retries, level)
            except _SchemaIgnored as e:
                self._step_up(model, str(e))  # the answer was paid for and is recorded
                retries += 1

    def _step_up(self, model: str, why: str) -> None:
        level = min(_PLAIN, self._level.get(model, 0) + 1)
        self._level[model] = level
        how = "the schema in its instructions" if level == _TOLD else "the schema there and no reasoning"
        log.warning(
            "%s did not keep to the JSON schema (%s): from now on it is asked with %s", model, why[:160], how
        )

    def _params(
        self,
        model: str,
        request: LLMRequest,
        info: RouterModel | None,
        with_reasoning: bool,
        level: int = 0,
    ) -> dict[str, Any]:
        max_out = request.max_output_tokens
        if info is not None and info.max_output is not None:
            max_out = min(max_out, info.max_output)
        extra: dict[str, Any] = {
            # Only providers that support every parameter here (structured outputs above all).
            "provider": {"require_parameters": True},
            "usage": {"include": True},
        }
        if with_reasoning and (info is None or info.reasoning):
            if request.effort == "none" or level >= _PLAIN:
                extra["reasoning"] = {"enabled": False}
            elif request.effort in _EFFORTS:
                extra["reasoning"] = {"effort": _EFFORTS[request.effort], "exclude": True}
        instructions = request.instructions
        if level >= _TOLD:
            # After the static instructions, so the cacheable prefix stays the same for every call.
            instructions += _SCHEMA_NOTE + json.dumps(
                request.schema, separators=(",", ":"), ensure_ascii=False
            )
        return {
            "model": model,
            "messages": [{"role": "system", "content": instructions}]
            + [_to_chat(m) for m in request.messages],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": request.schema_name, "strict": True, "schema": request.schema},
            },
            "max_tokens": max_out,
            "extra_body": extra,
        }

    async def _create(self, params: dict[str, Any]) -> _Answer:
        assert self._client is not None
        resp = await self._client.chat.completions.create(**params)
        data = resp.model_dump()
        choice = (data.get("choices") or [{}])[0] or {}
        message = choice.get("message") or {}
        if data.get("error") or choice.get("finish_reason") == "error":
            # OpenRouter reports a provider's failure inside a 200 answer.
            raise _StreamBroken(f"the provider failed: {data.get('error') or choice.get('error')}")
        usage, cost = _usage(data.get("usage"))
        return _Answer(
            text=str(message.get("content") or ""),
            finish=choice.get("finish_reason"),
            refusal=message.get("refusal"),
            model=str(data.get("model") or params["model"]),
            usage=usage,
            cost=cost,
            rid=data.get("id"),
        )

    async def _stream(self, params: dict[str, Any], ctx: CallContext) -> _Answer:
        """Stream the answer, feeding its text to `ctx.stream` as it comes."""
        assert self._client is not None and ctx.stream is not None
        ctx.stream.reset()
        parts: list[str] = []
        finish: str | None = None
        refusal: str | None = None
        model = str(params["model"])
        usage, cost, rid = Usage(), None, None
        stream = await self._client.chat.completions.create(
            **params, stream=True, stream_options={"include_usage": True}
        )
        async for chunk in stream:
            data = chunk.model_dump()
            if data.get("error"):
                raise _StreamBroken(f"streamed response failed: {data['error']}")
            rid = data.get("id") or rid
            model = str(data.get("model") or model)
            if data.get("usage"):
                usage, cost = _usage(data["usage"])
            for choice in data.get("choices") or []:
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    parts.append(delta["content"])
                    ctx.stream.feed(delta["content"])
                if delta.get("refusal"):
                    refusal = (refusal or "") + str(delta["refusal"])
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
        if finish is None:
            raise _StreamBroken("stream ended without a finish")
        if finish == "error":
            raise _StreamBroken("the provider failed while answering")
        return _Answer("".join(parts), finish, refusal, model, usage, cost, rid)

    def _finish(
        self,
        model: str,
        request: LLMRequest,
        ctx: CallContext,
        a: _Answer,
        info: RouterModel | None,
        started: float,
        retries: int,
        level: int = 0,
    ) -> LLMResult:
        cost = a.cost
        if cost is None:
            cost = self.prices.cost(a.model, a.usage)
        if cost is None and info is not None and info.price is not None:
            cost = PriceTable({model: info.price}).cost(model, a.usage)
        latency = time.perf_counter() - started

        def fail(status: Any, err: LLMError) -> LLMError:
            ctx.emit(self._record(request, a.model, status, a.usage, latency, retries, str(err), cost, a.rid))
            return err

        if a.finish == "length":
            raise fail(
                "incomplete", LLMIncomplete("response cut off (max_output_tokens)", "max_output_tokens")
            )
        if a.refusal or a.finish == "content_filter":
            raise fail("refusal", LLMRefusal(f"model refused: {(a.refusal or 'content filter')[:300]}"))
        try:
            data = parse_output(a.text)
        except LLMBadOutput as e:
            if level < _PLAIN:
                fail("bad_output", LLMBadOutput(f"{e}; asked again, stepped up"))
                raise _SchemaIgnored(str(e)) from e
            raise fail("bad_output", e) from e
        miss = misfit(data, request.schema)
        if miss is not None:
            if level < _PLAIN:
                fail(
                    "bad_output",
                    LLMBadOutput(f"the schema was not applied ({miss}); asked again, stepped up"),
                )
                raise _SchemaIgnored(f"the schema was not applied: {miss}")
            raise fail("bad_output", LLMBadOutput(f"output does not follow the schema: {miss}"))
        ctx.emit(self._record(request, a.model, "ok", a.usage, latency, retries, None, cost, a.rid))
        return LLMResult(data, a.model, a.usage, cost, latency, retries, [model], a.rid)

    def _record(
        self,
        request: LLMRequest,
        model: str | None,
        status: Any,
        usage: Usage,
        latency: float,
        retries: int,
        error: str | None,
        cost: float | None = None,
        response_id: str | None = None,
    ) -> CallRecord:
        return CallRecord(
            stage=request.stage,
            label=request.label,
            prompt_version=request.prompt_version,
            requested_models=list(request.models),
            model=model,
            status=status,
            usage=usage,
            cost_usd=cost if cost is not None else (0.0 if usage.total == 0 else None),
            latency_s=round(latency, 3),
            retries=retries,
            generation_id=request.generation_id,
            error=error,
            response_id=response_id,
        )
