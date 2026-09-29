"""OpenAI implementation of `LLMClient` (Responses API, strict Structured Outputs).

- Stateless calls: the full context is sent every time; `store` is configurable.
- Model chain: the stage's model first, then ANIMATOR_MODEL_FALLBACKS. A model this key cannot use
  (404/403/model_not_found) is skipped and remembered for the life of the process.
- Retries: exponential backoff with jitter on 429/5xx/timeouts/connection errors, honouring
  Retry-After. An invalid or deactivated key and exhausted quota are never retried.
- Refusals and truncated (`incomplete`) responses raise, and count as failed attempts upstream.
- Every call, successful or not, is reported with its usage and cost through `CallContext`.
The API key is never logged and never leaves this module.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2 as httpx
import openai
from openai import AsyncOpenAI

from animator.config import Settings
from animator.llm.catalog import ModelCatalog, PriceTable
from animator.llm.errors import (
    LLMAuthError,
    LLMBadOutput,
    LLMError,
    LLMIncomplete,
    LLMRefusal,
    LLMUnavailable,
)
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


class _ModelUnavailable(Exception):
    pass


class _StreamBroken(Exception):
    """A streamed response ended without a final response (retried like a dropped connection)."""


def _message_of(e: openai.APIStatusError) -> str:
    body = e.body if isinstance(e.body, dict) else {}
    err = body.get("error", body) if isinstance(body, dict) else {}
    msg = err.get("message") if isinstance(err, dict) else None
    return str(msg or e.message or e)[:400]


def _code_of(e: openai.APIStatusError) -> str:
    code = getattr(e, "code", None)
    if code:
        return str(code)
    body = e.body if isinstance(e.body, dict) else {}
    err = body.get("error", {}) if isinstance(body, dict) else {}
    return str(err.get("code") or "") if isinstance(err, dict) else ""


def _is_model_problem(e: openai.APIStatusError) -> bool:
    text = (_code_of(e) + " " + _message_of(e)).lower()
    return "model_not_found" in text or (
        "model" in text
        and any(s in text for s in ("does not exist", "not found", "do not have access", "not available"))
    )


def _retry_after(e: Exception) -> float | None:
    resp = getattr(e, "response", None)
    if not isinstance(resp, httpx.Response):
        return None
    raw = resp.headers.get("retry-after-ms")
    if raw:
        try:
            return float(raw) / 1000
        except ValueError:
            pass
    raw = resp.headers.get("retry-after")
    if raw:
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def _to_input(m: Message) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for p in m.parts:
        if isinstance(p, TextPart):
            content.append({"type": "input_text", "text": p.text})
        elif isinstance(p, ImagePart):
            content.append({"type": "input_image", "image_url": p.data_url(), "detail": p.detail})
    return {"role": m.role, "content": content}


def _usage(resp: Any) -> Usage:
    u = getattr(resp, "usage", None)
    if u is None:
        return Usage()
    in_details = getattr(u, "input_tokens_details", None)
    out_details = getattr(u, "output_tokens_details", None)
    return Usage(
        input_tokens=int(getattr(u, "input_tokens", 0) or 0),
        cached_input_tokens=int(getattr(in_details, "cached_tokens", 0) or 0),
        output_tokens=int(getattr(u, "output_tokens", 0) or 0),
        reasoning_tokens=int(getattr(out_details, "reasoning_tokens", 0) or 0),
    )


def _refusal(resp: Any) -> str | None:
    for item in getattr(resp, "output", None) or []:
        for c in getattr(item, "content", None) or []:
            if getattr(c, "type", None) == "refusal":
                return str(getattr(c, "refusal", "") or "refused")
    return None


def _output_text(resp: Any) -> str:
    parts: list[str] = []
    for item in getattr(resp, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for c in getattr(item, "content", None) or []:
            if getattr(c, "type", None) == "output_text":
                parts.append(str(getattr(c, "text", "")))
    return "".join(parts)


class OpenAILLM:
    def __init__(
        self,
        settings: Settings,
        prices: PriceTable,
        models: ModelCatalog,
        http_client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.prices = prices
        self.models = models
        self.max_retries = settings.openai_max_retries
        self._sleep = sleep
        self._sem = asyncio.Semaphore(settings.llm_max_concurrency)
        self.unavailable: dict[str, str] = {}
        self._client: AsyncOpenAI | None = None
        if settings.has_openai_key:
            assert settings.openai_api_key is not None
            self._client = AsyncOpenAI(
                api_key=settings.openai_api_key.get_secret_value().strip(),
                organization=(settings.openai_org_id or None),
                project=(settings.openai_project_id or None),
                base_url=(settings.openai_base_url or None),
                timeout=settings.openai_timeout_s,
                max_retries=0,  # retries are ours: counted, logged, Retry-After aware
                http_client=http_client,
            )

    async def generate(self, request: LLMRequest, ctx: CallContext | None = None) -> LLMResult:
        ctx = ctx or CallContext()
        if ctx.budget is not None:
            try:
                ctx.budget.check()
            except LLMError as e:
                ctx.emit(self._record(request, None, "budget", Usage(), 0.0, 0, str(e)))
                raise
        if self._client is None:
            err = LLMAuthError("OPENAI_API_KEY is not set: add it to .env to generate lessons")
            ctx.emit(self._record(request, None, "auth", Usage(), 0.0, 0, str(err)))
            raise err
        tried: list[str] = []
        started = time.perf_counter()
        for model in request.models:
            if model in self.unavailable:
                continue
            tried.append(model)
            try:
                return await self._call_model(model, request, ctx, tried, started)
            except _ModelUnavailable as e:
                self.unavailable[model] = str(e)
                continue
        detail = "; ".join(f"{m}: {why}" for m, why in self.unavailable.items() if m in request.models)
        none_left = LLMUnavailable(
            f"none of the configured models is available to this API key ({detail or 'none tried'})"
        )
        ctx.emit(
            self._record(
                request, None, "unavailable", Usage(), time.perf_counter() - started, 0, str(none_left)
            )
        )
        raise none_left

    # ------------------------------------------------------------------ one model

    def _params(self, model: str, request: LLMRequest, with_reasoning: bool) -> dict[str, Any]:
        caps = self.models.get(model)
        max_out = request.max_output_tokens
        if caps.max_output_tokens is not None:
            max_out = min(max_out, caps.max_output_tokens)
        params: dict[str, Any] = {
            "model": model,
            "instructions": request.instructions,
            "input": [_to_input(m) for m in request.messages],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": request.schema_name,
                    "schema": request.schema,
                    "strict": True,
                }
            },
            "max_output_tokens": max_out,
            "store": self.settings.openai_store,
        }
        effort = self.models.effort_for(model, request.effort) if with_reasoning else None
        if effort is not None:
            params["reasoning"] = {"effort": effort}
        if request.cache_key:
            params["prompt_cache_key"] = request.cache_key
        if self.settings.openai_service_tier:
            params["service_tier"] = self.settings.openai_service_tier
        if request.generation_id:
            params["metadata"] = {"generation_id": request.generation_id, "stage": request.stage}
        return params

    async def _call_model(
        self, model: str, request: LLMRequest, ctx: CallContext, tried: list[str], started: float
    ) -> LLMResult:
        assert self._client is not None
        retries = 0
        with_reasoning = True
        while True:
            try:
                async with self._sem:
                    params = self._params(model, request, with_reasoning)
                    if ctx.stream is None:
                        resp = await self._client.responses.create(**params)
                    else:
                        resp = await self._stream(params, ctx)
                break
            except openai.AuthenticationError as e:
                err = LLMAuthError(f"OpenAI rejected the API key: {_message_of(e)}")
                ctx.emit(
                    self._record(
                        request, model, "auth", Usage(), time.perf_counter() - started, retries, str(err)
                    )
                )
                raise err from e
            except (openai.NotFoundError, openai.PermissionDeniedError) as e:
                if _is_model_problem(e) or isinstance(e, openai.NotFoundError):
                    raise _ModelUnavailable(_message_of(e)) from e
                err2 = LLMUnavailable(f"OpenAI refused access: {_message_of(e)}")
                ctx.emit(
                    self._record(
                        request,
                        model,
                        "unavailable",
                        Usage(),
                        time.perf_counter() - started,
                        retries,
                        str(err2),
                    )
                )
                raise err2 from e
            except openai.BadRequestError as e:
                msg = _message_of(e)
                if _is_model_problem(e):
                    raise _ModelUnavailable(msg) from e
                if with_reasoning and "reasoning" in msg.lower():
                    with_reasoning = False  # this model takes no reasoning parameter
                    continue
                err3 = LLMError(f"OpenAI rejected the request: {msg}")
                ctx.emit(
                    self._record(
                        request, model, "error", Usage(), time.perf_counter() - started, retries, str(err3)
                    )
                )
                raise err3 from e
            except (
                openai.RateLimitError,
                openai.InternalServerError,
                openai.APIConnectionError,
                openai.APITimeoutError,
                openai.APIStatusError,
                _StreamBroken,
            ) as e:
                status = getattr(e, "status_code", None)
                if isinstance(e, openai.RateLimitError) and _code_of(e) == "insufficient_quota":
                    err4 = LLMUnavailable(f"OpenAI quota exhausted: {_message_of(e)}")
                    ctx.emit(
                        self._record(
                            request,
                            model,
                            "unavailable",
                            Usage(),
                            time.perf_counter() - started,
                            retries,
                            str(err4),
                        )
                    )
                    raise err4 from e
                retryable = status is None or status in (408, 409, 429) or status >= 500
                if not retryable or retries >= self.max_retries:
                    text = _message_of(e) if isinstance(e, openai.APIStatusError) else str(e)
                    err5 = LLMUnavailable(f"OpenAI call failed after {retries} retries: {text}")
                    ctx.emit(
                        self._record(
                            request,
                            model,
                            "unavailable",
                            Usage(),
                            time.perf_counter() - started,
                            retries,
                            str(err5),
                        )
                    )
                    raise err5 from e
                delay = _retry_after(e)
                if delay is None:
                    delay = min(60.0, 1.5 * 2**retries) * random.uniform(0.5, 1.5)  # noqa: S311 - jitter
                retries += 1
                await self._sleep(delay)
        return self._finish(model, request, ctx, resp, tried, started, retries)

    async def _stream(self, params: dict[str, Any], ctx: CallContext) -> Any:
        """Stream the response, feeding its output text to `ctx.stream`; returns the final response."""
        assert self._client is not None and ctx.stream is not None
        ctx.stream.reset()
        final: Any = None
        stream = await self._client.responses.create(**params, stream=True)
        async for event in stream:
            kind = getattr(event, "type", "")
            if kind == "response.output_text.delta":
                ctx.stream.feed(event.delta)
            elif kind in ("response.completed", "response.incomplete"):
                final = event.response
            elif kind in ("response.failed", "error"):
                failed = getattr(getattr(event, "response", None), "error", None)
                raise _StreamBroken(f"streamed response failed: {failed or getattr(event, 'message', '')}")
        if final is None:
            raise _StreamBroken("stream ended without a final response")
        return final

    def _finish(
        self,
        model: str,
        request: LLMRequest,
        ctx: CallContext,
        resp: Any,
        tried: list[str],
        started: float,
        retries: int,
    ) -> LLMResult:
        usage = _usage(resp)
        served = str(getattr(resp, "model", None) or model)
        cost = self.prices.cost(served, usage, getattr(resp, "service_tier", None))
        latency = time.perf_counter() - started
        rid = getattr(resp, "id", None)

        def fail(status: Any, err: LLMError) -> LLMError:
            ctx.emit(self._record(request, served, status, usage, latency, retries, str(err), cost, rid))
            return err

        if getattr(resp, "status", None) == "incomplete":
            details = getattr(resp, "incomplete_details", None)
            reason = str(getattr(details, "reason", None) or "unknown")
            raise fail("incomplete", LLMIncomplete(f"response cut off ({reason})", reason))
        refusal = _refusal(resp)
        if refusal is not None:
            raise fail("refusal", LLMRefusal(f"model refused: {refusal[:300]}"))
        text = _output_text(resp)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise fail("bad_output", LLMBadOutput(f"output is not valid JSON ({e.msg} at {e.pos})")) from e
        if not isinstance(data, dict):
            raise fail("bad_output", LLMBadOutput("output is not a JSON object"))
        ctx.emit(self._record(request, served, "ok", usage, latency, retries, None, cost, rid))
        return LLMResult(data, served, usage, cost, latency, retries, list(tried), rid)

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
