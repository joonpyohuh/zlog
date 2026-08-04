"""Creative Direction Bible v0.1 — Soft Flow contracts + effect budget."""

from __future__ import annotations

from pipeline.ai.schemas import (
    AudioStrategy,
    CaptionStrategy,
    ClipRole,
    CreativeQualityScores,
    EffectStrategy,
    FitMode,
    MotionKind,
    PlanEvaluation,
    PlannedClip,
    StoryPlan,
    StylePreset,
    TransitionKind,
    VideoPurpose,
    canonical_clip_role,
)
from pipeline.creative_direction import (
    BIBLE_PATH,
    director_bible_block,
    effect_budget,
    soft_flow_roles,
)
from pipeline.plan_timeline import role_for_index


def test_bible_file_exists() -> None:
    assert BIBLE_PATH.is_file()
    text = BIBLE_PATH.read_text(encoding="utf-8")
    assert "Soft Flow" in text
    assert "Zlog Moment" in text


def test_soft_flow_roles_order() -> None:
    assert soft_flow_roles() == [
        "hook",
        "orientation",
        "development",
        "zlog_moment",
        "release",
        "resonance",
    ]


def test_effect_budget() -> None:
    assert effect_budget(15) == 2
    assert effect_budget(30) == 4
    assert effect_budget(45) >= 4


def test_legacy_role_aliases() -> None:
    assert canonical_clip_role(ClipRole.opening) == ClipRole.hook
    assert canonical_clip_role(ClipRole.peak) == ClipRole.zlog_moment
    assert canonical_clip_role(ClipRole.closing) == ClipRole.resonance
    assert canonical_clip_role(ClipRole.hook) == ClipRole.hook


def test_planned_clip_creative_fields_default() -> None:
    clip = PlannedClip.model_validate(
        {
            "segment_id": "S01",
            "role": "zlog_moment",
            "target_duration_sec": 2.0,
            "fit_mode": FitMode.cover,
            "motion": MotionKind.none,
            "motion_strength": 0.0,
            "transition": TransitionKind.cut,
        }
    )
    assert clip.role == ClipRole.zlog_moment
    assert clip.caption_strategy == CaptionStrategy.none
    assert clip.audio_strategy.preserve_source_audio is True
    assert clip.effect_strategy.effect == "none"


def test_planned_clip_bible_example_shape() -> None:
    clip = PlannedClip.model_validate(
        {
            "segment_id": "S07",
            "role": "zlog_moment",
            "target_duration_sec": 2.5,
            "selection_reasons": ["DIRECT_GAZE", "EMOTIONAL_REACTION"],
            "cut_reason": "gaze then look-away completes the beat",
            "caption_strategy": "none",
            "caption_reason": "face and natural audio carry the moment",
            "audio_strategy": {
                "preserve_source_audio": True,
                "bgm_duck": True,
                "reason": "short laugh is the emotional core",
            },
            "effect_strategy": {
                "effect": "none",
                "reason": "effect would overpower micro-expression",
            },
        }
    )
    assert clip.selection_reasons == ["DIRECT_GAZE", "EMOTIONAL_REACTION"]
    assert isinstance(clip.audio_strategy, AudioStrategy)
    assert isinstance(clip.effect_strategy, EffectStrategy)


def test_story_plan_accepts_video_purpose() -> None:
    plan = StoryPlan.model_validate(
        {
            "user_intent": "weekend cafe",
            "concept": "quiet afternoon",
            "target_duration_sec": 15.0,
            "style_preset": StylePreset.clean_vlog,
            "video_purpose": VideoPurpose.daily_vlog,
            "hook_segment_id": "a",
            "ending_segment_id": "c",
            "selected_segment_ids": ["a", "b", "c"],
            "provider": "anthropic",
            "model": "test",
        }
    )
    assert plan.video_purpose == VideoPurpose.daily_vlog


def test_plan_evaluation_has_creative_scores() -> None:
    ev = PlanEvaluation.model_validate(
        {
            "overall_score": 0.7,
            "evaluator_provider": "openai",
            "evaluator_model": "test",
        }
    )
    assert isinstance(ev.creative, CreativeQualityScores)
    assert 0.0 <= ev.creative.template_visibility <= 1.0


def test_role_for_index_soft_flow() -> None:
    story = StoryPlan.model_validate(
        {
            "user_intent": "x",
            "concept": "y",
            "target_duration_sec": 15.0,
            "hook_segment_id": "h",
            "ending_segment_id": "e",
            "selected_segment_ids": ["h", "o", "d", "z", "r", "e"],
            "provider": "anthropic",
            "model": "test",
        }
    )
    ids = ["h", "o", "d", "z", "r", "e"]
    roles = [role_for_index(i, len(ids), sid, story, peak_id="z") for i, sid in enumerate(ids)]
    assert roles[0] == ClipRole.hook
    assert roles[1] == ClipRole.orientation
    assert roles[3] == ClipRole.zlog_moment
    assert roles[4] == ClipRole.release
    assert roles[-1] == ClipRole.resonance


def test_director_bible_block_mentions_soft_flow() -> None:
    block = director_bible_block()
    assert "Soft Flow" in block
    assert "zlog_moment" in block
