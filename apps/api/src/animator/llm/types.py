"""Provider-neutral LLM request/response types. No OpenAI types leave `animator.llm`."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    """A PNG image (the visual critic's keyframes)."""

    png: bytes
    detail: Literal["low", "high", "auto"] = "auto"

    def data_url(self) -> str:
        return "data:image/png;base64," + base64.b64encode(self.png).decode("ascii")


Part = TextPart | ImagePart


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "developer"]
    parts: tuple[Part, ...]

    @staticmethod
    def user(*parts: str | Part) -> Message:
        return Message("user", tuple(TextPart(p) if isinstance(p, str) else p for p in parts))


@dataclass
class LLMRequest:
    """One structured-output call. Static content goes in `instructions` (cacheable prefix),
    per-call content in `messages` (spec Section 4: static first, dynamic last)."""

    stage: str
    prompt_version: str
    instructions: str
    messages: list[Message]
    schema_name: str
    schema: dict[str, Any]
    models: list[str]
    effort: str
    max_output_tokens: int
    generation_id: str | None = None
    label: str = ""
    cache_key: str | None = None

    def fingerprint(self) -> str:
        """Content identity used by record/replay (independent of model choice)."""
        msgs = [
            {
                "role": m.role,
                "parts": [
                    {"text": p.text}
                    if isinstance(p, TextPart)
                    else {"png": hashlib.sha256(p.png).hexdigest()}
                    for p in m.parts
                ],
            }
            for m in self.messages
        ]
        payload = {
            "stage": self.stage,
            "prompt_version": self.prompt_version,
            "instructions": self.instructions,
            "messages": msgs,
            "schema_name": self.schema_name,
            "schema": self.schema,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()


@dataclass
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class LLMResult:
    data: dict[str, Any]
    model: str
    usage: Usage
    cost_usd: float | None
    latency_s: float
    retries: int = 0
    models_tried: list[str] = field(default_factory=list)
    response_id: str | None = None


CallStatus = Literal["ok", "refusal", "incomplete", "bad_output", "unavailable", "auth", "error", "budget"]


@dataclass
class CallRecord:
    """One line of a generation's LLM call log (observability, cost accounting)."""

    stage: str
    label: str
    prompt_version: str
    requested_models: list[str]
    model: str | None
    status: CallStatus
    usage: Usage
    cost_usd: float | None
    latency_s: float
    retries: int
    generation_id: str | None = None
    error: str | None = None
    response_id: str | None = None
    ts: float = field(default_factory=time.time)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class Budget:
    """Per-generation hard stop on cost and tokens (spec Section 4)."""

    def __init__(self, max_usd: float, max_tokens: int) -> None:
        self.max_usd = max_usd
        self.max_tokens = max_tokens
        self.spent_usd = 0.0
        self.spent_tokens = 0

    def check(self) -> None:
        from animator.llm.errors import BudgetExceeded

        if self.spent_usd >= self.max_usd:
            raise BudgetExceeded(f"cost budget reached: ${self.spent_usd:.2f} of ${self.max_usd:.2f}")
        if self.spent_tokens >= self.max_tokens:
            raise BudgetExceeded(f"token budget reached: {self.spent_tokens} of {self.max_tokens}")

    def add(self, usage: Usage, cost_usd: float | None) -> None:
        self.spent_tokens += usage.total
        self.spent_usd += cost_usd or 0.0


class TextSink(Protocol):
    """Receives a response's output text while it streams in (so work can start before it ends)."""

    def reset(self) -> None:
        """A new attempt starts: the text fed so far is void."""

    def feed(self, text: str) -> None: ...


@dataclass
class CallContext:
    budget: Budget | None = None
    on_record: Callable[[CallRecord], None] | None = None
    stream: TextSink | None = None

    def streaming(self, sink: TextSink) -> CallContext:
        """The same context (budget, records) for one call whose output text goes to `sink` as it
        streams in."""
        return CallContext(self.budget, self.on_record, sink)

    def emit(self, record: CallRecord) -> None:
        if self.budget is not None:
            self.budget.add(record.usage, record.cost_usd)
        if self.on_record is not None:
            self.on_record(record)


class LLMClient(Protocol):
    async def generate(self, request: LLMRequest, ctx: CallContext | None = None) -> LLMResult: ...
