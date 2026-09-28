"""LLM failures the pipeline distinguishes. Refusals, truncation and bad output count as failed
attempts (spec Section 17); auth and budget errors stop the generation."""

from __future__ import annotations


class LLMError(RuntimeError):
    """Base class. `kind` is what the call log records."""

    kind = "error"


class LLMRefusal(LLMError):
    kind = "refusal"


class LLMIncomplete(LLMError):
    """The response was cut off (output token limit or content filter)."""

    kind = "incomplete"

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


class LLMBadOutput(LLMError):
    """Not a JSON object, or not valid JSON."""

    kind = "bad_output"


class LLMUnavailable(LLMError):
    """No configured model could be reached (not available to this key, quota, outage)."""

    kind = "unavailable"


class LLMAuthError(LLMError):
    """The API key is missing, invalid, or its account is deactivated. Never retried."""

    kind = "auth"


class BudgetExceeded(LLMError):
    kind = "budget"
