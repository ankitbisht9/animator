"""Fake and record/replay `LLMClient`s: the offline test suite never calls OpenAI."""

from __future__ import annotations

import inspect
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from animator.llm.errors import LLMError
from animator.llm.types import CallContext, CallRecord, LLMClient, LLMRequest, LLMResult, Usage

Handler = Callable[[LLMRequest], "dict[str, Any] | Exception | Awaitable[dict[str, Any] | Exception]"]


class FakeLLM:
    """Scripted responses: `handler(request)` returns the output object, or an exception to raise."""

    def __init__(
        self, handler: Handler, usage: Usage | None = None, model: str = "fake-model", cost: float = 0.0
    ) -> None:
        self.handler = handler
        self.usage = usage or Usage(input_tokens=1000, cached_input_tokens=0, output_tokens=500)
        self.model = model
        self.cost = cost
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest, ctx: CallContext | None = None) -> LLMResult:
        ctx = ctx or CallContext()
        if ctx.budget is not None:
            try:
                ctx.budget.check()
            except LLMError as e:
                ctx.emit(_record(request, None, "budget", Usage(), 0.0, str(e)))
                raise
        self.requests.append(request)
        out = self.handler(request)
        if inspect.isawaitable(out):
            out = await out
        if isinstance(out, Exception):
            status = getattr(out, "kind", "error")
            ctx.emit(_record(request, self.model, status, self.usage, self.cost, str(out)))
            raise out
        text = json.dumps(out)
        feed(ctx, text)
        ctx.emit(_record(request, self.model, "ok", self.usage, self.cost, None))
        return LLMResult(json.loads(text), self.model, self.usage, self.cost, 0.0, 0, [self.model])


def feed(ctx: CallContext, text: str, chunk: int = 40) -> None:
    """Stream the output to the context's sink in small pieces, as a live response would."""
    if ctx.stream is not None:
        ctx.stream.reset()
        for i in range(0, len(text), chunk):
            ctx.stream.feed(text[i : i + chunk])


def _record(
    request: LLMRequest, model: str | None, status: Any, usage: Usage, cost: float, error: str | None
) -> CallRecord:
    return CallRecord(
        stage=request.stage,
        label=request.label,
        prompt_version=request.prompt_version,
        requested_models=list(request.models),
        model=model,
        status=status,
        usage=usage,
        cost_usd=cost,
        latency_s=0.0,
        retries=0,
        generation_id=request.generation_id,
        error=error,
    )


class RecordingLLM:
    """Wraps a live client and saves every successful call as a replay fixture."""

    def __init__(self, inner: LLMClient, directory: Path) -> None:
        self.inner = inner
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    async def generate(self, request: LLMRequest, ctx: CallContext | None = None) -> LLMResult:
        result = await self.inner.generate(request, ctx)
        fp = request.fingerprint()
        n = len(list(self.directory.glob("*.json")))
        path = self.directory / f"{n:03d}-{request.stage}-{fp[:12]}.json"
        path.write_text(
            json.dumps(
                {
                    "fingerprint": fp,
                    "stage": request.stage,
                    "label": request.label,
                    "prompt_version": request.prompt_version,
                    "model": result.model,
                    "usage": result.usage.__dict__,
                    "cost_usd": result.cost_usd,
                    "data": result.data,
                },
                indent=1,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return result


class ReplayLLM:
    """Answers from recorded fixtures by request fingerprint; an unknown request fails loudly."""

    def __init__(self, directory: Path) -> None:
        self.recordings: dict[str, dict[str, Any]] = {}
        for p in sorted(directory.glob("*.json")):
            rec = json.loads(p.read_text(encoding="utf-8"))
            self.recordings[rec["fingerprint"]] = rec
        self.directory = directory

    async def generate(self, request: LLMRequest, ctx: CallContext | None = None) -> LLMResult:
        ctx = ctx or CallContext()
        rec = self.recordings.get(request.fingerprint())
        if rec is None:
            err = LLMError(
                f"no recording for {request.stage} '{request.label}' in {self.directory.name} "
                "(prompt or inputs changed: re-record with a live key)"
            )
            ctx.emit(_record(request, None, "error", Usage(), 0.0, str(err)))
            raise err
        usage = Usage(**rec["usage"])
        started = time.perf_counter()
        feed(ctx, json.dumps(rec["data"], ensure_ascii=False))
        ctx.emit(_record(request, rec["model"], "ok", usage, float(rec.get("cost_usd") or 0.0), None))
        return LLMResult(
            rec["data"],
            rec["model"],
            usage,
            rec.get("cost_usd"),
            time.perf_counter() - started,
            0,
            [rec["model"]],
        )
