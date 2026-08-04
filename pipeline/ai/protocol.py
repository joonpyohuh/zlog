"""Provider-agnostic multimodal interface."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from pipeline.ai.schemas import AssetAnalysis, PlanEvaluation, StoryPlan
from pipeline.ai.usage import CallUsage


@runtime_checkable
class MultimodalProvider(Protocol):
    name: str

    def analyze_assets(
        self,
        *,
        image_paths: list[Path],
        segment_ids: list[str],
        source_files: list[str],
        allowed_segment_ids: set[str],
        model: str | None = None,
    ) -> tuple[list[AssetAnalysis], CallUsage]: ...

    def create_story_plan(
        self,
        *,
        user_intent: str,
        analyses: list[AssetAnalysis],
        allowed_segment_ids: set[str],
        target_duration_sec: float,
        model: str | None = None,
    ) -> tuple[StoryPlan, CallUsage]: ...

    def evaluate_plan(
        self,
        *,
        user_intent: str,
        plan: StoryPlan,
        analyses: list[AssetAnalysis],
        allowed_segment_ids: set[str],
        model: str | None = None,
    ) -> tuple[PlanEvaluation, CallUsage]: ...

    def repair_story_plan(
        self,
        *,
        user_intent: str,
        plan: StoryPlan,
        evaluation: PlanEvaluation,
        analyses: list[AssetAnalysis],
        allowed_segment_ids: set[str],
        model: str | None = None,
    ) -> tuple[StoryPlan, CallUsage]: ...
