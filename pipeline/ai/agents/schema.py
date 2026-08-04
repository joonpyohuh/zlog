"""AgentEnvelope + grounding models + reason codes (PROMPT 1.5)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

PROTOCOL_VERSION = "1.5.0"


class AgentStatus(str, Enum):
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NEEDS_ESCALATION = "NEEDS_ESCALATION"
    REJECTED = "REJECTED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"


class NextAction(str, Enum):
    PROCEED = "PROCEED"
    PROCEED_WITH_WARNINGS = "PROCEED_WITH_WARNINGS"
    CALL_EVALUATOR = "CALL_EVALUATOR"
    CALL_ESCALATION_MODEL = "CALL_ESCALATION_MODEL"
    RETRY_ONCE = "RETRY_ONCE"
    DETERMINISTIC_FALLBACK = "DETERMINISTIC_FALLBACK"
    HALT = "HALT"


# Status → allowed orchestrator actions (models cannot invent infinite retries).
STATUS_NEXT_ACTIONS: dict[AgentStatus, frozenset[NextAction]] = {
    AgentStatus.COMPLETED: frozenset({NextAction.PROCEED}),
    AgentStatus.COMPLETED_WITH_WARNINGS: frozenset({NextAction.PROCEED_WITH_WARNINGS}),
    AgentStatus.NEEDS_REVIEW: frozenset({NextAction.CALL_EVALUATOR}),
    AgentStatus.NEEDS_ESCALATION: frozenset({NextAction.CALL_ESCALATION_MODEL}),
    AgentStatus.REJECTED: frozenset({NextAction.HALT, NextAction.DETERMINISTIC_FALLBACK}),
    AgentStatus.FAILED_RETRYABLE: frozenset({NextAction.RETRY_ONCE}),
    AgentStatus.FAILED_TERMINAL: frozenset({NextAction.DETERMINISTIC_FALLBACK}),
}

MAX_RETRY_PER_TASK = 1


class InferenceLevel(str, Enum):
    DIRECT_FACT = "DIRECT_FACT"
    VISUAL_INTERPRETATION = "VISUAL_INTERPRETATION"
    MOOD_INTERPRETATION = "MOOD_INTERPRETATION"
    PERSONAL_STATE_INFERENCE = "PERSONAL_STATE_INFERENCE"


class ReasonCode(str, Enum):
    # Positive
    CLEAR_SUBJECT = "CLEAR_SUBJECT"
    DIRECT_GAZE = "DIRECT_GAZE"
    STRONG_COMPOSITION = "STRONG_COMPOSITION"
    STRONG_MOTION = "STRONG_MOTION"
    EMOTIONAL_REACTION = "EMOTIONAL_REACTION"
    ESTABLISHING_CONTEXT = "ESTABLISHING_CONTEXT"
    NARRATIVE_PROGRESS = "NARRATIVE_PROGRESS"
    VISUAL_NOVELTY = "VISUAL_NOVELTY"
    USEFUL_SOURCE_AUDIO = "USEFUL_SOURCE_AUDIO"
    NATURAL_ENDING = "NATURAL_ENDING"
    STRONG_HOOK = "STRONG_HOOK"
    # Negative
    VISUAL_DUPLICATE = "VISUAL_DUPLICATE"
    SEMANTIC_DUPLICATE = "SEMANTIC_DUPLICATE"
    LOW_TECHNICAL_QUALITY = "LOW_TECHNICAL_QUALITY"
    WEAK_NARRATIVE_VALUE = "WEAK_NARRATIVE_VALUE"
    UNSAFE_CROP = "UNSAFE_CROP"
    AMBIGUOUS_ACTION = "AMBIGUOUS_ACTION"
    AMBIGUOUS_CHRONOLOGY = "AMBIGUOUS_CHRONOLOGY"
    UNSUPPORTED_CAPTION = "UNSUPPORTED_CAPTION"
    PROMPT_LEAKAGE = "PROMPT_LEAKAGE"
    EXCESSIVE_DURATION = "EXCESSIVE_DURATION"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    INVALID_SEGMENT_ID = "INVALID_SEGMENT_ID"
    INVALID_EVIDENCE_ID = "INVALID_EVIDENCE_ID"
    STYLE_CONFLICT = "STYLE_CONFLICT"
    # Protocol / permission
    PERMISSION_VIOLATION = "PERMISSION_VIOLATION"
    STALE_ARTIFACT = "STALE_ARTIFACT"
    PROTOCOL_MISMATCH = "PROTOCOL_MISMATCH"
    UNGROUNDED_OBSERVATION = "UNGROUNDED_OBSERVATION"
    ORPHAN_INFERENCE = "ORPHAN_INFERENCE"


class SourceType(str, Enum):
    evidence_frame = "evidence_frame"
    deterministic_feature = "deterministic_feature"
    contact_sheet = "contact_sheet"
    audio_feature = "audio_feature"
    user_intent = "user_intent"
    prior_artifact = "prior_artifact"


class ArtifactType(str, Enum):
    evidence_manifest = "evidence_manifest"
    deterministic_features = "deterministic_features"
    asset_analyses = "asset_analyses"
    story_plan = "story_plan"
    timeline_plan = "timeline_plan"
    plan_evaluation = "plan_evaluation"
    contact_sheet = "contact_sheet"
    audio_analysis = "audio_analysis"
    edl = "edl"
    agent_envelope = "agent_envelope"
    other = "other"


class ValidationStatus(str, Enum):
    pending = "pending"
    valid = "valid"
    invalid = "invalid"
    stale = "stale"


class TaskType(str, Enum):
    extract_evidence = "extract_evidence"
    analyze_assets = "analyze_assets"
    create_story_plan = "create_story_plan"
    plan_timeline = "plan_timeline"
    evaluate_plan = "evaluate_plan"
    repair_story_plan = "repair_story_plan"
    render = "render"


def _score(default: float = 0.5) -> Any:
    return Field(default=default, ge=0.0, le=1.0)


class ArtifactReference(BaseModel):
    artifact_id: str
    artifact_type: ArtifactType
    path: str
    schema_version: str = "1"
    content_hash: str
    created_by_agent: str
    source_artifact_ids: list[str] = Field(default_factory=list)
    validation_status: ValidationStatus = ValidationStatus.pending

    @field_validator("artifact_id", "path", "content_hash")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("must be non-empty")
        return str(v).strip()


class GroundedObservation(BaseModel):
    observation_id: str = Field(default_factory=lambda: f"obs_{uuid.uuid4().hex[:10]}")
    claim: str
    evidence_frame_ids: list[str] = Field(default_factory=list)
    segment_ids: list[str] = Field(default_factory=list)
    confidence: float = _score(0.5)
    source_type: SourceType = SourceType.evidence_frame

    @field_validator("claim")
    @classmethod
    def _claim_ok(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("claim must be non-empty")
        return v.strip()


class ModelInference(BaseModel):
    inference_id: str = Field(default_factory=lambda: f"inf_{uuid.uuid4().hex[:10]}")
    claim: str
    based_on_observation_ids: list[str] = Field(default_factory=list)
    confidence: float = _score(0.5)
    inference_level: InferenceLevel = InferenceLevel.VISUAL_INTERPRETATION
    safe_for_caption: bool = True

    @model_validator(mode="after")
    def _personal_not_caption(self) -> ModelInference:
        if self.inference_level == InferenceLevel.PERSONAL_STATE_INFERENCE:
            object.__setattr__(self, "safe_for_caption", False)
        return self


class AgentDecision(BaseModel):
    decision_type: str
    selected_ids: list[str] = Field(default_factory=list)
    rejected_ids: list[str] = Field(default_factory=list)
    reason_codes: list[ReasonCode] = Field(default_factory=list)
    explanation: str = ""
    confidence: float = _score(0.5)
    reversible: bool = True


class UsageSummary(BaseModel):
    provider: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0
    latency_ms: float = 0.0
    retry_count: int = 0
    estimated_cost_usd: float = 0.0
    escalated: bool = False


# --- task payloads (typed; not a free-form dict) ----------------------------


class EvidencePayload(BaseModel):
    task: Literal[TaskType.extract_evidence] = TaskType.extract_evidence
    segment_ids: list[str] = Field(default_factory=list)
    feature_keys: list[str] = Field(default_factory=list)
    # Evidence agent must not emit semantic judgments or timestamps invented by LLM.
    invented_timestamps: list[float] = Field(default_factory=list)


class AnalysisPayload(BaseModel):
    task: Literal[TaskType.analyze_assets] = TaskType.analyze_assets
    analyses_artifact_id: str = ""
    # Forbidden if set: inventing cut times / final order.
    invented_timestamps: list[float] = Field(default_factory=list)
    final_order: list[str] = Field(default_factory=list)
    target_duration_sec: float | None = None


class DirectorPayload(BaseModel):
    task: Literal[TaskType.create_story_plan] = TaskType.create_story_plan
    story_plan_artifact_id: str = ""
    selected_segment_ids: list[str] = Field(default_factory=list)
    invented_timestamps: list[float] = Field(default_factory=list)
    invented_segment_ids: list[str] = Field(default_factory=list)


class TimelinePayload(BaseModel):
    task: Literal[TaskType.plan_timeline] = TaskType.plan_timeline
    timeline_artifact_id: str = ""
    edl_artifact_id: str = ""
    caption_texts: list[str] = Field(default_factory=list)


class EvaluationPayload(BaseModel):
    task: Literal[TaskType.evaluate_plan] = TaskType.evaluate_plan
    evaluation_artifact_id: str = ""
    failure_codes: list[ReasonCode] = Field(default_factory=list)
    requires_revision: bool = False
    # Evaluator must not silently rewrite timelines.
    mutated_timeline: bool = False


class RepairPayload(BaseModel):
    task: Literal[TaskType.repair_story_plan] = TaskType.repair_story_plan
    story_plan_artifact_id: str = ""
    addressed_failure_codes: list[ReasonCode] = Field(default_factory=list)
    invented_timestamps: list[float] = Field(default_factory=list)
    invented_segment_ids: list[str] = Field(default_factory=list)


class RenderPayload(BaseModel):
    task: Literal[TaskType.render] = TaskType.render
    output_path: str = ""
    edl_artifact_id: str = ""


AgentPayload = Annotated[
    EvidencePayload | AnalysisPayload | DirectorPayload | TimelinePayload | EvaluationPayload | RepairPayload | RenderPayload,
    Field(discriminator="task"),
]

_PAYLOAD_ADAPTER: TypeAdapter[Any] = TypeAdapter(AgentPayload)

TASK_PAYLOAD_TYPE: dict[TaskType, type[BaseModel]] = {
    TaskType.extract_evidence: EvidencePayload,
    TaskType.analyze_assets: AnalysisPayload,
    TaskType.create_story_plan: DirectorPayload,
    TaskType.plan_timeline: TimelinePayload,
    TaskType.evaluate_plan: EvaluationPayload,
    TaskType.repair_story_plan: RepairPayload,
    TaskType.render: RenderPayload,
}


class AgentEnvelope(BaseModel):
    protocol_version: str = PROTOCOL_VERSION
    message_id: str = Field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    project_id: str
    trace_id: str
    parent_message_id: str | None = None
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )
    sender_agent: str
    sender_provider: Literal["anthropic", "openai", "code", "ffmpeg", "remotion"] = "code"
    sender_model: str = "deterministic"
    recipient_agent: str
    task_type: TaskType
    status: AgentStatus
    input_artifact_refs: list[ArtifactReference] = Field(default_factory=list)
    output_artifact_refs: list[ArtifactReference] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    observations: list[GroundedObservation] = Field(default_factory=list)
    inferences: list[ModelInference] = Field(default_factory=list)
    decisions: list[AgentDecision] = Field(default_factory=list)
    rejected_options: list[str] = Field(default_factory=list)
    uncertainty: float = _score(0.0)
    blocking_issues: list[str] = Field(default_factory=list)
    requested_capability: str | None = None
    recommended_next_action: NextAction | None = None
    confidence: float = _score(0.5)
    usage: UsageSummary = Field(default_factory=UsageSummary)
    payload: EvidencePayload | AnalysisPayload | DirectorPayload | TimelinePayload | EvaluationPayload | RepairPayload | RenderPayload

    @model_validator(mode="before")
    @classmethod
    def _coerce_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        task = data.get("task_type")
        payload = data.get("payload")
        if isinstance(payload, dict) and task and "task" not in payload:
            # Allow omitting discriminator inside payload when task_type is set.
            try:
                tt = TaskType(task) if not isinstance(task, TaskType) else task
                payload = {"task": tt, **payload}
                data = {**data, "payload": payload}
            except ValueError:
                pass
        if isinstance(data.get("payload"), dict):
            data = {**data, "payload": _PAYLOAD_ADAPTER.validate_python(data["payload"])}
        return data

    @model_validator(mode="after")
    def _consistency(self) -> AgentEnvelope:
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError(
                f"protocol_version {self.protocol_version!r} != {PROTOCOL_VERSION}"
            )
        expected = TASK_PAYLOAD_TYPE[self.task_type]
        if not isinstance(self.payload, expected):
            raise TypeError(
                f"payload type {type(self.payload).__name__} incompatible with "
                f"task_type={self.task_type.value}"
            )
        if self.blocking_issues and self.status in {
            AgentStatus.COMPLETED,
        }:
            raise ValueError("blocking_issues present but status is COMPLETED")
        if self.status == AgentStatus.NEEDS_ESCALATION and not self.requested_capability:
            # Soft default — keep envelope valid but orchestrator should escalate.
            object.__setattr__(self, "requested_capability", "escalation_model")
        if self.recommended_next_action is None:
            # Stable primary action (not frozenset iteration order).
            priority = [
                NextAction.PROCEED,
                NextAction.PROCEED_WITH_WARNINGS,
                NextAction.CALL_EVALUATOR,
                NextAction.CALL_ESCALATION_MODEL,
                NextAction.RETRY_ONCE,
                NextAction.DETERMINISTIC_FALLBACK,
                NextAction.HALT,
            ]
            allowed = STATUS_NEXT_ACTIONS[self.status]
            pick = next((a for a in priority if a in allowed), NextAction.HALT)
            object.__setattr__(self, "recommended_next_action", pick)
        elif self.recommended_next_action not in STATUS_NEXT_ACTIONS[self.status]:
            raise ValueError(
                f"recommended_next_action {self.recommended_next_action} not allowed "
                f"for status {self.status}"
            )
        return self


def normalize_provider_output(
    *,
    project_id: str,
    trace_id: str,
    sender_agent: str,
    sender_provider: Literal["anthropic", "openai", "code", "ffmpeg", "remotion"],
    sender_model: str,
    recipient_agent: str,
    task_type: TaskType,
    status: AgentStatus,
    payload: BaseModel,
    parent_message_id: str | None = None,
    observations: list[GroundedObservation] | None = None,
    inferences: list[ModelInference] | None = None,
    decisions: list[AgentDecision] | None = None,
    usage: UsageSummary | None = None,
    input_artifact_refs: list[ArtifactReference] | None = None,
    output_artifact_refs: list[ArtifactReference] | None = None,
    blocking_issues: list[str] | None = None,
    requested_capability: str | None = None,
    recommended_next_action: NextAction | None = None,
    confidence: float = 0.5,
    uncertainty: float = 0.0,
) -> AgentEnvelope:
    """Normalize Claude/GPT/code outputs into one AgentEnvelope shape."""
    return AgentEnvelope(
        project_id=project_id,
        trace_id=trace_id,
        parent_message_id=parent_message_id,
        sender_agent=sender_agent,
        sender_provider=sender_provider,
        sender_model=sender_model,
        recipient_agent=recipient_agent,
        task_type=task_type,
        status=status,
        payload=payload,  # type: ignore[arg-type]
        observations=observations or [],
        inferences=inferences or [],
        decisions=decisions or [],
        usage=usage or UsageSummary(provider=sender_provider, model=sender_model),
        input_artifact_refs=input_artifact_refs or [],
        output_artifact_refs=output_artifact_refs or [],
        blocking_issues=blocking_issues or [],
        requested_capability=requested_capability,
        recommended_next_action=recommended_next_action,
        confidence=confidence,
        uncertainty=uncertainty,
    )
