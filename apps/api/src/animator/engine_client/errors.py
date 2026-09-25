"""Errors raised by the engine worker client."""

from __future__ import annotations

from typing import Any


class EngineError(Exception):
    """Base class for engine worker failures."""


class WorkerCrashed(EngineError):
    """The worker process exited (or its pipes broke) while a call was in flight."""


class WorkerTimeout(EngineError):
    """A call exceeded its deadline. The worker is killed because it may be stuck."""


class ProtocolMismatch(EngineError):
    """The worker speaks a protocol major version these contracts do not understand."""


class EngineUnavailable(EngineError):
    """The pool exhausted its restart budget; callers should degrade gracefully."""


class EngineRpcError(EngineError):
    """The worker answered with a JSON-RPC error. Deterministic, so never retried."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.rpc_message = message
        self.data = data
