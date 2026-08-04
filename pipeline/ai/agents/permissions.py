"""Per-agent capability boundaries (PROMPT 1.5)."""

from __future__ import annotations

from enum import Enum

from pipeline.ai.agents.schema import (
    AnalysisPayload,
    DirectorPayload,
    EvaluationPayload,
    EvidencePayload,
    ReasonCode,
    RepairPayload,
    TaskType,
)


class AgentName(str, Enum):
    evidence = "evidence"
    asset_analysis = "asset_analysis"
    director = "director"
    timeline_planner = "timeline_planner"
    evaluation = "evaluation"
    repair = "repair"
    renderer = "renderer"
    orchestrator = "orchestrator"


# Fields / actions each agent may produce.
ALLOWED_TASKS: dict[AgentName, frozenset[TaskType]] = {
    AgentName.evidence: frozenset({TaskType.extract_evidence}),
    AgentName.asset_analysis: frozenset({TaskType.analyze_assets}),
    AgentName.director: frozenset({TaskType.create_story_plan}),
    AgentName.timeline_planner: frozenset({TaskType.plan_timeline}),
    AgentName.evaluation: frozenset({TaskType.evaluate_plan}),
    AgentName.repair: frozenset({TaskType.repair_story_plan}),
    AgentName.renderer: frozenset({TaskType.render}),
    AgentName.orchestrator: frozenset(set(TaskType)),
}

# Payload attributes that constitute a permission violation when non-empty/True.
FORBIDDEN_PAYLOAD_ATTRS: dict[AgentName, frozenset[str]] = {
    AgentName.evidence: frozenset({"invented_timestamps"}),
    AgentName.asset_analysis: frozenset(
        {"invented_timestamps", "final_order", "target_duration_sec"}
    ),
    AgentName.director: frozenset({"invented_timestamps", "invented_segment_ids"}),
    AgentName.timeline_planner: frozenset(),  # captions checked separately for grounding
    AgentName.evaluation: frozenset({"mutated_timeline"}),
    AgentName.repair: frozenset({"invented_timestamps", "invented_segment_ids"}),
    AgentName.renderer: frozenset(),
    AgentName.orchestrator: frozenset(),
}


def forbidden_fields_for(agent: AgentName | str) -> frozenset[str]:
    name = AgentName(agent) if not isinstance(agent, AgentName) else agent
    return FORBIDDEN_PAYLOAD_ATTRS.get(name, frozenset())


def assert_agent_may(agent: AgentName | str, task_type: TaskType) -> None:
    name = AgentName(agent) if not isinstance(agent, AgentName) else agent
    allowed = ALLOWED_TASKS.get(name, frozenset())
    if task_type not in allowed:
        raise PermissionError(
            f"{name.value} may not perform task_type={task_type.value} "
            f"(reason={ReasonCode.PERMISSION_VIOLATION.value})"
        )


def check_payload_permissions(agent: AgentName | str, payload: object) -> list[str]:
    """Return violation messages (empty = ok)."""
    name = AgentName(agent) if not isinstance(agent, AgentName) else agent
    forbidden = FORBIDDEN_PAYLOAD_ATTRS.get(name, frozenset())
    violations: list[str] = []
    for attr in forbidden:
        if not hasattr(payload, attr):
            continue
        value = getattr(payload, attr)
        if value is None:
            continue
        if isinstance(value, bool) and value:
            violations.append(f"{name.value} set forbidden flag {attr}=True")
        elif isinstance(value, (list, tuple, set)) and len(value) > 0:
            violations.append(f"{name.value} set forbidden field {attr}={list(value)[:5]!r}")
        elif isinstance(value, (int, float)) and attr == "target_duration_sec":
            violations.append(f"{name.value} must not finalize target_duration_sec")
    # Extra semantic guards by payload type
    if isinstance(payload, EvidencePayload) and payload.invented_timestamps:
        violations.append("evidence agent invented timestamps")
    if isinstance(payload, AnalysisPayload):
        if payload.invented_timestamps:
            violations.append("analysis agent decided timestamps")
        if payload.final_order:
            violations.append("analysis agent decided final cut order")
        if payload.target_duration_sec is not None:
            violations.append("analysis agent finalized duration")
    if isinstance(payload, DirectorPayload):
        if payload.invented_timestamps:
            violations.append("director invented timestamps")
        if payload.invented_segment_ids:
            violations.append("director invented segment ids")
    if isinstance(payload, EvaluationPayload) and payload.mutated_timeline:
        violations.append("evaluation agent silently mutated timeline")
    if isinstance(payload, RepairPayload) and (
        payload.invented_timestamps or payload.invented_segment_ids
    ):
        violations.append("repair invented ids/timestamps")
    return violations
