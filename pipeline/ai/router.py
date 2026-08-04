"""ModelRouter — env-driven provider selection without wiring the pipeline yet."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pipeline.ai.anthropic_provider import AnthropicProvider
from pipeline.ai.config import ModelConfig, load_model_config
from pipeline.ai.openai_provider import OpenAIProvider
from pipeline.ai.protocol import MultimodalProvider
from pipeline.ai.schemas import AssetAnalysis, PlanEvaluation, StoryPlan
from pipeline.ai.usage import CallUsage


class ModelRouter:
    """Routes analyze/director/evaluate/repair to configured providers."""

    def __init__(
        self,
        config: ModelConfig | None = None,
        *,
        anthropic: MultimodalProvider | None = None,
        openai: MultimodalProvider | None = None,
    ) -> None:
        self.config = config or load_model_config()
        self._anthropic = anthropic
        self._openai = openai

    def _provider(self, name: str) -> MultimodalProvider:
        if name == "anthropic":
            if self._anthropic is None:
                self._anthropic = AnthropicProvider(
                    default_model=self.config.director_model,
                )
            return self._anthropic
        if name == "openai":
            if self._openai is None:
                self._openai = OpenAIProvider(default_model=self.config.evaluator_model)
            return self._openai
        raise ValueError(f"unknown provider {name!r}")

    def analyzer_model_for_uncertainty(self, uncertainty: float) -> str:
        """Escalate to Sonnet when Haiku uncertainty is high."""
        if self.config.quality_mode == "max":
            return self.config.analyzer_escalation_model
        if uncertainty >= 0.55:
            return self.config.analyzer_escalation_model
        return self.config.analyzer_model

    def analyze_assets(
        self,
        *,
        image_paths: list[Path],
        segment_ids: list[str],
        source_files: list[str],
        allowed_segment_ids: set[str],
        escalate: bool = False,
    ) -> tuple[list[AssetAnalysis], CallUsage]:
        provider = self._provider(self.config.analyzer_provider)
        model = (
            self.config.analyzer_escalation_model
            if escalate
            else self.config.analyzer_model
        )
        return provider.analyze_assets(
            image_paths=image_paths,
            segment_ids=segment_ids,
            source_files=source_files,
            allowed_segment_ids=allowed_segment_ids,
            model=model,
        )

    def create_story_plan(
        self,
        *,
        user_intent: str,
        analyses: list[AssetAnalysis],
        allowed_segment_ids: set[str],
        target_duration_sec: float,
    ) -> tuple[StoryPlan, CallUsage]:
        provider = self._provider(self.config.director_provider)
        return provider.create_story_plan(
            user_intent=user_intent,
            analyses=analyses,
            allowed_segment_ids=allowed_segment_ids,
            target_duration_sec=target_duration_sec,
            model=self.config.director_model,
        )

    def evaluate_plan(
        self,
        *,
        user_intent: str,
        plan: StoryPlan,
        analyses: list[AssetAnalysis],
        allowed_segment_ids: set[str],
    ) -> tuple[PlanEvaluation, CallUsage]:
        provider = self._provider(self.config.evaluator_provider)
        return provider.evaluate_plan(
            user_intent=user_intent,
            plan=plan,
            analyses=analyses,
            allowed_segment_ids=allowed_segment_ids,
            model=self.config.evaluator_model,
        )

    def repair_story_plan(
        self,
        *,
        user_intent: str,
        plan: StoryPlan,
        evaluation: PlanEvaluation,
        analyses: list[AssetAnalysis],
        allowed_segment_ids: set[str],
    ) -> tuple[StoryPlan, CallUsage]:
        provider = self._provider(self.config.repair_provider)
        return provider.repair_story_plan(
            user_intent=user_intent,
            plan=plan,
            evaluation=evaluation,
            analyses=analyses,
            allowed_segment_ids=allowed_segment_ids,
            model=self.config.repair_model,
        )

    def describe(self) -> dict[str, Any]:
        c = self.config
        return {
            "analyzer": {"provider": c.analyzer_provider, "model": c.analyzer_model},
            "analyzer_escalation": {
                "provider": c.analyzer_provider,
                "model": c.analyzer_escalation_model,
            },
            "director": {"provider": c.director_provider, "model": c.director_model},
            "evaluator": {"provider": c.evaluator_provider, "model": c.evaluator_model},
            "repair": {"provider": c.repair_provider, "model": c.repair_model},
            "quality_mode": c.quality_mode,
        }
