"""Core contracts for Zlog's content-led editorial system."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EDITORIAL_SCHEMA_VERSION = "2.0"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EditorialRole(StrEnum):
    establishing = "establishing"
    progression = "progression"
    action = "action"
    detail = "detail"
    atmosphere = "atmosphere"
    interaction = "interaction"
    reaction = "reaction"
    transition = "transition"
    highlight = "highlight"
    payoff = "payoff"
    breathing = "breathing"
    ending = "ending"


class SourceRange(StrictModel):
    source_file: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> SourceRange:
        if self.end_ms <= self.start_ms:
            raise ValueError("source range must have positive duration")
        return self


class ObservableCue(StrictModel):
    kind: Literal[
        "speech",
        "laughter",
        "expression",
        "interaction",
        "movement",
        "ambient_audio",
        "visual_change",
        "time_change",
        "location_change",
    ]
    description: str
    evidence_ids: list[str] = Field(default_factory=list)


class SceneUnderstanding(StrictModel):
    scene_id: str
    segment_id: str
    source_range: SourceRange
    chronological_index: int = Field(ge=0)
    day_id: str
    event_id: str
    label: str
    subjects: list[str] = Field(default_factory=list)
    observed_action: str = "unknown"
    location: str = "unknown"
    time_context: str = "unknown"
    vlog_mode: Literal["speaking", "visual", "mixed", "unknown"] = "unknown"
    roles: list[EditorialRole] = Field(min_length=1)
    observable_cues: list[ObservableCue] = Field(default_factory=list)
    emotional_progression: Literal[
        "calm",
        "anticipation",
        "excitement",
        "release",
        "unknown",
    ] = "unknown"
    technical_quality: Literal["usable", "limited", "unknown"] = "unknown"
    redundancy_group: str | None = None
    focus_x: float = Field(default=0.5, ge=0, le=1)
    focus_y: float = Field(default=0.5, ge=0, le=1)
    crop_confidence: float = Field(default=0, ge=0, le=1)
    evidence_frame_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)


class EventNode(StrictModel):
    event_id: str
    day_id: str
    title: str
    scene_ids: list[str] = Field(min_length=1)


class DayNode(StrictModel):
    day_id: str
    title: str
    date: str | None = None
    event_ids: list[str] = Field(min_length=1)
    boundary_confidence: float = Field(default=0.5, ge=0, le=1)
    boundary_reason: str = "upload chronology"


class TripGraph(StrictModel):
    schema_version: str = EDITORIAL_SCHEMA_VERSION
    project: str
    title: str = "Trip"
    input_mode: Literal["multiple_clips", "single_long_video"]
    days: list[DayNode] = Field(min_length=1)
    events: list[EventNode] = Field(min_length=1)
    scenes: list[SceneUnderstanding] = Field(min_length=1)
    unknowns: list[str] = Field(default_factory=list)


class EditingIntent(StrictModel):
    schema_version: str = EDITORIAL_SCHEMA_VERSION
    raw_prompt: str
    desired_mood: str | None = None
    pacing: Literal["calm", "balanced", "energetic"] | None = None
    visual_style: str | None = None
    narrative_focus: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    chronology: Literal["preserve", "flexible"] = "preserve"
    speaking_preference: Literal["more", "less", "unspecified"] = "unspecified"
    caption_preference: Literal["none", "sparse", "contextual", "unspecified"] = (
        "unspecified"
    )
    current_project_edits: list[str] = Field(default_factory=list)
    learned_preferences: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class HighlightDecision(StrictModel):
    scene_id: str
    output_type: Literal["long", "short"]
    selected: bool
    reasons: list[str] = Field(min_length=1)
    rejected_reasons: list[str] = Field(default_factory=list)


class EffectCommand(StrictModel):
    effect_id: Literal[
        "cut",
        "micro_push_in",
        "micro_pull_out",
        "reaction_punch_in",
        "soft_reveal",
        "ambient_outro",
    ] = "cut"
    start_frame: int = Field(default=0, ge=0)
    duration_frames: int = Field(default=0, ge=0)
    reason: str = "No content-based effect reason; use a clean cut."


class TimelineVideoItem(StrictModel):
    item_id: str
    scene_id: str
    segment_id: str
    source_file: str
    source_in_ms: int = Field(ge=0)
    source_out_ms: int = Field(gt=0)
    timeline_start_frame: int = Field(ge=0)
    duration_frames: int = Field(gt=0)
    roles: list[EditorialRole] = Field(min_length=1)
    decision_reason: str
    focus_x: float = Field(default=0.5, ge=0, le=1)
    focus_y: float = Field(default=0.5, ge=0, le=1)
    crop_confidence: float = Field(default=0, ge=0, le=1)
    effect: EffectCommand = Field(default_factory=EffectCommand)

    @model_validator(mode="after")
    def valid_source_window(self) -> TimelineVideoItem:
        if self.source_out_ms <= self.source_in_ms:
            raise ValueError("timeline item source range must be positive")
        return self


class TimelineCaptionItem(StrictModel):
    item_id: str
    start_frame: int = Field(ge=0)
    duration_frames: int = Field(gt=0)
    text: str
    position: Literal["top", "center", "bottom"] = "bottom"
    style: Literal["title", "subtitle", "lower_third"] = "subtitle"
    animation: Literal["none", "soft_fade", "spring"] = "soft_fade"
    grounding: str


class TimelineMusicItem(StrictModel):
    music_id: str
    start_frame: int = Field(default=0, ge=0)
    duration_frames: int = Field(gt=0)
    volume: float = Field(default=0.35, ge=0, le=1)
    selected_by_user: bool = True


class EditTimeline(StrictModel):
    schema_version: str = EDITORIAL_SCHEMA_VERSION
    timeline_id: str
    project: str
    output_type: Literal["long", "short"]
    revision: int = Field(default=1, ge=1)
    fps: int = Field(default=30, ge=1, le=120)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    target_duration_sec: float = Field(gt=0)
    duration_frames: int = Field(gt=0)
    creative_direction: str
    hypothesis_version: str = "zlog-editorial-theory/1"
    video_items: list[TimelineVideoItem] = Field(min_length=1)
    captions: list[TimelineCaptionItem] = Field(default_factory=list)
    music: TimelineMusicItem | None = None

    @model_validator(mode="after")
    def timeline_is_ordered_and_bounded(self) -> EditTimeline:
        ordered = sorted(self.video_items, key=lambda item: item.timeline_start_frame)
        cursor = 0
        for item in ordered:
            if item.timeline_start_frame != cursor:
                raise ValueError("video items must form one contiguous primary track")
            cursor += item.duration_frames
        if cursor != self.duration_frames:
            raise ValueError("duration_frames must equal the primary track duration")
        for caption in self.captions:
            if caption.start_frame + caption.duration_frames > self.duration_frames:
                raise ValueError("caption exceeds timeline duration")
        if (
            self.music
            and self.music.start_frame + self.music.duration_frames
            > self.duration_frames
        ):
            raise ValueError("music exceeds timeline duration")
        return self


class RepairAction(StrictModel):
    action: Literal[
        "drop_item",
        "insert_scene",
        "move_item",
        "adjust_duration",
        "remove_effect",
        "add_time_marker",
        "review_unknown",
    ]
    item_id: str | None = None
    scene_id: str | None = None
    target_frame: int | None = Field(default=None, ge=0)
    duration_frames: int | None = Field(default=None, gt=0)


class CriticIssue(StrictModel):
    code: Literal[
        "semantic_repetition",
        "missing_establishing",
        "unexplained_time_change",
        "unexplained_location_change",
        "intent_pacing_conflict",
        "aesthetic_inconsistency",
        "unsupported_effect",
        "unknown_context",
    ]
    message: str
    scene_ids: list[str] = Field(default_factory=list)
    repair: RepairAction


class CriticReport(StrictModel):
    schema_version: str = EDITORIAL_SCHEMA_VERSION
    timeline_id: str
    issues: list[CriticIssue] = Field(default_factory=list)
    passed: bool


class EditorialPackage(StrictModel):
    schema_version: str = EDITORIAL_SCHEMA_VERSION
    project: str
    trip_graph: TripGraph
    editing_intent: EditingIntent
    highlights: list[HighlightDecision]
    long_timeline: EditTimeline
    short_timeline: EditTimeline
    long_critic: CriticReport
    short_critic: CriticReport
