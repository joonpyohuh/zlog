"""Versioned contracts for the offline reference-learning workspace."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pipeline.edit_presets import PRESET_TAXONOMY_VERSION, PresetCategory, get_preset
from pipeline.edl import EDL

REFERENCE_SCHEMA_VERSION = "1.0"
AnalysisStatus = Literal[
    "observed",
    "inferred",
    "not_detected",
    "unknown",
    "requires_manual_annotation",
]
DatasetSplit = Literal["train", "validation", "test"]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidencePoint(StrictModel):
    source: Literal["opencv", "scenedetect", "ffprobe", "manual"]
    description: str
    timestamp_ms: int | None = Field(default=None, ge=0)


class ReferenceMedia(StrictModel):
    reference_id: str
    path: str
    sha256: str = Field(min_length=64, max_length=64)
    file_size_bytes: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    fps: float = Field(ge=0)
    width: int = Field(ge=0)
    height: int = Field(ge=0)
    codec: str | None = None
    has_audio: bool | None = None
    license: str = "unknown"
    source_url: str | None = None
    rights_holder: str | None = None


class PacingAnalysis(StrictModel):
    status: AnalysisStatus
    cut_times_ms: list[int] = Field(default_factory=list)
    shot_durations_ms: list[int] = Field(default_factory=list)
    cut_count: int = Field(ge=0)
    cuts_per_minute: float = Field(ge=0)
    average_shot_duration_ms: float = Field(ge=0)
    median_shot_duration_ms: float = Field(ge=0)
    short_shot_ms: int = Field(ge=0)
    long_shot_ms: int = Field(ge=0)
    pace_label: Literal["slow", "balanced", "fast", "unknown"]
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidencePoint] = Field(default_factory=list)


class FeatureObservation(StrictModel):
    status: AnalysisStatus = "requires_manual_annotation"
    summary: str = ""
    preset_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    evidence: list[EvidencePoint] = Field(default_factory=list)


class ManualEditMarker(StrictModel):
    timestamp_ms: int = Field(ge=0)
    category: PresetCategory
    preset_id: str
    note: str = ""

    @model_validator(mode="after")
    def known_preset(self) -> ManualEditMarker:
        get_preset(self.preset_id, self.category)
        return self


class VisualStatistics(StrictModel):
    sampled_frame_count: int = Field(ge=0)
    average_luminance: float = Field(ge=0, le=1)
    average_saturation: float = Field(ge=0, le=1)
    estimated_warmth: float = Field(ge=0, le=1)
    average_frame_change: float = Field(ge=0, le=1)


class ReferenceEditAnalysis(StrictModel):
    schema_version: str = REFERENCE_SCHEMA_VERSION
    reference_id: str
    pacing: PacingAnalysis
    captions: FeatureObservation
    editorial_motion: FeatureObservation
    color: FeatureObservation
    transitions: FeatureObservation
    overlays: FeatureObservation
    audio: FeatureObservation
    visual_statistics: VisualStatistics
    markers: list[ManualEditMarker] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_preset_categories(self) -> ReferenceEditAnalysis:
        categories: tuple[tuple[PresetCategory, FeatureObservation], ...] = (
            ("caption", self.captions),
            ("motion", self.editorial_motion),
            ("color", self.color),
            ("transition", self.transitions),
            ("overlay", self.overlays),
            ("audio", self.audio),
        )
        for category, observation in categories:
            for preset_id in observation.preset_ids:
                get_preset(preset_id, category)
        return self


class StyleRule(StrictModel):
    dimension: Literal[
        "pacing", "caption", "motion", "color", "transition", "overlay", "audio"
    ]
    rule: str
    confidence: float = Field(ge=0, le=1)
    supporting_evidence: list[str] = Field(default_factory=list)


class PlannerRecommendation(StrictModel):
    preset_id: str
    category: PresetCategory
    use_when: str
    priority: int = Field(default=50, ge=0, le=100)

    @model_validator(mode="after")
    def known_preset(self) -> PlannerRecommendation:
        get_preset(self.preset_id, self.category)
        return self


class StyleProfile(StrictModel):
    schema_version: str = REFERENCE_SCHEMA_VERSION
    preset_taxonomy_version: str = PRESET_TAXONOMY_VERSION
    profile_id: str
    source_reference_ids: list[str]
    style_tags: list[str] = Field(default_factory=list)
    observed_behavior: list[str] = Field(default_factory=list)
    inferred_rules: list[StyleRule] = Field(default_factory=list)
    planner_recommendations: list[PlannerRecommendation] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class QualityLabels(StrictModel):
    overall: float | None = Field(default=None, ge=0, le=1)
    pacing: float | None = Field(default=None, ge=0, le=1)
    clarity: float | None = Field(default=None, ge=0, le=1)
    emotional_payoff: float | None = Field(default=None, ge=0, le=1)


class DatasetProvenance(StrictModel):
    analysis_origin: Literal["automatic_analysis", "manual_annotation", "hybrid"]
    analyzer_version: str
    created_at: str = Field(default_factory=utc_now)
    annotator: str | None = None
    license: str = "unknown"
    source_url: str | None = None


class ReferenceTrainingExample(StrictModel):
    schema_version: str = REFERENCE_SCHEMA_VERSION
    preset_taxonomy_version: str = PRESET_TAXONOMY_VERSION
    renderer_contract: str = "pipeline.edl.EDL"
    example_id: str
    split: DatasetSplit
    reference_media: ReferenceMedia
    reference_edit_analysis: ReferenceEditAnalysis
    style_profile: StyleProfile
    target_timeline: EDL | None = None
    quality_labels: QualityLabels = Field(default_factory=QualityLabels)
    provenance: DatasetProvenance


class ReferenceAnnotation(StrictModel):
    """Optional human facts that automatic video analysis cannot establish safely."""

    license: str = "unknown"
    source_url: str | None = None
    rights_holder: str | None = None
    annotator: str | None = None
    style_tags: list[str] = Field(default_factory=list)
    caption_presets: list[str] = Field(default_factory=list)
    motion_presets: list[str] = Field(default_factory=list)
    color_presets: list[str] = Field(default_factory=list)
    transition_presets: list[str] = Field(default_factory=list)
    overlay_presets: list[str] = Field(default_factory=list)
    audio_presets: list[str] = Field(default_factory=list)
    markers: list[ManualEditMarker] = Field(default_factory=list)
    quality_labels: QualityLabels = Field(default_factory=QualityLabels)
    notes: str = ""

    @model_validator(mode="after")
    def validate_preset_categories(self) -> ReferenceAnnotation:
        categories: tuple[tuple[PresetCategory, list[str]], ...] = (
            ("caption", self.caption_presets),
            ("motion", self.motion_presets),
            ("color", self.color_presets),
            ("transition", self.transition_presets),
            ("overlay", self.overlay_presets),
            ("audio", self.audio_presets),
        )
        for category, preset_ids in categories:
            for preset_id in preset_ids:
                get_preset(preset_id, category)
        return self
