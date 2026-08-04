"""PROMPT 1.5 — agent envelope, permissions, handoff, context, trace."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.ai.agents.artifacts import content_hash_path, make_artifact_ref
from pipeline.ai.agents.common_prompt import (
    COMMON_AGENT_SYSTEM_PREAMBLE,
    with_common_preamble,
)
from pipeline.ai.agents.context import (
    build_analysis_context,
    build_director_context,
    build_evaluator_context,
    build_repair_context,
    context_keys,
)
from pipeline.ai.agents.handoff import (
    MAX_RETRY_PER_TASK,
    HandoffError,
    HandoffValidator,
    next_action_for_status,
    route_after_envelope,
)
from pipeline.ai.agents.permissions import (
    AgentName,
    assert_agent_may,
    check_payload_permissions,
)
from pipeline.ai.agents.schema import (
    PROTOCOL_VERSION,
    AgentDecision,
    AgentEnvelope,
    AgentStatus,
    AnalysisPayload,
    ArtifactType,
    DirectorPayload,
    EvaluationPayload,
    GroundedObservation,
    InferenceLevel,
    ModelInference,
    NextAction,
    ReasonCode,
    TaskType,
    UsageSummary,
    normalize_provider_output,
)
from pipeline.ai.agents.trace import AgentTraceStore, write_trace_report
from pipeline.ai.schemas import (
    AssetAnalysis,
    MediaType,
    Mood,
    PlanEvaluation,
    PlannedClip,
    StoryPlan,
    StylePreset,
    TimelinePlan,
)

ALLOWED = {"still_01#s001", "still_02#s001", "still_03#s001"}
EVIDENCE = {"frames/still_01#s001.jpg", "frames/still_02#s001.jpg"}


def _analysis(sid: str = "still_01#s001", **over) -> AssetAnalysis:
    base = {
        "asset_id": f"a:{sid}",
        "segment_id": sid,
        "source_file": f"{sid.split('#')[0]}.mp4",
        "media_type": MediaType.still_video,
        "upload_index": 0,
        "evidence_frame_ids": [f"frames/{sid}.jpg"],
        "subjects": ["person"],
        "mood": Mood.calm,
        "hook_potential": 0.6,
        "narrative_value": 0.5,
        "crop_confidence": 0.7,
        "visually_grounded_facts": ["person"],
        "analysis_provider": "anthropic",
        "analysis_model": "haiku",
    }
    base.update(over)
    return AssetAnalysis.model_validate(base)


def _story(**over) -> StoryPlan:
    base = {
        "user_intent": "calm vlog",
        "concept": "walk",
        "hook_segment_id": "still_01#s001",
        "ending_segment_id": "still_03#s001",
        "selected_segment_ids": sorted(ALLOWED),
        "target_duration_sec": 12,
        "provider": "anthropic",
        "model": "sonnet",
        "style_preset": StylePreset.clean_vlog,
    }
    base.update(over)
    return StoryPlan.model_validate(base)


def test_analysis_agent_timestamp_decision_rejected():
    payload = AnalysisPayload(invented_timestamps=[1.23, 4.56])
    violations = check_payload_permissions(AgentName.asset_analysis, payload)
    assert violations
    env = normalize_provider_output(
        project_id="p",
        trace_id="t",
        sender_agent=AgentName.asset_analysis.value,
        sender_provider="anthropic",
        sender_model="haiku",
        recipient_agent=AgentName.director.value,
        task_type=TaskType.analyze_assets,
        status=AgentStatus.COMPLETED,
        payload=payload,
    )
    result = HandoffValidator(allowed_segment_ids=ALLOWED).validate(env)
    assert not result.ok
    assert ReasonCode.PERMISSION_VIOLATION in result.reason_codes


def test_director_unknown_segment_rejected():
    payload = DirectorPayload(selected_segment_ids=["ghost#s001", "still_01#s001"])
    env = normalize_provider_output(
        project_id="p",
        trace_id="t",
        sender_agent=AgentName.director.value,
        sender_provider="anthropic",
        sender_model="sonnet",
        recipient_agent=AgentName.timeline_planner.value,
        task_type=TaskType.create_story_plan,
        status=AgentStatus.COMPLETED,
        payload=payload,
        decisions=[
            AgentDecision(
                decision_type="select",
                selected_ids=["ghost#s001"],
                reason_codes=[ReasonCode.STRONG_HOOK],
            )
        ],
    )
    result = HandoffValidator(allowed_segment_ids=ALLOWED).validate(env)
    assert not result.ok
    assert ReasonCode.INVALID_SEGMENT_ID in result.reason_codes


def test_observation_without_evidence_rejected():
    env = normalize_provider_output(
        project_id="p",
        trace_id="t",
        sender_agent=AgentName.asset_analysis.value,
        sender_provider="anthropic",
        sender_model="haiku",
        recipient_agent=AgentName.director.value,
        task_type=TaskType.analyze_assets,
        status=AgentStatus.COMPLETED,
        payload=AnalysisPayload(),
        observations=[
            GroundedObservation(
                claim="a person is visible",
                evidence_frame_ids=[],
                segment_ids=["still_01#s001"],
            )
        ],
    )
    result = HandoffValidator(allowed_segment_ids=ALLOWED, allowed_evidence_ids=EVIDENCE).validate(
        env
    )
    assert not result.ok
    assert ReasonCode.UNGROUNDED_OBSERVATION in result.reason_codes


def test_inference_without_observation_refs_rejected():
    obs = GroundedObservation(
        claim="person centered",
        evidence_frame_ids=["frames/still_01#s001.jpg"],
        segment_ids=["still_01#s001"],
    )
    env = normalize_provider_output(
        project_id="p",
        trace_id="t",
        sender_agent=AgentName.asset_analysis.value,
        sender_provider="anthropic",
        sender_model="haiku",
        recipient_agent=AgentName.director.value,
        task_type=TaskType.analyze_assets,
        status=AgentStatus.COMPLETED,
        payload=AnalysisPayload(),
        observations=[obs],
        inferences=[
            ModelInference(
                claim="they feel lonely",
                based_on_observation_ids=[],
                inference_level=InferenceLevel.PERSONAL_STATE_INFERENCE,
            )
        ],
    )
    result = HandoffValidator(allowed_segment_ids=ALLOWED, allowed_evidence_ids=EVIDENCE).validate(
        env
    )
    assert not result.ok
    assert ReasonCode.ORPHAN_INFERENCE in result.reason_codes
    # personal state never caption-safe
    assert env.inferences[0].safe_for_caption is False


def test_stale_artifact_hash_rejected(tmp_path: Path):
    f = tmp_path / "asset_analyses.json"
    f.write_text('{"ok": true}', encoding="utf-8")
    ref = make_artifact_ref(
        path=f,
        artifact_type=ArtifactType.asset_analyses,
        created_by_agent=AgentName.asset_analysis,
    )
    # mutate file → stale
    f.write_text('{"ok": false}', encoding="utf-8")
    assert content_hash_path(f) != ref.content_hash

    env = normalize_provider_output(
        project_id="p",
        trace_id="t",
        sender_agent=AgentName.director.value,
        sender_provider="anthropic",
        sender_model="sonnet",
        recipient_agent=AgentName.timeline_planner.value,
        task_type=TaskType.create_story_plan,
        status=AgentStatus.COMPLETED,
        payload=DirectorPayload(selected_segment_ids=["still_01#s001"]),
        input_artifact_refs=[ref],
    )
    result = HandoffValidator(
        allowed_segment_ids=ALLOWED,
        project_dir=tmp_path,
        expected_input_hashes={ref.artifact_id: ref.content_hash},
    ).validate(env)
    assert not result.ok
    assert ReasonCode.STALE_ARTIFACT in result.reason_codes


def test_needs_escalation_routes_to_escalation_model():
    env = normalize_provider_output(
        project_id="p",
        trace_id="t",
        sender_agent=AgentName.asset_analysis.value,
        sender_provider="anthropic",
        sender_model="haiku",
        recipient_agent=AgentName.orchestrator.value,
        task_type=TaskType.analyze_assets,
        status=AgentStatus.NEEDS_ESCALATION,
        payload=AnalysisPayload(),
        requested_capability="escalation_model",
    )
    # NEEDS_ESCALATION sets recommended action in validator of envelope
    assert env.recommended_next_action == NextAction.CALL_ESCALATION_MODEL
    assert route_after_envelope(env) == NextAction.CALL_ESCALATION_MODEL
    assert next_action_for_status(AgentStatus.NEEDS_ESCALATION) == NextAction.CALL_ESCALATION_MODEL


def test_retry_count_capped():
    env = normalize_provider_output(
        project_id="p",
        trace_id="t",
        sender_agent=AgentName.asset_analysis.value,
        sender_provider="anthropic",
        sender_model="haiku",
        recipient_agent=AgentName.orchestrator.value,
        task_type=TaskType.analyze_assets,
        status=AgentStatus.FAILED_RETRYABLE,
        payload=AnalysisPayload(),
        usage=UsageSummary(retry_count=0),
    )
    assert route_after_envelope(env, retry_count=0) == NextAction.RETRY_ONCE
    assert route_after_envelope(env, retry_count=MAX_RETRY_PER_TASK) == NextAction.DETERMINISTIC_FALLBACK
    assert route_after_envelope(env, retry_count=99) == NextAction.DETERMINISTIC_FALLBACK


def test_claude_and_gpt_normalize_to_same_envelope_shape():
    payload = EvaluationPayload(
        evaluation_artifact_id="art_eval",
        failure_codes=[ReasonCode.PROMPT_LEAKAGE],
        requires_revision=True,
    )
    claude = normalize_provider_output(
        project_id="p",
        trace_id="t1",
        sender_agent=AgentName.evaluation.value,
        sender_provider="anthropic",
        sender_model="sonnet",
        recipient_agent=AgentName.repair.value,
        task_type=TaskType.evaluate_plan,
        status=AgentStatus.NEEDS_REVIEW,
        payload=payload,
    )
    gpt = normalize_provider_output(
        project_id="p",
        trace_id="t1",
        sender_agent=AgentName.evaluation.value,
        sender_provider="openai",
        sender_model="gpt-5.6-luna",
        recipient_agent=AgentName.repair.value,
        task_type=TaskType.evaluate_plan,
        status=AgentStatus.NEEDS_REVIEW,
        payload=payload,
    )
    assert claude.protocol_version == gpt.protocol_version == PROTOCOL_VERSION
    assert set(claude.model_dump(mode="json").keys()) == set(gpt.model_dump(mode="json").keys())
    assert isinstance(claude.payload, EvaluationPayload)
    assert isinstance(gpt.payload, EvaluationPayload)
    # Roundtrip JSON
    AgentEnvelope.model_validate_json(claude.model_dump_json())
    AgentEnvelope.model_validate_json(gpt.model_dump_json())


def test_context_builders_are_minimal():
    analyses = [_analysis(s) for s in sorted(ALLOWED)]
    a_ctx = build_analysis_context(
        segment_id="still_01#s001",
        evidence_frame_ids=["frames/still_01#s001.jpg"],
        deterministic_features={"audio_rms": 0.1, "is_static": True, "unused_huge": "x" * 5000},
        user_intent="감성적인 유튜브 브이로그로 만들어줘",
        neighbor_summaries=[{"segment_id": "still_02#s001"}],
    )
    assert context_keys(a_ctx) == {
        "segment_id",
        "evidence_frame_ids",
        "deterministic_features",
        "user_intent_brief",
        "neighbor_summaries",
    }
    assert "unused_huge" not in a_ctx["deterministic_features"]

    d_ctx = build_director_context(user_intent="x", analyses=analyses)
    assert "analyses_summary" in d_ctx and "chronology_segment_ids" in d_ctx
    assert "raw_frames" not in d_ctx

    story = _story()
    tl = TimelinePlan(
        project="p",
        clips=[
            PlannedClip(segment_id=s, target_duration_sec=2.0) for s in sorted(ALLOWED)
        ],
        total_target_duration_sec=12,
    )
    e_ctx = build_evaluator_context(
        user_intent=story.user_intent,
        story=story,
        timeline=tl,
        analyses=analyses,
    )
    assert "selected_analyses" in e_ctx and "alternate_candidates" in e_ctx
    assert "full_candidates_json" not in e_ctx

    ev = PlanEvaluation(
        overall_score=0.4,
        narrative_coherence=0.5,
        hook_strength=0.5,
        redundancy_score=0.5,
        chronology_score=0.5,
        caption_grounding=0.5,
        prompt_leakage=0.9,
        crop_safety=0.5,
        visual_variety=0.5,
        duration_suitability=0.5,
        evaluator_provider="openai",
        evaluator_model="luna",
        requires_revision=True,
    )
    r_ctx = build_repair_context(
        user_intent=story.user_intent,
        original_plan=story,
        current_plan=story,
        evaluation=ev,
        failure_segment_ids=["still_01#s001"],
        alternate_candidates=analyses,
    )
    assert "mutable_fields" in r_ctx and "failure_reason_codes" in r_ctx
    assert "forbidden" in r_ctx


def test_trace_reproducible_and_no_secrets(tmp_path: Path):
    store = AgentTraceStore(project_id="demo", trace_id="tr_test")
    env = normalize_provider_output(
        project_id="demo",
        trace_id="tr_test",
        sender_agent=AgentName.director.value,
        sender_provider="anthropic",
        sender_model="sonnet",
        recipient_agent=AgentName.timeline_planner.value,
        task_type=TaskType.create_story_plan,
        status=AgentStatus.COMPLETED,
        payload=DirectorPayload(selected_segment_ids=["still_01#s001"]),
        usage=UsageSummary(
            provider="anthropic",
            model="sonnet",
            input_tokens=10,
            output_tokens=5,
            estimated_cost_usd=0.001,
            latency_ms=12.0,
        ),
    )
    store.record_envelope(env, next_action=NextAction.PROCEED)
    path = store.save(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["trace_id"] == "tr_test"
    assert data["events"][0]["agent"] == "director"
    assert data["events"][0]["task_type"] == "create_story_plan"
    html_path = write_trace_report(tmp_path, store)
    assert html_path.exists()
    body = html_path.read_text(encoding="utf-8")
    assert "API keys" in body or "api keys" in body.lower() or "No API keys" in body
    assert "sk-" not in body
    assert "data:image" not in body


def test_common_preamble_reusable():
    assert "independent role-scoped agent" in COMMON_AGENT_SYSTEM_PREAMBLE
    text = with_common_preamble("You are the director.")
    assert text.startswith(COMMON_AGENT_SYSTEM_PREAMBLE.strip()[:40])
    assert "director" in text


def test_evidence_agent_cannot_create_story_plan():
    with pytest.raises(PermissionError):
        assert_agent_may(AgentName.evidence, TaskType.create_story_plan)


def test_validate_or_raise():
    env = normalize_provider_output(
        project_id="p",
        trace_id="t",
        sender_agent=AgentName.asset_analysis.value,
        sender_provider="openai",
        sender_model="luna",
        recipient_agent=AgentName.director.value,
        task_type=TaskType.analyze_assets,
        status=AgentStatus.COMPLETED,
        payload=AnalysisPayload(invented_timestamps=[9.9]),
    )
    with pytest.raises(HandoffError):
        HandoffValidator(allowed_segment_ids=ALLOWED).validate_or_raise(env)
