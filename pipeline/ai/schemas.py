"""Pydantic contracts for hybrid Claude/GPT planning (PROMPT 1).

Models select existing segment_id / evidence_frame_id values only —
they never invent source timestamps. Timing remains FFmpeg/PySceneDetect.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class MediaType(str, Enum):
    image = "image"
    video = "video"
    still_video = "still_video"


class ShotType(str, Enum):
    wide = "wide"
    medium = "medium"
    close = "close"
    detail = "detail"
    unknown = "unknown"


class SceneKind(str, Enum):
    indoor = "indoor"
    outdoor = "outdoor"
    transit = "transit"
    food = "food"
    portrait = "portrait"
    activity = "activity"
    other = "other"


class Mood(str, Enum):
    bright = "bright"
    calm = "calm"
    lively = "lively"
    moody = "moody"
    nostalgic = "nostalgic"
    romantic = "romantic"
    energetic = "energetic"


class StylePreset(str, Enum):
    clean_vlog = "clean_vlog"
    y2k_camcorder = "y2k_camcorder"
    y2k_4x3_letterbox = "y2k_4x3_letterbox"  # alias of y2k_camcorder
    vertical_full = "vertical_full"
    cinematic_16x9 = "cinematic_16x9"
    soft_vlog = "soft_vlog"
    punchy_short = "punchy_short"


class TargetPlatform(str, Enum):
    youtube = "youtube"
    youtube_shorts = "youtube_shorts"
    instagram_reels = "instagram_reels"
    tiktok = "tiktok"
    generic = "generic"


class CaptionMode(str, Enum):
    none = "none"
    sparse = "sparse"
    dense = "dense"
    hook_only = "hook_only"


class ClipRole(str, Enum):
    opening = "opening"
    body = "body"
    peak = "peak"
    closing = "closing"
    bridge = "bridge"


class FitMode(str, Enum):
    cover = "cover"
    contain = "contain"
    smart_crop = "smart_crop"
    subject_aware_cover = "subject_aware_cover"
    blurred_background_contain = "blurred_background_contain"


class MotionKind(str, Enum):
    none = "none"
    ken_burns_in = "ken_burns_in"
    ken_burns_out = "ken_burns_out"
    pan_left = "pan_left"
    pan_right = "pan_right"
    static = "static"


class TransitionKind(str, Enum):
    cut = "cut"
    flash = "flash"
    dissolve = "dissolve"


class PreferredMoment(str, Enum):
    start = "start"
    middle = "middle"
    end = "end"
    peak_action = "peak_action"


class UnitInterval(BaseModel):
    """Marker mixin docs — scores use Field(ge=0, le=1) directly."""


def _score(default: float = 0.0) -> float:
    return Field(default=default, ge=0.0, le=1.0)


class AssetAnalysis(BaseModel):
    asset_id: str
    segment_id: str
    source_file: str
    media_type: MediaType
    upload_index: int = Field(ge=0)
    capture_time: str | None = None  # ISO8601 if known from EXIF; never invented
    evidence_frame_ids: list[str] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)
    scene: SceneKind = SceneKind.other
    shot_type: ShotType = ShotType.unknown
    action_progression: str = ""
    mood: Mood = Mood.calm
    technical_quality: float = _score(0.5)
    aesthetic_value: float = _score(0.5)
    emotional_value: float = _score(0.5)
    narrative_value: float = _score(0.5)
    hook_potential: float = _score(0.0)
    motion_quality: float = _score(0.0)
    novelty: float = _score(0.5)
    redundancy_group: str | None = None
    focus_x: float = _score(0.5)
    focus_y: float = _score(0.5)
    focus_width: float = Field(default=0.6, ge=0.0, le=1.0)
    focus_height: float = Field(default=0.6, ge=0.0, le=1.0)
    crop_confidence: float = _score(0.0)
    visually_grounded_facts: list[str] = Field(default_factory=list)
    uncertainty: float = _score(0.0)
    analysis_provider: Literal["anthropic", "openai"]
    analysis_model: str

    @field_validator("segment_id", "asset_id")
    @classmethod
    def _non_empty_id(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("id must be non-empty")
        return v.strip()

    @field_validator("evidence_frame_ids")
    @classmethod
    def _frame_ids_non_empty_strings(cls, v: list[str]) -> list[str]:
        out = [x.strip() for x in v if x and str(x).strip()]
        return out


class PlannedClip(BaseModel):
    segment_id: str
    role: ClipRole = ClipRole.body
    evidence_frame_ids: list[str] = Field(default_factory=list)
    preferred_moment: PreferredMoment = PreferredMoment.middle
    target_duration_sec: float = Field(ge=0.2, le=30.0)
    fit_mode: FitMode = FitMode.cover
    focus_x: float = _score(0.5)
    focus_y: float = _score(0.5)
    motion: MotionKind = MotionKind.ken_burns_in
    motion_strength: float = _score(0.35)
    transition: TransitionKind = TransitionKind.cut
    caption: str = ""
    caption_grounding: str = ""
    overlay: str | None = None
    reuse_reason: str | None = None

    @field_validator("segment_id")
    @classmethod
    def _segment_required(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("segment_id must be non-empty")
        return v.strip()


class StoryPlan(BaseModel):
    user_intent: str
    concept: str
    tone: Mood = Mood.calm
    target_platform: TargetPlatform = TargetPlatform.youtube
    target_duration_sec: float = Field(ge=3.0, le=120.0)
    style_preset: StylePreset = StylePreset.clean_vlog
    hook_segment_id: str
    ending_segment_id: str
    selected_segment_ids: list[str] = Field(min_length=1)
    narrative_arc: list[str] = Field(default_factory=list)
    caption_mode: CaptionMode = CaptionMode.sparse
    allow_asset_reuse: bool = False
    music_requirements: str = ""
    reasoning_summary: str = ""
    confidence: float = _score(0.5)
    provider: Literal["anthropic", "openai"]
    model: str

    @model_validator(mode="after")
    def _ids_consistent(self) -> StoryPlan:
        selected = set(self.selected_segment_ids)
        if self.hook_segment_id not in selected:
            raise ValueError(
                f"hook_segment_id {self.hook_segment_id!r} not in selected_segment_ids"
            )
        if self.ending_segment_id not in selected:
            raise ValueError(
                f"ending_segment_id {self.ending_segment_id!r} not in selected_segment_ids"
            )
        return self


class TimelinePlan(BaseModel):
    project: str
    story_plan_version: str = "1"
    clips: list[PlannedClip] = Field(min_length=1)
    total_target_duration_sec: float = Field(ge=3.0, le=120.0)
    style_preset: StylePreset = StylePreset.clean_vlog

    @model_validator(mode="after")
    def _clip_segments_unique_order(self) -> TimelinePlan:
        ids = [c.segment_id for c in self.clips]
        if not ids:
            raise ValueError("clips must not be empty")
        return self


class EvaluationFailure(BaseModel):
    code: str
    message: str
    segment_id: str | None = None


class RecommendedChange(BaseModel):
    action: Literal[
        "drop_segment",
        "add_segment",
        "reorder",
        "rewrite_caption",
        "change_style",
        "shorten",
        "lengthen",
        "other",
    ]
    segment_id: str | None = None
    detail: str = ""


class PlanEvaluation(BaseModel):
    overall_score: float = _score()
    narrative_coherence: float = _score()
    hook_strength: float = _score()
    redundancy_score: float = _score()
    chronology_score: float = _score()
    caption_grounding: float = _score()
    prompt_leakage: float = _score()
    crop_safety: float = _score()
    visual_variety: float = _score()
    duration_suitability: float = _score()
    failures: list[EvaluationFailure] = Field(default_factory=list)
    recommended_changes: list[RecommendedChange] = Field(default_factory=list)
    requires_revision: bool = False
    evaluator_provider: Literal["anthropic", "openai"]
    evaluator_model: str


def assert_known_segment_ids(
    segment_ids: list[str],
    allowed: set[str],
    *,
    context: str,
) -> None:
    """Reject any model-proposed segment_id outside the candidate set."""
    unknown = sorted({s for s in segment_ids if s not in allowed})
    if unknown:
        raise ValueError(f"{context}: unknown segment_id(s): {unknown}")


def story_plan_against_candidates(plan: StoryPlan, allowed: set[str]) -> None:
    assert_known_segment_ids(plan.selected_segment_ids, allowed, context="StoryPlan")
    assert_known_segment_ids(
        [plan.hook_segment_id, plan.ending_segment_id],
        allowed,
        context="StoryPlan anchors",
    )


def timeline_against_candidates(plan: TimelinePlan, allowed: set[str]) -> None:
    assert_known_segment_ids(
        [c.segment_id for c in plan.clips],
        allowed,
        context="TimelinePlan",
    )
