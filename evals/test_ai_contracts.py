"""PROMPT 1 — hybrid AI schema + provider contract tests (no live API calls)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from pipeline.ai.anthropic_provider import AnthropicProvider
from pipeline.ai.config import ModelConfig, load_model_config
from pipeline.ai.openai_provider import OpenAIProvider
from pipeline.ai.router import ModelRouter
from pipeline.ai.schemas import (
    AssetAnalysis,
    MediaType,
    Mood,
    PlanEvaluation,
    PlannedClip,
    ShotType,
    StoryPlan,
    StylePreset,
    TimelinePlan,
    story_plan_against_candidates,
    timeline_against_candidates,
)
from pipeline.ai.usage import CallUsage

ALLOWED = {"still_01#s001", "still_02#s001", "still_03#s001"}


def _analysis(segment_id: str = "still_01#s001", **over: Any) -> AssetAnalysis:
    base = {
        "asset_id": f"asset:{segment_id}",
        "segment_id": segment_id,
        "source_file": f"{segment_id.split('#')[0]}.mp4",
        "media_type": MediaType.still_video,
        "upload_index": 0,
        "evidence_frame_ids": [f"frames/{segment_id}.jpg"],
        "subjects": ["person"],
        "mood": Mood.calm,
        "technical_quality": 0.7,
        "aesthetic_value": 0.6,
        "emotional_value": 0.5,
        "narrative_value": 0.5,
        "hook_potential": 0.4,
        "motion_quality": 0.2,
        "novelty": 0.5,
        "focus_x": 0.5,
        "focus_y": 0.45,
        "focus_width": 0.5,
        "focus_height": 0.55,
        "crop_confidence": 0.4,
        "visually_grounded_facts": ["person centered"],
        "uncertainty": 0.2,
        "analysis_provider": "anthropic",
        "analysis_model": "claude-haiku-4-5-20251001",
    }
    base.update(over)
    return AssetAnalysis.model_validate(base)


def _story(**over: Any) -> StoryPlan:
    base = {
        "user_intent": "감성적인 유튜브 브이로그로 만들어줘",
        "concept": "quiet afternoon walk",
        "tone": Mood.nostalgic,
        "target_duration_sec": 12.0,
        "style_preset": StylePreset.soft_vlog,
        "hook_segment_id": "still_01#s001",
        "ending_segment_id": "still_03#s001",
        "selected_segment_ids": ["still_01#s001", "still_02#s001", "still_03#s001"],
        "narrative_arc": ["hook", "walk", "close"],
        "confidence": 0.7,
        "provider": "anthropic",
        "model": "claude-sonnet-4-5-20250929",
    }
    base.update(over)
    return StoryPlan.model_validate(base)


# --- schema ser/de + ranges ------------------------------------------------


def test_asset_analysis_roundtrip_json():
    a = _analysis()
    raw = a.model_dump_json()
    b = AssetAnalysis.model_validate_json(raw)
    assert b == a


def test_story_and_timeline_roundtrip():
    plan = _story()
    timeline = TimelinePlan(
        project="demo",
        clips=[
            PlannedClip(
                segment_id="still_01#s001",
                target_duration_sec=2.0,
                evidence_frame_ids=["frames/still_01#s001.jpg"],
            ),
            PlannedClip(
                segment_id="still_02#s001",
                target_duration_sec=2.0,
            ),
        ],
        total_target_duration_sec=12.0,
        style_preset=StylePreset.y2k_4x3_letterbox,
    )
    assert StoryPlan.model_validate_json(plan.model_dump_json()) == plan
    assert TimelinePlan.model_validate_json(timeline.model_dump_json()) == timeline


def test_score_out_of_range_rejected():
    with pytest.raises(ValidationError):
        _analysis(technical_quality=1.5)
    with pytest.raises(ValidationError):
        _analysis(focus_x=-0.1)


def test_unknown_segment_id_rejected_by_guard():
    plan = _story(selected_segment_ids=["still_01#s001", "ghost#s999"], hook_segment_id="still_01#s001", ending_segment_id="still_01#s001")
    # StoryPlan itself allows any string; guard rejects against candidates.
    with pytest.raises(ValueError, match="unknown segment_id"):
        story_plan_against_candidates(plan, ALLOWED)


def test_story_plan_hook_must_be_selected():
    with pytest.raises(ValidationError):
        _story(
            hook_segment_id="still_99#s001",
            selected_segment_ids=["still_01#s001", "still_02#s001"],
            ending_segment_id="still_02#s001",
        )


def test_timeline_against_candidates_rejects_unknown():
    tl = TimelinePlan(
        project="x",
        clips=[PlannedClip(segment_id="nope#s001", target_duration_sec=1.0)],
        total_target_duration_sec=12.0,
    )
    with pytest.raises(ValueError, match="unknown segment_id"):
        timeline_against_candidates(tl, ALLOWED)


def test_call_usage_estimates_cost():
    u = CallUsage(
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        input_tokens=1000,
        output_tokens=500,
        cache_tokens=200,
        latency_ms=12.5,
        operation="analyze_assets",
    )
    d = u.as_dict()
    assert d["estimated_cost_usd"] > 0
    assert d["provider"] == "anthropic"


# --- Anthropic mock --------------------------------------------------------


class _FakeAnthropicMessage:
    def __init__(self, tool_name: str, payload: dict[str, Any]):
        self.content = [
            SimpleNamespace(type="tool_use", name=tool_name, input=payload),
        ]
        self.usage = SimpleNamespace(
            input_tokens=11,
            output_tokens=22,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=5,
        )


class _FakeAnthropicClient:
    def __init__(self, tool_name: str, payload: dict[str, Any]):
        self.messages = self
        self._tool_name = tool_name
        self._payload = payload
        self.last_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _FakeAnthropicMessage:
        self.last_kwargs = kwargs
        assert kwargs["tool_choice"]["type"] == "tool"
        assert kwargs["tool_choice"]["name"] == self._tool_name
        return _FakeAnthropicMessage(self._tool_name, self._payload)


def test_anthropic_provider_analyze_assets_mock(tmp_path: Path):
    img = tmp_path / "f.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")  # minimal jpeg-ish
    payload = {
        "analyses": [
            _analysis("still_01#s001").model_dump(mode="json"),
        ]
    }
    client = _FakeAnthropicClient("submit_asset_analyses", payload)
    provider = AnthropicProvider(client=client, default_model="claude-haiku-4-5-20251001")
    analyses, usage = provider.analyze_assets(
        image_paths=[img],
        segment_ids=["still_01#s001"],
        source_files=["still_01.mp4"],
        allowed_segment_ids=ALLOWED,
    )
    assert analyses[0].segment_id == "still_01#s001"
    assert usage.provider == "anthropic"
    assert usage.input_tokens == 11
    assert usage.cache_tokens == 5
    assert client.last_kwargs is not None


def test_anthropic_provider_rejects_unknown_segment_in_tool_payload(tmp_path: Path):
    img = tmp_path / "f.jpg"
    img.write_bytes(b"\xff\xd8\xff\xd9")
    bad = _analysis("ghost#s001").model_dump(mode="json")
    client = _FakeAnthropicClient("submit_asset_analyses", {"analyses": [bad]})
    provider = AnthropicProvider(client=client)
    with pytest.raises(ValueError, match="unknown segment_id"):
        provider.analyze_assets(
            image_paths=[img],
            segment_ids=["still_01#s001"],
            source_files=["still_01.mp4"],
            allowed_segment_ids=ALLOWED,
        )


def test_anthropic_create_story_plan_mock():
    client = _FakeAnthropicClient("submit_story_plan", _story().model_dump(mode="json"))
    provider = AnthropicProvider(client=client)
    plan, usage = provider.create_story_plan(
        user_intent="감성적인 유튜브 브이로그로 만들어줘",
        analyses=[_analysis("still_01#s001"), _analysis("still_02#s001", upload_index=1), _analysis("still_03#s001", upload_index=2)],
        allowed_segment_ids=ALLOWED,
        target_duration_sec=12.0,
    )
    assert plan.hook_segment_id in ALLOWED
    assert usage.operation == "create_story_plan"


# --- OpenAI mock -----------------------------------------------------------


class _FakeOpenAIResponse:
    def __init__(self, payload: dict[str, Any]):
        self.output_text = json.dumps(payload)
        self.usage = SimpleNamespace(
            input_tokens=9,
            output_tokens=13,
            input_tokens_details=SimpleNamespace(cached_tokens=2),
        )
        self.output = []


class _FakeOpenAIClient:
    def __init__(self, payload: dict[str, Any]):
        self.responses = self
        self._payload = payload
        self.last_kwargs: dict[str, Any] | None = None

    def create(self, **kwargs: Any) -> _FakeOpenAIResponse:
        self.last_kwargs = kwargs
        assert "format" in kwargs["text"]
        assert kwargs["text"]["format"]["type"] == "json_schema"
        assert kwargs["text"]["format"]["strict"] is True
        return _FakeOpenAIResponse(self._payload)


def test_openai_provider_evaluate_plan_mock():
    evaluation = PlanEvaluation(
        overall_score=0.4,
        narrative_coherence=0.5,
        hook_strength=0.3,
        redundancy_score=0.8,
        chronology_score=0.6,
        caption_grounding=0.2,
        prompt_leakage=0.9,
        crop_safety=0.7,
        visual_variety=0.4,
        duration_suitability=0.5,
        requires_revision=True,
        evaluator_provider="openai",
        evaluator_model="gpt-5.6-luna",
    )
    client = _FakeOpenAIClient(evaluation.model_dump(mode="json"))
    provider = OpenAIProvider(client=client, default_model="gpt-5.6-luna")
    result, usage = provider.evaluate_plan(
        user_intent="감성적인 유튜브 브이로그로 만들어줘",
        plan=_story(),
        analyses=[_analysis()],
        allowed_segment_ids=ALLOWED,
    )
    assert result.prompt_leakage == 0.9
    assert usage.provider == "openai"
    assert usage.cache_tokens == 2
    assert client.last_kwargs["text"]["format"]["name"] == "plan_evaluation"


def test_openai_provider_create_story_plan_mock():
    client = _FakeOpenAIClient(_story(provider="openai", model="gpt-5.6-sol").model_dump(mode="json"))
    provider = OpenAIProvider(client=client, default_model="gpt-5.6-sol")
    plan, usage = provider.create_story_plan(
        user_intent="감성적인 유튜브 브이로그로 만들어줘",
        analyses=[_analysis("still_01#s001"), _analysis("still_02#s001"), _analysis("still_03#s001")],
        allowed_segment_ids=ALLOWED,
        target_duration_sec=12,
    )
    assert plan.provider == "openai"
    assert usage.model == "gpt-5.6-sol"


# --- ModelRouter config ----------------------------------------------------


def test_load_model_config_defaults(monkeypatch: pytest.MonkeyPatch):
    for key in list(monkeypatch.__dict__.get("_setitem", {}) or []):
        pass
    monkeypatch.delenv("ZLOG_ANALYZER_PROVIDER", raising=False)
    monkeypatch.delenv("ZLOG_EVALUATOR_PROVIDER", raising=False)
    monkeypatch.setenv("ZLOG_ANALYZER_PROVIDER", "anthropic")
    monkeypatch.setenv("ZLOG_DIRECTOR_PROVIDER", "anthropic")
    monkeypatch.setenv("ZLOG_EVALUATOR_PROVIDER", "openai")
    monkeypatch.setenv("ZLOG_REPAIR_PROVIDER", "openai")
    monkeypatch.setenv("ZLOG_ANALYZER_MODEL", "claude-haiku-4-5-20251001")
    monkeypatch.setenv("ZLOG_DIRECTOR_MODEL", "claude-sonnet-4-5-20250929")
    monkeypatch.setenv("ZLOG_EVALUATOR_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("ZLOG_REPAIR_MODEL", "gpt-5.6-sol")
    cfg = load_model_config(load_env=False)
    assert cfg.analyzer_provider == "anthropic"
    assert cfg.evaluator_provider == "openai"
    assert cfg.repair_model == "gpt-5.6-sol"


def test_model_router_routes_evaluate_to_openai(monkeypatch: pytest.MonkeyPatch):
    cfg = ModelConfig(
        analyzer_provider="anthropic",
        analyzer_model="claude-haiku-4-5-20251001",
        analyzer_escalation_model="claude-sonnet-4-5-20250929",
        director_provider="anthropic",
        director_model="claude-sonnet-4-5-20250929",
        evaluator_provider="openai",
        evaluator_model="gpt-5.6-luna",
        repair_provider="openai",
        repair_model="gpt-5.6-sol",
        quality_mode="balanced",
    )

    class _Spy:
        name = "openai"

        def evaluate_plan(self, **kwargs: Any):
            assert kwargs["model"] == "gpt-5.6-luna"
            ev = PlanEvaluation(
                overall_score=0.5,
                narrative_coherence=0.5,
                hook_strength=0.5,
                redundancy_score=0.5,
                chronology_score=0.5,
                caption_grounding=0.5,
                prompt_leakage=0.0,
                crop_safety=0.5,
                visual_variety=0.5,
                duration_suitability=0.5,
                evaluator_provider="openai",
                evaluator_model=kwargs["model"],
            )
            return ev, CallUsage(provider="openai", model=kwargs["model"], operation="evaluate_plan")

        def analyze_assets(self, **kwargs: Any):
            raise NotImplementedError

        def create_story_plan(self, **kwargs: Any):
            raise NotImplementedError

        def repair_story_plan(self, **kwargs: Any):
            raise NotImplementedError

    router = ModelRouter(cfg, openai=_Spy())
    ev, usage = router.evaluate_plan(
        user_intent="x",
        plan=_story(),
        analyses=[_analysis()],
        allowed_segment_ids=ALLOWED,
    )
    assert ev.evaluator_model == "gpt-5.6-luna"
    assert usage.provider == "openai"
    desc = router.describe()
    assert desc["evaluator"]["provider"] == "openai"
    assert desc["director"]["provider"] == "anthropic"


def test_router_uncertainty_escalation_model():
    cfg = ModelConfig(
        analyzer_provider="anthropic",
        analyzer_model="claude-haiku-4-5-20251001",
        analyzer_escalation_model="claude-sonnet-4-5-20250929",
        director_provider="anthropic",
        director_model="claude-sonnet-4-5-20250929",
        evaluator_provider="openai",
        evaluator_model="gpt-5.6-luna",
        repair_provider="openai",
        repair_model="gpt-5.6-sol",
        quality_mode="balanced",
    )
    router = ModelRouter(cfg)
    assert router.analyzer_model_for_uncertainty(0.1) == "claude-haiku-4-5-20251001"
    assert router.analyzer_model_for_uncertainty(0.9) == "claude-sonnet-4-5-20250929"


def test_shot_type_enum_serialization():
    a = _analysis(shot_type=ShotType.close)
    data = json.loads(a.model_dump_json())
    assert data["shot_type"] == "close"
