"""PROMPT 4 — Claude Director StoryPlan contracts (no live API)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline.ai.schemas import (
    AssetAnalysis,
    CaptionMode,
    MediaType,
    Mood,
    SceneKind,
    ShotType,
    StoryPlan,
    StylePreset,
)
from pipeline.director import (
    create_story_plan,
    deterministic_story_plan,
    intent_leaks_into_captions,
    max_allowed_duration_sec,
    sort_analyses_chronologically,
    style_from_brief,
    suggest_target_duration_sec,
    text_similarity,
    validate_story_plan,
)


def _analysis(segment_id: str, **over: Any) -> AssetAnalysis:
    base: dict[str, Any] = {
        "asset_id": f"asset:{segment_id}",
        "segment_id": segment_id,
        "source_file": f"{segment_id.split('#')[0]}.mp4",
        "media_type": MediaType.still_video.value,
        "upload_index": int(segment_id.split("_")[1][:2]) if "still_" in segment_id else 0,
        "evidence_frame_ids": [f"{segment_id}#f01"],
        "subjects": ["person"],
        "scene": SceneKind.outdoor.value,
        "shot_type": ShotType.medium.value,
        "action_progression": "stands then smiles",
        "mood": Mood.calm.value,
        "technical_quality": 0.7,
        "aesthetic_value": 0.65,
        "emotional_value": 0.55,
        "narrative_value": 0.6,
        "hook_potential": 0.4,
        "motion_quality": 0.1,
        "novelty": 0.5,
        "redundancy_group": None,
        "focus_x": 0.5,
        "focus_y": 0.45,
        "focus_width": 0.5,
        "focus_height": 0.55,
        "crop_confidence": 0.6,
        "visually_grounded_facts": ["person outdoors"],
        "uncertainty": 0.2,
        "analysis_provider": "anthropic",
        "analysis_model": "claude-haiku-4-5-20251001",
    }
    base.update(over)
    return AssetAnalysis.model_validate(base)


def _few_photos() -> list[AssetAnalysis]:
    return [
        _analysis("still_01#s001", upload_index=0, hook_potential=0.8, narrative_value=0.7),
        _analysis("still_02#s001", upload_index=1, hook_potential=0.3, narrative_value=0.55),
        _analysis("still_03#s001", upload_index=2, hook_potential=0.5, narrative_value=0.6),
    ]


def test_user_intent_reaches_director_plan():
    intent = "감성적인 유튜브 브이로그로 만들어줘"
    plan = deterministic_story_plan(intent, _few_photos())
    assert plan.user_intent == intent


def test_raw_intent_not_used_as_caption():
    intent = "감성적인 유튜브 브이로그로 만들어줘"
    plan = deterministic_story_plan(intent, _few_photos())
    leaks = intent_leaks_into_captions(intent, plan.narrative_arc + [plan.concept])
    assert leaks == []
    for line in plan.narrative_arc:
        assert intent not in line
    assert intent not in plan.concept


def test_few_photos_choose_short_duration():
    plan = deterministic_story_plan("make a calm vlog", _few_photos())
    assert plan.target_duration_sec <= suggest_target_duration_sec(3) + 0.01
    assert plan.target_duration_sec <= 11.0
    assert suggest_target_duration_sec(2) < suggest_target_duration_sec(8)


def test_does_not_select_all_duplicate_photos():
    analyses = [
        _analysis(
            f"still_{i:02d}#s001",
            upload_index=i - 1,
            redundancy_group="same_selfie",
            hook_potential=0.5 + i * 0.01,
            narrative_value=0.5,
            visually_grounded_facts=["same person selfie"],
        )
        for i in range(1, 7)
    ]
    plan = deterministic_story_plan("short vlog please", analyses)
    selected_group = [
        a.segment_id
        for a in analyses
        if a.segment_id in plan.selected_segment_ids and a.redundancy_group == "same_selfie"
    ]
    assert len(selected_group) == 1
    assert plan.allow_asset_reuse is False
    assert len(plan.selected_segment_ids) == len(set(plan.selected_segment_ids))


def test_default_style_is_clean_vlog_without_y2k_request():
    assert style_from_brief("감성적인 유튜브 브이로그로 만들어줘") == StylePreset.clean_vlog
    plan = deterministic_story_plan("감성적인 유튜브 브이로그로 만들어줘", _few_photos())
    assert plan.style_preset == StylePreset.clean_vlog
    assert plan.caption_mode != CaptionMode.none
    assert any(
        line.lower().startswith("fact:") or line.lower().startswith("mood:")
        for line in plan.narrative_arc
    )


def test_y2k_only_when_brief_asks():
    assert style_from_brief("Y2K CCD camcorder look please") == StylePreset.y2k_camcorder
    plan = deterministic_story_plan("make it y2k retro ccd", _few_photos())
    assert plan.style_preset == StylePreset.y2k_camcorder


def test_validate_rejects_unknown_and_duplicate_and_leak():
    analyses = _few_photos()
    allowed = {a.segment_id for a in analyses}
    intent = "감성적인 유튜브 브이로그로 만들어줘"
    bad = StoryPlan(
        user_intent=intent,
        concept=intent,
        tone=Mood.calm,
        target_duration_sec=60.0,
        style_preset=StylePreset.clean_vlog,
        hook_segment_id="still_01#s001",
        ending_segment_id="still_01#s001",
        selected_segment_ids=["still_01#s001", "still_01#s001"],
        narrative_arc=[f"fact: {intent}"],
        caption_mode=CaptionMode.sparse,
        allow_asset_reuse=False,
        provider="anthropic",
        model="x",
    )
    errors = validate_story_plan(bad, allowed_segment_ids=allowed, analyses=analyses)
    assert any("duplicate" in e for e in errors)
    assert any("too long" in e for e in errors)
    assert any("leaked" in e for e in errors)

    unknown = deterministic_story_plan(intent, analyses).model_copy(
        update={"selected_segment_ids": ["ghost#s001", "still_01#s001"], "hook_segment_id": "ghost#s001", "ending_segment_id": "still_01#s001"}
    )
    # model_copy bypasses model_validator? In pydantic v2 model_copy doesn't re-run validators by default
    errors2 = validate_story_plan(unknown, allowed_segment_ids=allowed, analyses=analyses)
    assert any("unknown" in e for e in errors2)


def test_clean_vlog_rejects_caption_mode_none():
    analyses = _few_photos()
    plan = deterministic_story_plan("vlog", analyses).model_copy(
        update={"caption_mode": CaptionMode.none}
    )
    errors = validate_story_plan(
        plan, allowed_segment_ids={a.segment_id for a in analyses}, analyses=analyses
    )
    assert any("caption" in e for e in errors)


def test_create_story_plan_writes_files_on_api_failure(tmp_path: Path, monkeypatch):
    project = "dir_demo"
    project_dir = tmp_path / project
    project_dir.mkdir()
    intent = "감성적인 유튜브 브이로그로 만들어줘"
    (project_dir / "note.txt").write_text(intent, encoding="utf-8")
    analyses = _few_photos()
    (project_dir / "asset_analyses.json").write_text(
        json.dumps({"project": project, "analyses": [a.model_dump(mode="json") for a in analyses]}),
        encoding="utf-8",
    )

    class Boom:
        def messages(self):  # pragma: no cover
            raise AssertionError("should use .messages.create")

    class BoomClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("no network in tests")

    out = create_story_plan(
        tmp_path,
        project,
        client=BoomClient(),  # type: ignore[arg-type]
        force=True,
    )
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    plan = StoryPlan.model_validate(payload["plan"])
    assert plan.user_intent == intent
    assert plan.style_preset == StylePreset.clean_vlog
    assert intent not in plan.concept
    assert not intent_leaks_into_captions(intent, plan.narrative_arc)
    usage = json.loads((project_dir / "story_plan_usage.json").read_text(encoding="utf-8"))
    assert usage["project"] == project
    assert payload["source"].startswith("deterministic")


def test_chrono_sort_uses_upload_index_not_alpha():
    analyses = [
        _analysis("z_clip#s001", upload_index=0, source_file="z_clip.mp4", media_type=MediaType.video),
        _analysis("a_clip#s001", upload_index=1, source_file="a_clip.mp4", media_type=MediaType.video),
    ]
    ordered = sort_analyses_chronologically(analyses)
    assert [a.segment_id for a in ordered] == ["z_clip#s001", "a_clip#s001"]


def test_text_similarity_detects_near_copy():
    intent = "감성적인 유튜브 브이로그로 만들어줘"
    assert text_similarity(intent, intent) >= 0.99
    assert text_similarity(intent, "person outdoors") < 0.5
    assert max_allowed_duration_sec(2) >= suggest_target_duration_sec(2)
