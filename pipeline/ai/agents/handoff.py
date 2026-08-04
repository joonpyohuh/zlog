"""Handoff validation + status routing (PROMPT 1.5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pipeline.ai.agents.artifacts import verify_artifact_fresh
from pipeline.ai.agents.permissions import (
    assert_agent_may,
    check_payload_permissions,
)
from pipeline.ai.agents.schema import (
    MAX_RETRY_PER_TASK,
    PROTOCOL_VERSION,
    STATUS_NEXT_ACTIONS,
    AgentEnvelope,
    AgentStatus,
    InferenceLevel,
    NextAction,
    ReasonCode,
)


class HandoffError(ValueError):
    """Raised when an envelope must not be passed to the next agent."""

    def __init__(self, message: str, *, reason_codes: list[ReasonCode] | None = None):
        super().__init__(message)
        self.reason_codes = reason_codes or [ReasonCode.PROTOCOL_MISMATCH]


@dataclass
class HandoffResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reason_codes: list[ReasonCode] = field(default_factory=list)

    def raise_if_failed(self) -> None:
        if not self.ok:
            raise HandoffError("; ".join(self.errors), reason_codes=self.reason_codes)


def next_action_for_status(status: AgentStatus) -> NextAction:
    actions = STATUS_NEXT_ACTIONS[status]
    # Deterministic pick: single primary action per status.
    priority = [
        NextAction.PROCEED,
        NextAction.PROCEED_WITH_WARNINGS,
        NextAction.CALL_EVALUATOR,
        NextAction.CALL_ESCALATION_MODEL,
        NextAction.RETRY_ONCE,
        NextAction.DETERMINISTIC_FALLBACK,
        NextAction.HALT,
    ]
    for a in priority:
        if a in actions:
            return a
    return NextAction.HALT


def route_after_envelope(
    envelope: AgentEnvelope,
    *,
    retry_count: int = 0,
) -> NextAction:
    """Orchestrator routing — reason codes / status only (no NL branching)."""
    action = envelope.recommended_next_action or next_action_for_status(envelope.status)
    if action not in STATUS_NEXT_ACTIONS[envelope.status]:
        action = next_action_for_status(envelope.status)
    if action == NextAction.RETRY_ONCE and retry_count >= MAX_RETRY_PER_TASK:
        return NextAction.DETERMINISTIC_FALLBACK
    if envelope.status == AgentStatus.NEEDS_ESCALATION:
        return NextAction.CALL_ESCALATION_MODEL
    return action


class HandoffValidator:
    """Validate envelopes between agents before the next stage runs."""

    def __init__(
        self,
        *,
        allowed_segment_ids: set[str] | None = None,
        allowed_evidence_ids: set[str] | None = None,
        project_dir: Path | None = None,
        expected_input_hashes: dict[str, str] | None = None,
    ) -> None:
        self.allowed_segment_ids = allowed_segment_ids or set()
        self.allowed_evidence_ids = allowed_evidence_ids or set()
        self.project_dir = project_dir
        self.expected_input_hashes = expected_input_hashes or {}

    def validate(self, envelope: AgentEnvelope) -> HandoffResult:
        errors: list[str] = []
        warnings: list[str] = []
        codes: list[ReasonCode] = []

        if envelope.protocol_version != PROTOCOL_VERSION:
            errors.append(f"protocol version mismatch: {envelope.protocol_version}")
            codes.append(ReasonCode.PROTOCOL_MISMATCH)

        # Task permission
        try:
            assert_agent_may(envelope.sender_agent, envelope.task_type)
        except PermissionError as exc:
            errors.append(str(exc))
            codes.append(ReasonCode.PERMISSION_VIOLATION)

        for msg in check_payload_permissions(envelope.sender_agent, envelope.payload):
            errors.append(msg)
            codes.append(ReasonCode.PERMISSION_VIOLATION)

        # Artifact freshness
        for ref in envelope.input_artifact_refs:
            if (
                ref.artifact_id in self.expected_input_hashes
                and ref.content_hash != self.expected_input_hashes[ref.artifact_id]
            ):
                errors.append(
                    f"stale input artifact {ref.artifact_id}: hash mismatch vs expected"
                )
                codes.append(ReasonCode.STALE_ARTIFACT)
            status = verify_artifact_fresh(ref, project_dir=self.project_dir)
            if status.value == "stale":
                errors.append(f"stale artifact on disk: {ref.path}")
                codes.append(ReasonCode.STALE_ARTIFACT)
            elif status.value == "invalid" and ref.path:
                # Missing file is only an error when project_dir was provided.
                if self.project_dir is not None:
                    warnings.append(f"artifact path not found for rehash: {ref.path}")

        for ref in envelope.output_artifact_refs:
            if ref.validation_status.value == "stale":
                errors.append(f"output marked stale: {ref.artifact_id}")
                codes.append(ReasonCode.STALE_ARTIFACT)

        # Observations must link evidence (or deterministic source)
        obs_ids: set[str] = set()
        for obs in envelope.observations:
            obs_ids.add(obs.observation_id)
            if not obs.evidence_frame_ids and obs.source_type.value == "evidence_frame":
                errors.append(
                    f"observation {obs.observation_id} has no evidence_frame_ids"
                )
                codes.append(ReasonCode.UNGROUNDED_OBSERVATION)
            if self.allowed_evidence_ids:
                unknown_e = [e for e in obs.evidence_frame_ids if e not in self.allowed_evidence_ids]
                if unknown_e:
                    errors.append(f"unknown evidence ids: {unknown_e[:5]}")
                    codes.append(ReasonCode.INVALID_EVIDENCE_ID)
            if self.allowed_segment_ids:
                unknown_s = [s for s in obs.segment_ids if s not in self.allowed_segment_ids]
                if unknown_s:
                    errors.append(f"unknown segment ids in observation: {unknown_s[:5]}")
                    codes.append(ReasonCode.INVALID_SEGMENT_ID)

        # Inferences must reference observations
        for inf in envelope.inferences:
            if not inf.based_on_observation_ids:
                errors.append(f"inference {inf.inference_id} references no observations")
                codes.append(ReasonCode.ORPHAN_INFERENCE)
            else:
                missing = [i for i in inf.based_on_observation_ids if i not in obs_ids]
                if missing and envelope.observations:
                    # If envelope carries observations, refs must resolve.
                    errors.append(
                        f"inference {inf.inference_id} refs unknown observations {missing}"
                    )
                    codes.append(ReasonCode.ORPHAN_INFERENCE)
                elif missing and not envelope.observations:
                    warnings.append(
                        f"inference refs observations not in envelope: {missing[:3]}"
                    )
            if (
                inf.inference_level == InferenceLevel.PERSONAL_STATE_INFERENCE
                and inf.safe_for_caption
            ):
                errors.append("PERSONAL_STATE_INFERENCE cannot be safe_for_caption")
                codes.append(ReasonCode.UNSUPPORTED_CAPTION)

        # Decisions / selected ids
        for dec in envelope.decisions:
            if self.allowed_segment_ids:
                bad = [s for s in dec.selected_ids if s not in self.allowed_segment_ids]
                if bad:
                    errors.append(f"decision selected unknown segment_ids: {bad}")
                    codes.append(ReasonCode.INVALID_SEGMENT_ID)

        # Payload-selected ids (director)
        selected = getattr(envelope.payload, "selected_segment_ids", None)
        if selected and self.allowed_segment_ids:
            bad = [s for s in selected if s not in self.allowed_segment_ids]
            if bad:
                errors.append(f"payload selected unknown segment_ids: {bad}")
                codes.append(ReasonCode.INVALID_SEGMENT_ID)

        # Confidence range already enforced by pydantic; double-check
        if not (0.0 <= envelope.confidence <= 1.0):
            errors.append("confidence out of range")
        if not (0.0 <= envelope.uncertainty <= 1.0):
            errors.append("uncertainty out of range")

        # Blocking issues vs status
        if envelope.blocking_issues and envelope.status == AgentStatus.COMPLETED:
            errors.append("blocking_issues incompatible with COMPLETED")
            codes.append(ReasonCode.PROTOCOL_MISMATCH)

        if envelope.status == AgentStatus.NEEDS_ESCALATION and not envelope.requested_capability:
            warnings.append("NEEDS_ESCALATION without requested_capability")

        # Required fields for next stage (minimal)
        if (
            envelope.status in {AgentStatus.COMPLETED, AgentStatus.COMPLETED_WITH_WARNINGS}
            and not envelope.output_artifact_refs
            and envelope.task_type.value != "render"
        ):
            warnings.append("completed without output_artifact_refs")

        ok = not errors
        return HandoffResult(ok=ok, errors=errors, warnings=warnings, reason_codes=codes)

    def validate_or_raise(self, envelope: AgentEnvelope) -> HandoffResult:
        result = self.validate(envelope)
        result.raise_if_failed()
        return result


def escalation_model_for_capability(capability: str | None, *, default: str) -> str:
    """Map requested_capability → model id (orchestrator uses this, not NL)."""
    cap = (capability or "").strip().lower()
    if cap in {"escalation_model", "sonnet", "claude-sonnet"}:
        return default
    if cap in {"sol", "gpt-5.6-sol", "repair"}:
        return "gpt-5.6-sol"
    return default
