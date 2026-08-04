"""PROMPT 8 — code evaluation + Luna/Sol repair gates (no live API)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from pipeline.ai.config import ModelConfig
from pipeline.ai.schemas import (
    AssetAnalysis,
    ClipRole,
    EvaluationFailure,
    FitMode,
    MediaType,
    Mood,
    MotionKind,
    PlanEvaluation,
    PlannedClip,
    SceneKind,
    ShotType,
    StoryPlan,
    StylePreset,
    TimelinePlan,
    TransitionKind,
)
from pipeline.ai.usage import CallUsage
from pipeline.evaluate_plan import (
    code_evaluate_plan,
    evaluate_and_repair,
    important_failures,
    judgments_conflict,
    merge_evaluations,
    should_call_sol,
)

IDS = ["still_01#s001", "still_02#s001", "still_03#s001", "still_04#s001"]


def _analysis(sid: str, **over: Any) -> AssetAnalysis:
    idx = IDS.index(sid) if sid in IDS else 0
    base = {
        "asset_id": f"asset:{sid}",
        "segment_id": sid,
        "source_file": f"{sid.split('#')[0]}.mp4",
        "media_type": MediaType.still_video,
        "upload_index": idx,
        "capture_time": f"2024-01-0{idx + 1}T12:00:00",
        "evidence_frame_ids": [f"frames/{sid}.jpg"],
        "subjects": ["person"],
        "scene": SceneKind.outdoor,
        "shot_type": ShotType.medium,
        "mood": Mood.calm,
        "technical_quality": 0.7,
        "aesthetic_value": 0.6,
        "emotional_value": 0.5,
        "narrative_value": 0.6,
        "hook_potential": 0.7,
        "motion_quality": 0.2,
        "novelty": 0.6,
        "focus_x": 0.5,
        "focus_y": 0.45,
        "crop_confidence": 0.7,
        "visually_grounded_facts": ["person outdoors"],
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
        "tone": Mood.calm,
        "target_duration_sec": 12.0,
        "style_preset": StylePreset.clean_vlog,
        "hook_segment_id": IDS[0],
        "ending_segment_id": IDS[2],
        "selected_segment_ids": IDS[:3],
        "narrative_arc": ["fact: person outdoors", "mood: calm"],
        "confidence": 0.7,
        "provider": "anthropic",
        "model": "claude-sonnet-4-5-20250929",
        "allow_asset_reuse": False,
        "caption_mode": "sparse",
    }
    base.update(over)
    return StoryPlan.model_validate(base)


def _clip(sid: str, **over: Any) -> PlannedClip:
    base = {
        "segment_id": sid,
        "role": ClipRole.body,
        "target_duration_sec": 2.0,
        "fit_mode": FitMode.blurred_background_contain,
        "focus_x": 0.5,
        "focus_y": 0.45,
        "motion": MotionKind.ken_burns_in,
        "motion_strength": 0.3,
        "transition": TransitionKind.cut,
        "caption": "",
        "caption_grounding": "",
    }
    base.update(over)
    return PlannedClip.model_validate(base)


def _timeline(clips: list[PlannedClip], **over: Any) -> TimelinePlan:
    base = {
        "project": "demo",
        "clips": clips,
        "total_target_duration_sec": 12.0,
        "style_preset": StylePreset.clean_vlog,
    }
    base.update(over)
    return TimelinePlan.model_validate(base)


def _cfg(mode: str = "balanced") -> ModelConfig:
    return ModelConfig(
        analyzer_provider="anthropic",
        analyzer_model="claude-haiku-4-5-20251001",
        analyzer_escalation_model="claude-sonnet-4-5-20250929",
        director_provider="anthropic",
        director_model="claude-sonnet-4-5-20250929",
        evaluator_provider="openai",
        evaluator_model="gpt-5.6-luna",
        repair_provider="openai",
        repair_model="gpt-5.6-sol",
        quality_mode=mode,  # type: ignore[arg-type]
    )


def _luna_ok(**kwargs: Any) -> tuple[PlanEvaluation, CallUsage]:
    ev = PlanEvaluation(
        overall_score=0.9,
        narrative_coherence=0.9,
        hook_strength=0.9,
        redundancy_score=0.1,
        chronology_score=0.9,
        caption_grounding=0.9,
        prompt_leakage=0.05,
        crop_safety=0.9,
        visual_variety=0.9,
        duration_suitability=0.9,
        failures=[],
        requires_revision=False,
        evaluator_provider="openai",
        evaluator_model="gpt-5.6-luna",
    )
    return ev, CallUsage(provider="openai", model="gpt-5.6-luna", operation="evaluate_plan")


def _luna_fail(**kwargs: Any) -> tuple[PlanEvaluation, CallUsage]:
    ev = PlanEvaluation(
        overall_score=0.3,
        narrative_coherence=0.4,
        hook_strength=0.2,
        redundancy_score=0.8,
        chronology_score=0.4,
        caption_grounding=0.2,
        prompt_leakage=0.9,
        crop_safety=0.3,
        visual_variety=0.3,
        duration_suitability=0.3,
        failures=[
            EvaluationFailure(
                code="prompt_leakage",
                message="brief leaked",
                segment_id=IDS[0],
            )
        ],
        requires_revision=True,
        evaluator_provider="openai",
        evaluator_model="gpt-5.6-luna",
    )
    return ev, CallUsage(provider="openai", model="gpt-5.6-luna", operation="evaluate_plan")


# --- code checks -----------------------------------------------------------


def test_prompt_leakage_detected():
    brief = "감성적인 유튜브 브이로그로 만들어줘"
    story = _story(user_intent=brief, narrative_arc=[f"caption: {brief}"])
    timeline = _timeline(
        [
            _clip(IDS[0], caption=brief, caption_grounding="facts"),
            _clip(IDS[1]),
            _clip(IDS[2]),
        ]
    )
    fails = code_evaluate_plan(
        user_intent=brief,
        story=story,
        timeline=timeline,
        analyses=[_analysis(s) for s in IDS[:3]],
    )
    assert any(f.code == "prompt_leakage" for f in fails)


def test_segment_repeat_detected():
    timeline = _timeline(
        [_clip(IDS[0]), _clip(IDS[1]), _clip(IDS[0]), _clip(IDS[2])]
    )
    fails = code_evaluate_plan(
        user_intent="make a calm vlog",
        story=_story(),
        timeline=timeline,
        analyses=[_analysis(s) for s in IDS[:3]],
    )
    assert any(f.code == "segment_repeat" and f.segment_id == IDS[0] for f in fails)


def test_redundancy_group_repeat():
    analyses = [
        _analysis(IDS[0], redundancy_group="cafe_A"),
        _analysis(IDS[1], redundancy_group="cafe_A"),
        _analysis(IDS[2], redundancy_group="street"),
    ]
    timeline = _timeline([_clip(IDS[0]), _clip(IDS[1]), _clip(IDS[2])])
    fails = code_evaluate_plan(
        user_intent="calm vlog",
        story=_story(),
        timeline=timeline,
        analyses=analyses,
    )
    assert any(f.code == "redundancy_repeat" for f in fails)


def test_ungrounded_caption_hallucination():
    timeline = _timeline(
        [
            _clip(IDS[0], caption="secret romance", caption_grounding=""),
            _clip(IDS[1]),
            _clip(IDS[2]),
        ]
    )
    fails = code_evaluate_plan(
        user_intent="calm vlog",
        story=_story(),
        timeline=timeline,
        analyses=[_analysis(s) for s in IDS[:3]],
    )
    assert any(f.code == "ungrounded_caption" and f.segment_id == IDS[0] for f in fails)


def test_chronology_violation():
    analyses = [
        _analysis(IDS[0], upload_index=0, capture_time="2024-01-01T10:00:00"),
        _analysis(IDS[1], upload_index=2, capture_time="2024-01-03T10:00:00"),
        _analysis(IDS[2], upload_index=1, capture_time="2024-01-02T10:00:00"),
    ]
    # hook cold-open OK; body IDS[1] then IDS[2] regresses in time
    timeline = _timeline([_clip(IDS[0]), _clip(IDS[1]), _clip(IDS[2])])
    fails = code_evaluate_plan(
        user_intent="calm vlog",
        story=_story(),
        timeline=timeline,
        analyses=analyses,
    )
    assert any(f.code == "chronology_violation" for f in fails)


def test_risky_crop():
    analyses = [_analysis(s, crop_confidence=0.1) for s in IDS[:3]]
    timeline = _timeline(
        [
            _clip(IDS[0], fit_mode=FitMode.subject_aware_cover),
            _clip(IDS[1]),
            _clip(IDS[2]),
        ]
    )
    fails = code_evaluate_plan(
        user_intent="calm vlog",
        story=_story(),
        timeline=timeline,
        analyses=analyses,
    )
    assert any(f.code == "risky_crop" and f.segment_id == IDS[0] for f in fails)


def test_weak_hook():
    analyses = [
        _analysis(IDS[0], hook_potential=0.1),
        _analysis(IDS[1]),
        _analysis(IDS[2]),
    ]
    fails = code_evaluate_plan(
        user_intent="calm vlog",
        story=_story(),
        timeline=_timeline([_clip(s) for s in IDS[:3]]),
        analyses=analyses,
    )
    assert any(f.code == "weak_hook" for f in fails)


def test_excessive_duration():
    story = _story(target_duration_sec=60.0, selected_segment_ids=IDS[:2], ending_segment_id=IDS[1])
    timeline = _timeline(
        [_clip(IDS[0]), _clip(IDS[1])],
        total_target_duration_sec=60.0,
    )
    fails = code_evaluate_plan(
        user_intent="calm vlog",
        story=story,
        timeline=timeline,
        analyses=[_analysis(IDS[0]), _analysis(IDS[1])],
    )
    assert any(f.code == "excessive_duration" for f in fails)


def test_style_conflict_clean_vs_y2k_effects():
    edl = {
        "aesthetic": {
            "camcorder_osd": True,
            "scanlines": True,
            "lut": "ccd_cool_01.cube",
            "allow_flash": True,
        },
        "signature": {"enabled": True},
        "timeline": [{"segment_id": IDS[0], "transition": "flash"}],
    }
    fails = code_evaluate_plan(
        user_intent="calm vlog",
        story=_story(style_preset=StylePreset.clean_vlog),
        timeline=_timeline([_clip(s) for s in IDS[:3]]),
        analyses=[_analysis(s) for s in IDS[:3]],
        edl=edl,
    )
    assert any(f.code == "style_conflict" for f in fails)


# --- Sol gates -------------------------------------------------------------


def test_should_call_sol_requires_premium_and_claude_failure():
    assert not should_call_sol(
        quality_mode="balanced",
        important_failures_remain=True,
        claude_revision_failed=True,
        judgments_conflict_flag=True,
    )
    assert not should_call_sol(
        quality_mode="premium",
        important_failures_remain=True,
        claude_revision_failed=False,
        judgments_conflict_flag=True,
    )
    assert should_call_sol(
        quality_mode="premium",
        important_failures_remain=True,
        claude_revision_failed=True,
        judgments_conflict_flag=False,
    )
    assert should_call_sol(
        quality_mode="max",
        important_failures_remain=True,
        claude_revision_failed=True,
        judgments_conflict_flag=True,
    )


def test_judgments_conflict_high_confidence_low_luna():
    story = _story(confidence=0.9)
    luna, _ = _luna_fail()
    assert judgments_conflict(
        code_failures=[],
        luna=luna,
        story=story,
    )


# --- stage integration with fakes -----------------------------------------


def _write_project(tmp: Path, story: StoryPlan, timeline: TimelinePlan, analyses: list[AssetAnalysis]) -> Path:
    project = "eval_demo"
    d = tmp / project
    d.mkdir(parents=True)
    (d / "note.txt").write_text(story.user_intent, encoding="utf-8")
    (d / "story_plan.json").write_text(
        json.dumps({"project": project, "plan": story.model_dump(mode="json")}, ensure_ascii=False),
        encoding="utf-8",
    )
    (d / "timeline_plan.json").write_text(
        json.dumps({"project": project, "plan": timeline.model_dump(mode="json")}, ensure_ascii=False),
        encoding="utf-8",
    )
    (d / "asset_analyses.json").write_text(
        json.dumps({"analyses": [a.model_dump(mode="json") for a in analyses]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return d


def test_balanced_falls_back_to_baseline_without_sol(tmp_path: Path):
    brief = "감성적인 유튜브 브이로그로 만들어줘"
    story = _story(user_intent=brief, narrative_arc=[f"caption: {brief}"], confidence=0.9)
    timeline = _timeline(
        [
            _clip(IDS[0], caption=brief, caption_grounding=""),
            _clip(IDS[1]),
            _clip(IDS[2]),
        ]
    )
    analyses = [_analysis(s) for s in IDS[:3]]
    project_dir = _write_project(tmp_path, story, timeline, analyses)

    def claude_noop(**kwargs: Any) -> tuple[StoryPlan, CallUsage]:
        # "revise" but keep the leak — forces second-pass failure
        return kwargs["plan"], CallUsage(
            provider="anthropic", model="claude-sonnet-4-5-20250929", operation="repair_story_plan"
        )

    sol_calls = {"n": 0}

    def sol_spy(**kwargs: Any) -> tuple[StoryPlan, CallUsage]:
        sol_calls["n"] += 1
        return kwargs["plan"], CallUsage(
            provider="openai", model="gpt-5.6-sol", operation="repair_story_plan"
        )

    out = evaluate_and_repair(
        tmp_path,
        project_dir.name,
        bgm_track=None,
        config=_cfg("balanced"),
        force=True,
        luna_fn=_luna_fail,
        claude_revise_fn=claude_noop,
        sol_repair_fn=sol_spy,
        rebuild_timeline=False,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["outcome"] == "baseline_fallback"
    assert data["sol_used"] is False
    assert sol_calls["n"] == 0
    assert not (project_dir / "sol_repair.json").exists()
    usage = json.loads((project_dir / "plan_evaluation_usage.json").read_text(encoding="utf-8"))
    assert "estimated_cost_usd_total" in usage
    assert (project_dir / "story_plan_revision.json").exists()


def test_premium_calls_sol_once_when_claude_fails(tmp_path: Path):
    brief = "감성적인 유튜브 브이로그로 만들어줘"
    story = _story(user_intent=brief, narrative_arc=[f"caption: {brief}"], confidence=0.9)
    timeline = _timeline(
        [
            _clip(IDS[0], caption=brief, caption_grounding=""),
            _clip(IDS[1]),
            _clip(IDS[2]),
        ]
    )
    analyses = [_analysis(s, hook_potential=0.8) for s in IDS[:3]]
    project_dir = _write_project(tmp_path, story, timeline, analyses)

    def claude_noop(**kwargs: Any) -> tuple[StoryPlan, CallUsage]:
        return kwargs["plan"], CallUsage(
            provider="anthropic", model="claude-sonnet-4-5-20250929", operation="repair_story_plan"
        )

    clean = _story(
        user_intent=brief,
        narrative_arc=["fact: person outdoors", "mood: calm"],
        confidence=0.7,
    )

    def sol_fix(**kwargs: Any) -> tuple[StoryPlan, CallUsage]:
        return clean, CallUsage(
            provider="openai", model="gpt-5.6-sol", operation="repair_story_plan"
        )

    out = evaluate_and_repair(
        tmp_path,
        project_dir.name,
        bgm_track=None,
        config=_cfg("premium"),
        force=True,
        luna_fn=_luna_fail,
        claude_revise_fn=claude_noop,
        sol_repair_fn=sol_fix,
        rebuild_timeline=False,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["sol_used"] is True
    assert (project_dir / "sol_repair.json").exists()
    assert data["outcome"] in {"pass_after_sol", "sol_exhausted"}


def test_pass_skips_repair(tmp_path: Path):
    story = _story()
    timeline = _timeline([_clip(s) for s in IDS[:3]])
    analyses = [_analysis(s) for s in IDS[:3]]
    project_dir = _write_project(tmp_path, story, timeline, analyses)

    def boom(**kwargs: Any) -> tuple[StoryPlan, CallUsage]:
        raise AssertionError("should not revise on pass")

    out = evaluate_and_repair(
        tmp_path,
        project_dir.name,
        config=_cfg("premium"),
        force=True,
        luna_fn=_luna_ok,
        claude_revise_fn=boom,
        sol_repair_fn=boom,
        rebuild_timeline=False,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["outcome"] == "pass"
    assert data["sol_used"] is False


def test_merge_marks_revision_on_important_code_fail():
    code = [EvaluationFailure(code="weak_hook", message="weak", segment_id=IDS[0])]
    luna, _ = _luna_ok()
    merged = merge_evaluations(code, luna)
    assert merged.requires_revision
    assert important_failures(merged.failures)


def test_openai_evaluate_includes_timeline_in_prompt():
    from pipeline.ai.openai_provider import OpenAIProvider

    class _Resp:
        output_text = PlanEvaluation(
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
            evaluator_model="gpt-5.6-luna",
        ).model_dump_json()
        usage = None
        output: ClassVar[list[object]] = []

    class _Client:
        def __init__(self) -> None:
            self.last = None

            class _R:
                def __init__(self, outer: _Client) -> None:
                    self._outer = outer

                def create(self, **kwargs: Any) -> _Resp:
                    self._outer.last = kwargs
                    return _Resp()

            self.responses = _R(self)

    client = _Client()
    provider = OpenAIProvider(client=client, default_model="gpt-5.6-luna")
    tl = _timeline([_clip(s) for s in IDS[:3]])
    provider.evaluate_plan(
        user_intent="x",
        plan=_story(),
        analyses=[_analysis(s) for s in IDS[:3]],
        allowed_segment_ids=set(IDS[:3]),
        timeline=tl,
    )
    text = client.last["input"][0]["content"][0]["text"]
    assert "TimelinePlan" in text
    assert "Summarized AssetAnalysis" in text
    assert "EVALUATOR" in text
