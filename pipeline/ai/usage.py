"""Token / latency / cost metadata for every provider call."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ProviderName = Literal["anthropic", "openai"]


# Rough USD / 1M tokens — planning estimates only, not billing.
_RATE_PER_MTOK: dict[str, tuple[float, float]] = {
    # (input, output)
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-sonnet-4-5-20250929": (3.0, 15.0),
    "gpt-5.6-luna": (1.5, 6.0),
    "gpt-5.6-sol": (5.0, 20.0),
}


@dataclass
class CallUsage:
    provider: ProviderName
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0
    latency_ms: float = 0.0
    retry_count: int = 0
    estimated_cost_usd: float = 0.0
    operation: str = ""
    ok: bool = True
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def finalize_cost(self) -> None:
        inn, out = _RATE_PER_MTOK.get(self.model, (2.0, 8.0))
        self.estimated_cost_usd = round(
            (self.input_tokens / 1_000_000.0) * inn
            + (self.output_tokens / 1_000_000.0) * out
            + (self.cache_tokens / 1_000_000.0) * (inn * 0.1),
            6,
        )

    def as_dict(self) -> dict[str, Any]:
        self.finalize_cost()
        return asdict(self)


class UsageTimer:
    def __init__(self) -> None:
        self._t0 = time.perf_counter()

    def ms(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000.0, 2)


def record_usage(usage: CallUsage) -> CallUsage:
    """Hook for later structured logging — currently returns enriched usage."""
    usage.finalize_cost()
    return usage
