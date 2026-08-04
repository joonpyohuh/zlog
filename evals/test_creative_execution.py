"""Creative Execution Layer v1 contracts; no model or network calls."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from pipeline.ai.schemas import (
    AssetAnalysis,
    CaptionMode,
    ClipRole,
    CreativeCallback,
    CreativeDecision,
    CreativeEffectBudget,
    CreativeExecutionPlan,
    EffectId,
    MediaType,
    PlannedClip,
    StoryPlan,
    StylePreset,
)
from pipeline.creative_execution import (
    build_execution_plan,
    choose_callback,
    finalize_comparison,
    preserve_previous_render,
    validate_execution_plan,
)
from pipeline.director import validate_story_plan


def _analysis(segment_id: str, **over: Any) -> AssetAnalysis:
    data: dict[str, Any] = {
        "asset_id": f"asset:{segment_id}",
        "segment_id": segment_id,
        "source_file": f"{segment_id}.mp4",
        "media_type": MediaType.still_video,
        "upload_index": int(segment_id[-1]),
        "subjects": ["person"],
        "action_progression": "person smiles and looks at camera",
        "visually_grounded_facts": ["person outdoors"],
        "hook_potential": 0.8,
        "emotional_value": 0.7,
        "narrative_value": 0.7,
        "crop_confidence": 0.8,
        "analysis_provider": "anthropic",
        "analysis_model": "fixture",
    }
    data.update(over)
    return AssetAnalysis.model_validate(data)


def _story(ids: list[str], **over: Any) -> StoryPlan:
    data: dict[str, Any] = {
        "user_intent": "quiet walk",
        "concept": "quiet walk",
        "target_duration_sec": 10,
        "style_preset": StylePreset.clean_vlog,
        "hook_segment_id": ids[0],
        "ending_segment_id": ids[-1],
        "selected_segment_ids": ids,
        "caption_mode": CaptionMode.none,
        "provider": "anthropic",
        "model": "fixture",
    }
    data.update(over)
    return StoryPlan.model_validate(data)


def _clips(ids: list[str]) -> list[PlannedClip]:
    roles = [ClipRole.hook, ClipRole.orientation, ClipRole.development, ClipRole.zlog_moment, ClipRole.resonance]
    return [
        PlannedClip(segment_id=sid, role=roles[min(i, len(roles) - 1)], target_duration_sec=2)
        for i, sid in enumerate(ids)
    ]


def test_creative_decision_roundtrip_and_unknown_effect_rejected() -> None:
    decision = CreativeDecision(
        segment_id="s1",
        narrative_role=ClipRole.hook,
        cut_reason="cold open",
        primary_effect=EffectId.micro_push_in,
    )
    assert CreativeDecision.model_validate_json(decision.model_dump_json()) == decision
    with pytest.raises(ValidationError):
        CreativeDecision.model_validate(
            decision.model_dump(mode="json") | {"primary_effect": "invented_zoom"}
        )


def test_effect_budget_role_and_low_crop_guards() -> None:
    analyses = {"s1": _analysis("s1", crop_confidence=0.2)}
    bad = CreativeExecutionPlan(
        project="demo",
        creative_intent="x",
        opening_strategy="cold_open",
        ending_strategy="clean_end",
        decisions=[
            CreativeDecision(
                segment_id="s1",
                narrative_role=ClipRole.development,
                cut_reason="x",
                primary_effect=EffectId.reaction_punch_in,
            )
        ],
    )
    with pytest.raises(ValueError, match="not allowed"):
        validate_execution_plan(bad, analyses)

    low_crop = bad.model_copy(
        update={
            "decisions": [
                bad.decisions[0].model_copy(update={"narrative_role": ClipRole.hook})
            ]
        }
    )
    with pytest.raises(ValueError, match="low crop"):
        validate_execution_plan(low_crop, analyses)

    over_budget = CreativeExecutionPlan(
        project="demo",
        creative_intent="x",
        opening_strategy="cold_open",
        ending_strategy="clean_end",
        effect_budget=CreativeEffectBudget(strong_effects_max=1),
        decisions=[
            low_crop.decisions[0].model_copy(update={"segment_id": "s2"}),
            CreativeDecision(
                segment_id="s3",
                narrative_role=ClipRole.zlog_moment,
                cut_reason="x",
                primary_effect=EffectId.freeze_reaction_hold,
            ),
        ],
    )
    with pytest.raises(ValueError, match="budget exceeded"):
        validate_execution_plan(over_budget, {})


def test_callback_requires_reason_and_is_the_only_allowed_duplicate() -> None:
    with pytest.raises(ValidationError):
        CreativeCallback(enabled=True, source_segment_id="s1")

    decision = CreativeDecision(
        segment_id="s1", narrative_role=ClipRole.hook, cut_reason="open"
    )
    callback = CreativeDecision(
        segment_id="s1",
        narrative_role=ClipRole.resonance,
        cut_reason="payoff",
        reuse_reason="opening_callback",
    )
    plan = CreativeExecutionPlan(
        project="demo",
        creative_intent="x",
        opening_strategy="cold_open",
        ending_strategy="ambient_hold",
        callback=CreativeCallback(
            enabled=True,
            source_segment_id="s1",
            reuse_reason="opening_callback",
            alternate_crop=True,
        ),
        decisions=[decision, callback],
    )
    assert plan.callback.enabled
    with pytest.raises(ValidationError):
        CreativeExecutionPlan.model_validate(
            plan.model_dump(mode="json")
            | {"decisions": [decision.model_dump(), callback.model_dump(), callback.model_dump()]}
        )


def test_caption_metadata_is_removed_and_clean_vlog_allows_zero_captions() -> None:
    ids = ["s1", "s2"]
    story = _story(ids, caption_mode=CaptionMode.none)
    analyses = {sid: _analysis(sid) for sid in ids}
    clips = _clips(ids)
    clips[0] = clips[0].model_copy(
        update={"caption": "calm", "caption_grounding": "mood"}
    )
    plan = build_execution_plan("demo", story, clips, analyses)
    assert plan.decisions[0].caption.text == ""
    assert not any("requires captions" in error for error in validate_story_plan(
        story, allowed_segment_ids=set(ids), analyses=list(analyses.values())
    ))


def test_callback_selection_and_execution_plan_are_deterministic() -> None:
    ids = [f"s{i}" for i in range(1, 6)]
    story = _story(ids)
    analyses = {sid: _analysis(sid) for sid in ids}
    clips = _clips(ids)
    callback = choose_callback(story, clips, analyses)
    assert callback.enabled and callback.source_segment_id == "s1"
    first = build_execution_plan("demo", story, clips, analyses)
    second = build_execution_plan("demo", story, clips, analyses)
    assert first.model_dump() == second.model_dump()
    assert first.decisions[0].primary_effect != first.decisions[-1].primary_effect


def test_comparison_preserves_prior_render(tmp_path: Any, monkeypatch: Any) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    (project / "final.mp4").write_bytes(b"before")
    preserve_previous_render(project)
    (project / "final.mp4").write_bytes(b"after")
    (project / "creative_execution_plan.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("pipeline.creative_execution.shutil.which", lambda _name: None)
    finalize_comparison(project)
    assert (project / "comparison" / "before.mp4").read_bytes() == b"before"
    assert (project / "comparison" / "after.mp4").read_bytes() == b"after"
