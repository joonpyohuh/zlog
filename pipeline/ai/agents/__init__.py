"""Structured multi-agent handoff protocol (PROMPT 1.5).

Agents submit AgentEnvelope results to a shared artifact store.
Downstream agents must validate evidence + schema — never trust prior
conclusions as facts. Free-form agent-to-agent chat is forbidden.
"""

from pipeline.ai.agents.common_prompt import COMMON_AGENT_SYSTEM_PREAMBLE
from pipeline.ai.agents.context import (
    build_analysis_context,
    build_director_context,
    build_evaluator_context,
    build_repair_context,
)
from pipeline.ai.agents.handoff import (
    HandoffError,
    HandoffValidator,
    next_action_for_status,
    route_after_envelope,
)
from pipeline.ai.agents.permissions import (
    AgentName,
    assert_agent_may,
    forbidden_fields_for,
)
from pipeline.ai.agents.schema import (
    AgentDecision,
    AgentEnvelope,
    AgentStatus,
    ArtifactReference,
    GroundedObservation,
    InferenceLevel,
    ModelInference,
    ReasonCode,
    UsageSummary,
    normalize_provider_output,
)
from pipeline.ai.agents.trace import AgentTraceStore, write_trace_report

__all__ = [
    "COMMON_AGENT_SYSTEM_PREAMBLE",
    "AgentDecision",
    "AgentEnvelope",
    "AgentName",
    "AgentStatus",
    "AgentTraceStore",
    "ArtifactReference",
    "GroundedObservation",
    "HandoffError",
    "HandoffValidator",
    "InferenceLevel",
    "ModelInference",
    "ReasonCode",
    "UsageSummary",
    "assert_agent_may",
    "build_analysis_context",
    "build_director_context",
    "build_evaluator_context",
    "build_repair_context",
    "forbidden_fields_for",
    "next_action_for_status",
    "normalize_provider_output",
    "route_after_envelope",
    "write_trace_report",
]
