"""Hybrid AI contracts — schemas + multimodal providers.

Not yet wired into server.py / select_ai.py. Existing Claude paths keep
running unchanged until a later prompt swaps them in.
"""

from pipeline.ai.config import ModelConfig, load_model_config
from pipeline.ai.protocol import MultimodalProvider
from pipeline.ai.router import ModelRouter
from pipeline.ai.schemas import (
    AssetAnalysis,
    PlanEvaluation,
    PlannedClip,
    StoryPlan,
    TimelinePlan,
)
from pipeline.ai.usage import CallUsage

__all__ = [
    "AssetAnalysis",
    "CallUsage",
    "ModelConfig",
    "ModelRouter",
    "MultimodalProvider",
    "PlanEvaluation",
    "PlannedClip",
    "StoryPlan",
    "TimelinePlan",
    "load_model_config",
]
