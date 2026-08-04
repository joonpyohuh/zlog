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
    """Soft Flow roles (bible v0.1) + legacy aliases for older EDLs."""

    # Soft Flow
    hook = "hook"
    orientation = "orientation"
    development = "development"
    zlog_moment = "zlog_moment"
    release = "release"
    resonance = "resonance"
    # Legacy (map via canonical_clip_role)
    opening = "opening"
    body = "body"
    peak = "peak"
    closing = "closing"
    bridge = "bridge"


_LEGACY_ROLE_TO_SOFT: dict[ClipRole, ClipRole] = {
    ClipRole.opening: ClipRole.hook,
    ClipRole.body: ClipRole.development,
    ClipRole.peak: ClipRole.zlog_moment,
    ClipRole.bridge: ClipRole.release,
    ClipRole.closing: ClipRole.resonance,
}


def canonical_clip_role(role: ClipRole) -> ClipRole:
    """Normalize legacy roles onto Soft Flow names."""
    return _LEGACY_ROLE_TO_SOFT.get(role, role)


class VideoPurpose(str, Enum):
    daily_vlog = "daily_vlog"
    travel_vlog = "travel_vlog"
    comedy_vlog = "comedy_vlog"
    emotional_vlog = "emotional_vlog"
    product_brand = "product_brand"
    generic = "generic"


class CaptionStrategy(str, Enum):
    none = "none"
    sparse = "sparse"
    contextual = "contextual"
    resonance = "resonance"


class AudioStrategy(BaseModel):
    preserve_source_audio: bool = True
    bgm_duck: bool = False
    reason: str = ""


class EffectStrategy(BaseModel):
    effect: str = "none"
    reason: str = ""


class EffectId(str, Enum):
    clean_cut = "clean_cut"
    micro_push_in = "micro_push_in"
    micro_pull_out = "micro_pull_out"
    reaction_punch_in = "reaction_punch_in"
    blur_caption_focus = "blur_caption_focus"
    freeze_reaction_hold = "freeze_reaction_hold"
    soft_reveal = "soft_reveal"
    ambient_outro = "ambient_outro"


class ReasonCode(str, Enum):
    hook_potential = "HOOK_POTENTIAL"
    reaction_visible = "REACTION_VISIBLE"
    important_caption = "IMPORTANT_CAPTION"
    spatial_orientation = "SPATIAL_ORIENTATION"
    natural_audio_priority = "NATURAL_AUDIO_PRIORITY"
    ending_breath = "ENDING_BREATH"
    callback_payoff = "CALLBACK_PAYOFF"
    low_crop_confidence = "LOW_CROP_CONFIDENCE"
    no_effect_needed = "NO_EFFECT_NEEDED"
    narrative_payoff = "NARRATIVE_PAYOFF"


class CreativeMotion(BaseModel):
    type: Literal["none", "micro_push_in", "micro_pull_out"] = "none"
    strength: float = Field(default=0.0, ge=0.0, le=1.0)
    focus_x: float = Field(default=0.5, ge=0.0, le=1.0)
    focus_y: float = Field(default=0.5, ge=0.0, le=1.0)


class CreativeCaption(BaseModel):
    mode: CaptionStrategy = CaptionStrategy.none
    text: str = ""
    reason: str = ""


class CreativeAudio(BaseModel):
    preserve_source: bool = False
    duck_bgm: bool = False
    reason: str = ""


class CreativeDecision(BaseModel):
    segment_id: str
    narrative_role: ClipRole
    selection_reasons: list[ReasonCode] = Field(default_factory=list)
    cut_reason: str
    entry_effect: EffectId = EffectId.clean_cut
    primary_effect: EffectId = EffectId.clean_cut
    exit_effect: EffectId = EffectId.clean_cut
    motion: CreativeMotion = Field(default_factory=CreativeMotion)
    caption: CreativeCaption = Field(default_factory=CreativeCaption)
    audio: CreativeAudio = Field(default_factory=CreativeAudio)
    reuse_reason: Literal[
        "opening_callback", "visual_motif_callback", "narrative_payoff"
    ] | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class CreativeCallback(BaseModel):
    enabled: bool = False
    source_segment_id: str | None = None
    target_position: Literal["ending"] = "ending"
    reuse_reason: Literal[
        "opening_callback", "visual_motif_callback", "narrative_payoff"
    ] | None = None
    alternate_crop: bool = False

    @model_validator(mode="after")
    def _reason_required(self) -> CreativeCallback:
        if self.enabled and (not self.source_segment_id or not self.reuse_reason):
            raise ValueError("enabled callback requires source_segment_id and reuse_reason")
        return self


class CreativeEffectBudget(BaseModel):
    strong_effects_max: int = Field(default=2, ge=0)
    medium_effects_max: int = Field(default=3, ge=0)
    caption_focus_effects_max: int = Field(default=1, ge=0)


class CreativeExecutionPlan(BaseModel):
    project: str
    version: str = "1"
    creative_intent: str
    opening_strategy: Literal["cold_open", "soft_reveal", "clean_open"]
    ending_strategy: Literal["ambient_hold", "micro_pull_out", "clean_end"]
    callback: CreativeCallback = Field(default_factory=CreativeCallback)
    effect_budget: CreativeEffectBudget = Field(default_factory=CreativeEffectBudget)
    decisions: list[CreativeDecision] = Field(min_length=1)

    @model_validator(mode="after")
    def _callback_is_the_only_duplicate(self) -> CreativeExecutionPlan:
        counts: dict[str, int] = {}
        for decision in self.decisions:
            counts[decision.segment_id] = counts.get(decision.segment_id, 0) + 1
        duplicates = {sid for sid, count in counts.items() if count > 1}
        if not duplicates:
            return self
        allowed = {self.callback.source_segment_id} if self.callback.enabled else set()
        if duplicates != allowed or any(counts[sid] != 2 for sid in duplicates):
            raise ValueError("only one explicit callback segment may be reused once")
        return self


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
    role: ClipRole = ClipRole.development
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
    # Creative Direction Bible §7 — short structured edit rationale
    selection_reasons: list[str] = Field(default_factory=list)
    cut_reason: str = ""
    caption_strategy: CaptionStrategy = CaptionStrategy.none
    caption_reason: str = ""
    audio_strategy: AudioStrategy = Field(default_factory=AudioStrategy)
    effect_strategy: EffectStrategy = Field(default_factory=EffectStrategy)

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
    video_purpose: VideoPurpose = VideoPurpose.generic
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


class CreativeQualityScores(BaseModel):
    """Creative Direction Bible §9 — soft-flow / aftertaste axes."""

    narrative_coherence: float = _score()
    hook_strength: float = _score()
    zlog_moment_strength: float = _score()
    flow_naturalness: float = _score()
    effect_relevance: float = _score()
    effect_restraint: float = _score()
    caption_restraint: float = _score()
    natural_audio_preservation: float = _score()
    human_imperfection_value: float = _score()
    opening_ending_resonance: float = _score()
    # High = structure looks like a template (bad)
    template_visibility: float = _score(0.0)
    emotional_aftertaste: float = _score()


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
    creative: CreativeQualityScores = Field(default_factory=CreativeQualityScores)
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
