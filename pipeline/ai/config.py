"""Central model/provider configuration — IDs live here / in env only."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from dotenv import load_dotenv

ProviderName = Literal["anthropic", "openai"]
# premium = Sol repair path; max is treated as premium alias.
QualityMode = Literal["economy", "balanced", "max", "premium"]


@dataclass(frozen=True)
class ModelConfig:
    analyzer_provider: ProviderName
    analyzer_model: str
    analyzer_escalation_model: str
    director_provider: ProviderName
    director_model: str
    evaluator_provider: ProviderName
    evaluator_model: str
    repair_provider: ProviderName
    repair_model: str
    quality_mode: QualityMode


def _provider(name: str, default: ProviderName) -> ProviderName:
    value = (os.getenv(name) or default).strip().lower()
    if value not in ("anthropic", "openai"):
        raise ValueError(f"{name} must be anthropic|openai, got {value!r}")
    return value  # type: ignore[return-value]


def _quality(name: str, default: QualityMode) -> QualityMode:
    value = (os.getenv(name) or default).strip().lower()
    if value not in ("economy", "balanced", "max", "premium"):
        raise ValueError(f"{name} must be economy|balanced|max|premium, got {value!r}")
    return value  # type: ignore[return-value]


def is_premium_mode(mode: QualityMode | str) -> bool:
    return str(mode).strip().lower() in ("premium", "max")


def load_model_config(*, load_env: bool = True) -> ModelConfig:
    if load_env:
        load_dotenv()
    return ModelConfig(
        analyzer_provider=_provider("ZLOG_ANALYZER_PROVIDER", "anthropic"),
        analyzer_model=os.getenv("ZLOG_ANALYZER_MODEL", "claude-haiku-4-5-20251001"),
        analyzer_escalation_model=os.getenv(
            "ZLOG_ANALYZER_ESCALATION_MODEL",
            "claude-sonnet-4-5-20250929",
        ),
        director_provider=_provider("ZLOG_DIRECTOR_PROVIDER", "anthropic"),
        director_model=os.getenv("ZLOG_DIRECTOR_MODEL", "claude-sonnet-4-5-20250929"),
        evaluator_provider=_provider("ZLOG_EVALUATOR_PROVIDER", "openai"),
        evaluator_model=os.getenv("ZLOG_EVALUATOR_MODEL", "gpt-5.6-luna"),
        repair_provider=_provider("ZLOG_REPAIR_PROVIDER", "openai"),
        repair_model=os.getenv("ZLOG_REPAIR_MODEL", "gpt-5.6-sol"),
        quality_mode=_quality("ZLOG_QUALITY_MODE", "balanced"),
    )
