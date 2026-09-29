"""Model prices and capabilities, loaded from config files (never constants in code)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from animator.llm.types import Usage

EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def _lookup[T](table: dict[str, T], model: str) -> T | None:
    """Exact id, else the longest configured prefix (dated snapshots like 'gpt-5-2025-08-07')."""
    if model in table:
        return table[model]
    best = max((k for k in table if model.startswith(k + "-")), key=len, default=None)
    return table[best] if best is not None else None


@dataclass(frozen=True)
class Price:
    input: float
    cached_input: float
    output: float


class PriceTable:
    def __init__(self, prices: dict[str, Price], tiers: dict[str, float] | None = None) -> None:
        self.prices = prices
        # Multiplier per processing tier (the tier a response says it was served on).
        self.tiers = tiers or {}

    @classmethod
    def load(cls, path: Path) -> PriceTable:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("unit") != "per_1m_tokens":
            raise ValueError(f"{path.name}: unit must be per_1m_tokens")
        return cls(
            {
                m: Price(float(p["input"]), float(p["cached_input"]), float(p["output"]))
                for m, p in data["models"].items()
            },
            {t: float(x) for t, x in data.get("tiers", {}).items()},
        )

    def cost(self, model: str, usage: Usage, tier: str | None = None) -> float | None:
        """USD for one call; None when the model has no configured price. Reasoning tokens are
        part of output tokens. `tier` is the processing tier the response was served on."""
        p = _lookup(self.prices, model)
        if p is None:
            return None
        uncached = max(0, usage.input_tokens - usage.cached_input_tokens)
        standard = (
            uncached * p.input + usage.cached_input_tokens * p.cached_input + usage.output_tokens * p.output
        ) / 1e6
        return standard * self.tiers.get(tier, 1.0) if tier else standard


@dataclass(frozen=True)
class ModelCaps:
    reasoning: bool = True
    efforts: tuple[str, ...] | None = None
    vision: bool = True
    max_output_tokens: int | None = None


class ModelCatalog:
    def __init__(self, caps: dict[str, ModelCaps]) -> None:
        self.caps = caps

    @classmethod
    def load(cls, path: Path) -> ModelCatalog:
        data = json.loads(path.read_text(encoding="utf-8"))
        out: dict[str, ModelCaps] = {}
        for m, c in data["models"].items():
            efforts = c.get("efforts")
            out[m] = ModelCaps(
                reasoning=bool(c.get("reasoning", True)),
                efforts=tuple(efforts) if efforts else None,
                vision=bool(c.get("vision", True)),
                max_output_tokens=c.get("max_output_tokens"),
            )
        return cls(out)

    def get(self, model: str) -> ModelCaps:
        return _lookup(self.caps, model) or ModelCaps()

    def effort_for(self, model: str, effort: str) -> str | None:
        """The effort to send, or None when the model takes no reasoning parameter.
        Unsupported levels map to the nearest supported one (ties go up)."""
        caps = self.get(model)
        if not caps.reasoning:
            return None
        if caps.efforts is None or effort in caps.efforts:
            return effort
        want = EFFORT_ORDER.index(effort) if effort in EFFORT_ORDER else EFFORT_ORDER.index("medium")
        ranked = [e for e in caps.efforts if e in EFFORT_ORDER]
        if not ranked:
            return None
        return min(ranked, key=lambda e: (abs(EFFORT_ORDER.index(e) - want), -EFFORT_ORDER.index(e)))
