"""LLM layer: provider-neutral types, the OpenAI client, fakes, record/replay, prices and budgets.

Nothing outside this package imports `openai`.
"""

from animator.llm.catalog import ModelCatalog, PriceTable
from animator.llm.errors import (
    BudgetExceeded,
    LLMAuthError,
    LLMBadOutput,
    LLMError,
    LLMIncomplete,
    LLMRefusal,
    LLMUnavailable,
)
from animator.llm.fake import FakeLLM, RecordingLLM, ReplayLLM
from animator.llm.types import (
    Budget,
    CallContext,
    CallRecord,
    ImagePart,
    LLMClient,
    LLMRequest,
    LLMResult,
    Message,
    TextPart,
    Usage,
)

__all__ = [
    "Budget",
    "BudgetExceeded",
    "CallContext",
    "CallRecord",
    "FakeLLM",
    "ImagePart",
    "LLMAuthError",
    "LLMBadOutput",
    "LLMClient",
    "LLMError",
    "LLMIncomplete",
    "LLMRefusal",
    "LLMRequest",
    "LLMResult",
    "LLMUnavailable",
    "Message",
    "ModelCatalog",
    "PriceTable",
    "RecordingLLM",
    "ReplayLLM",
    "TextPart",
    "Usage",
    "build_llm",
]


def build_llm() -> LLMClient:
    """The client selected by ANIMATOR_LLM_PROVIDER: live OpenAI, live with recording, or replay."""
    from animator.config import get_settings
    from animator.llm.openai_client import OpenAILLM

    s = get_settings()
    fixtures = s.resolve(s.animator_llm_fixtures_dir)
    if s.animator_llm_provider == "replay":
        return ReplayLLM(fixtures)
    live = OpenAILLM(
        s,
        PriceTable.load(s.resolve(s.animator_pricing_file)),
        ModelCatalog.load(s.resolve(s.animator_models_file)),
    )
    return RecordingLLM(live, fixtures) if s.animator_llm_provider == "record" else live
