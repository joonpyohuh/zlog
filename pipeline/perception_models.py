"""Versioned contracts for Zlog's deterministic adaptive perception path."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "1.0"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class HierarchyLevel(StrEnum):
    frame = "frame"
    shot = "shot"
    atomic_event = "atomic_event"
    semantic_scene = "semantic_scene"
    narrative_beat = "narrative_beat"
    chapter = "chapter"


class SourceRange(BaseModel):
    source_file: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def ordered(self) -> SourceRange:
        if self.end_ms < self.start_ms:
            raise ValueError("source range end_ms must be >= start_ms")
        return self


class ProvenanceRecord(BaseModel):
    source: Literal[
        "ffmpeg",
        "opencv",
        "transcription",
        "embedding_model",
        "multimodal_model",
        "text_model",
        "heuristic",
        "user",
    ]
    model_or_version: str | None = None
    created_at: str = Field(default_factory=utc_now)
    input_evidence_ids: list[str] = Field(default_factory=list)


class ConfidenceRecord(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list)


class EditingGoal(BaseModel):
    target_duration_seconds: float = Field(default=30.0, gt=0.0)
    platform: Literal[
        "youtube_shorts", "instagram_reels", "tiktok", "youtube", "other"
    ] = "other"
    style: str = "clean_vlog"
    pacing: Literal["slow", "balanced", "fast"] = "balanced"
    narrative_preference: str = ""
    preserve_dialogue: bool = True
    preserve_ambient_audio: bool = True


class AnalysisBudget(BaseModel):
    max_representative_frames: int = Field(default=80, ge=1)
    max_high_cost_inspections: int = Field(default=0, ge=0)
    max_multimodal_input_seconds: float = Field(default=0.0, ge=0.0)
    max_estimated_cost_usd: float = Field(default=0.0, ge=0.0)
    max_processing_time_ms: float = Field(default=30_000.0, gt=0.0)


class LightingFeatures(BaseModel):
    luminance: float = Field(ge=0.0, le=1.0)
    contrast: float = Field(ge=0.0, le=1.0)
    estimated_warmth: float | None = Field(default=None, ge=0.0, le=1.0)
    exposure_usable: bool


class CompositionFeatures(BaseModel):
    shot_type: str = "unknown"
    subject_position_x: float | None = Field(default=None, ge=0.0, le=1.0)
    subject_position_y: float | None = Field(default=None, ge=0.0, le=1.0)
    face_visibility: float | None = Field(default=None, ge=0.0, le=1.0)


class MotionFeatures(BaseModel):
    optical_flow_magnitude: float = Field(ge=0.0)
    perceptual_change: float = Field(ge=0.0, le=1.0)
    is_static: bool
    is_high_motion: bool


class AudioFeatures(BaseModel):
    rms: float = Field(ge=0.0)
    onset_density: float = Field(ge=0.0)
    silence_probability: float = Field(ge=0.0, le=1.0)
    speech_presence: float | None = Field(default=None, ge=0.0, le=1.0)
    availability: Literal["measured", "unavailable"] = "measured"
    unavailable_reason: str | None = None


class QualityFeatures(BaseModel):
    blur_score: float = Field(ge=0.0)
    quality_score: float = Field(ge=0.0, le=1.0)
    duplicate_likelihood: float = Field(ge=0.0, le=1.0)
    filter_verdict: str | None = None


class FactorizedFeatures(BaseModel):
    lighting: LightingFeatures
    composition: CompositionFeatures
    motion: MotionFeatures
    audio: AudioFeatures
    quality: QualityFeatures


class PerceptionObservation(BaseModel):
    schema_version: str = SCHEMA_VERSION
    observation_id: str
    segment_id: str
    source_range: SourceRange
    representative_frame_ids: list[str] = Field(default_factory=list)
    representative_frame_timestamps_ms: dict[str, int] = Field(default_factory=dict)
    features: FactorizedFeatures
    provenance: list[ProvenanceRecord] = Field(default_factory=list)


class SourceAssetMetadata(BaseModel):
    asset_id: str
    source_file: str
    duration_ms: int = Field(ge=0)
    fps: float = Field(ge=0.0)
    width: int = Field(ge=0)
    height: int = Field(ge=0)
    codec: str | None = None
    provenance: list[ProvenanceRecord] = Field(default_factory=list)


class ScanArtifact(BaseModel):
    schema_version: str = SCHEMA_VERSION
    project: str
    duration_ms: int = Field(ge=0)
    sources: list[SourceAssetMetadata] = Field(default_factory=list)
    observations: list[PerceptionObservation] = Field(default_factory=list)


class SignalScores(BaseModel):
    shot_change: float = Field(default=0.0, ge=0.0, le=1.0)
    visual_change: float = Field(default=0.0, ge=0.0, le=1.0)
    motion_change: float = Field(default=0.0, ge=0.0, le=1.0)
    lighting_change: float = Field(default=0.0, ge=0.0, le=1.0)
    audio_change: float = Field(default=0.0, ge=0.0, le=1.0)
    prediction_error: float = Field(default=0.0, ge=0.0, le=1.0)


class BoundaryCandidate(BaseModel):
    schema_version: str = SCHEMA_VERSION
    candidate_id: str
    timestamp_ms: int = Field(ge=0)
    before_segment_id: str
    after_segment_id: str
    signal_scores: SignalScores
    combined_score: float = Field(ge=0.0, le=1.0)
    confidence: ConfidenceRecord
    provenance: list[ProvenanceRecord] = Field(default_factory=list)


class BoundaryCandidatesArtifact(BaseModel):
    schema_version: str = SCHEMA_VERSION
    project: str
    candidates: list[BoundaryCandidate] = Field(default_factory=list)


class SegmentRoute(BaseModel):
    segment_id: str
    categories: dict[str, float]
    selected_feature_groups: list[str]
    confidence: ConfidenceRecord
    provenance: list[ProvenanceRecord] = Field(default_factory=list)


class RoutingArtifact(BaseModel):
    schema_version: str = SCHEMA_VERSION
    project: str
    routes: list[SegmentRoute] = Field(default_factory=list)


class BoundaryDecision(BaseModel):
    schema_version: str = SCHEMA_VERSION
    candidate_id: str
    decision: Literal["split", "merge", "uncertain"]
    target_level: Literal[
        "shot", "atomic_event", "semantic_scene", "narrative_beat", "chapter"
    ] = "semantic_scene"
    reason_codes: list[str] = Field(default_factory=list)
    concise_reason: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    inspected_evidence_ids: list[str] = Field(default_factory=list)


class EscalationRequest(BaseModel):
    request_id: str
    candidate_id: str
    task: Literal["boundary_judgment", "merge_judgment"]
    reason_codes: list[str]
    status: Literal["budget_blocked", "provider_not_configured"]
    context_segment_ids: list[str]


class BoundaryDecisionsArtifact(BaseModel):
    schema_version: str = SCHEMA_VERSION
    project: str
    decisions: list[BoundaryDecision] = Field(default_factory=list)
    escalation_requests: list[EscalationRequest] = Field(default_factory=list)


class TimelineNode(BaseModel):
    node_id: str
    level: HierarchyLevel
    source_ranges: list[SourceRange]
    parent_id: str | None = None
    child_ids: list[str] = Field(default_factory=list)
    categories: dict[str, float] = Field(default_factory=dict)
    representative_frame_ids: list[str] = Field(default_factory=list)
    feature_observation_ids: list[str] = Field(default_factory=list)
    narrative_role: str | None = None
    confidence: ConfidenceRecord
    provenance: list[ProvenanceRecord] = Field(default_factory=list)


class HierarchicalTimeline(BaseModel):
    schema_version: str = SCHEMA_VERSION
    project: str
    asset_id: str
    duration_ms: int = Field(ge=0)
    editing_goal: EditingGoal
    nodes: list[TimelineNode]
    boundary_candidate_ids: list[str] = Field(default_factory=list)
    boundary_decision_ids: list[str] = Field(default_factory=list)
    escalation_requests: list[EscalationRequest] = Field(default_factory=list)
    quality_status: Literal["full", "degraded"] = "full"
    degraded_reasons: list[str] = Field(default_factory=list)


class StageCost(BaseModel):
    stage: str
    latency_ms: float = Field(ge=0.0)
    cache_status: Literal["hit", "miss"]
    sampled_frames: int = Field(default=0, ge=0)
    inspected_seconds: float = Field(default=0.0, ge=0.0)
    multimodal_calls: int = Field(default=0, ge=0)
    text_model_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: float = Field(default=0.0, ge=0.0)


class ProcessingCostReport(BaseModel):
    schema_version: str = SCHEMA_VERSION
    project: str
    quality_status: Literal["full", "degraded"]
    degraded_reasons: list[str] = Field(default_factory=list)
    stages: list[StageCost]
    total_latency_ms: float = Field(ge=0.0)
    cache_hits: int = Field(ge=0)
    cache_misses: int = Field(ge=0)
    sampled_frames: int = Field(ge=0)
    inspected_seconds: float = Field(ge=0.0)
    escalation_count: int = Field(ge=0)
    multimodal_calls: int = Field(ge=0)
    text_model_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0.0)


class UserEditFeedback(BaseModel):
    schema_version: str = SCHEMA_VERSION
    project_id: str
    source_segment_ids: list[str]
    action: Literal[
        "split",
        "merge",
        "move_boundary",
        "remove",
        "restore",
        "shorten",
        "lengthen",
        "prefer_candidate",
        "reject_candidate",
    ]
    previous_value: Any | None = None
    new_value: Any | None = None
    editing_goal: EditingGoal | None = None
    created_at: str = Field(default_factory=utc_now)
