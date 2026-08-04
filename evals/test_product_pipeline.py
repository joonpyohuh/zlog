"""PROMPT 9 — product pipeline wiring, quality modes, scenarios, taste lock."""

from __future__ import annotations

from pathlib import Path

from evals.compare_modes import estimated_mode_costs, load_catalog, score_artifacts
from pipeline.ai.config import ModelConfig
from pipeline.ai.schemas import (
    AssetAnalysis,
    FitMode,
    MediaType,
    Mood,
    MotionKind,
    PlannedClip,
    SceneKind,
    ShotType,
    StoryPlan,
    StylePreset,
    TimelinePlan,
    TransitionKind,
)
from pipeline.product_pipeline import PRODUCT_STAGES, escalation_threshold_for_mode
from pipeline.youtube_trends import main as trends_main

REPO = Path(__file__).resolve().parents[1]


def test_product_stages_match_target_order():
    assert PRODUCT_STAGES == [
        "split",
        "evidence",
        "filter",
        "sheet",
        "analyze",
        "director",
        "plan",
        "evaluate",
        "render",
        "grade",
        "audio",
    ]


def test_escalation_thresholds_by_mode():
    assert escalation_threshold_for_mode("economy") is None
    assert escalation_threshold_for_mode("balanced") == 0.55
    assert escalation_threshold_for_mode("premium") == 0.35


def test_server_wires_product_pipeline():
    src = (REPO / "server.py").read_text(encoding="utf-8")
    assert "run_product_pipeline" in src
    assert "quality_mode" in src
    assert "youtube_taste_overwrite\": False" in src or "youtube_taste_overwrite" in src
    assert "select_ai.select_ai" not in src


def test_run_py_uses_hybrid_stages():
    src = (REPO / "run.py").read_text(encoding="utf-8")
    assert "run_product_pipeline" in src
    assert "quality-mode" in src or "quality_mode" in src


def test_youtube_taste_overwrite_disabled_by_default(monkeypatch, capsys):
    monkeypatch.delenv("ZLOG_ALLOW_YOUTUBE_TASTE_OVERWRITE", raising=False)
    taste = REPO / "taste" / "taste_profile.json"
    before = taste.read_text(encoding="utf-8") if taste.exists() else None
    trends_main()
    out = capsys.readouterr().out
    assert "disabled" in out.lower()
    if before is not None:
        assert taste.read_text(encoding="utf-8") == before


def test_ten_eval_scenarios_catalogued():
    cat = load_catalog()
    scenarios = cat["scenarios"]
    assert len(scenarios) >= 10
    ids = {s["id"] for s in scenarios}
    assert "vague_intent" in ids
    assert "explicit_y2k" in ids
    assert "repeated_pet" in ids
    assert set(cat["modes"]) >= {"baseline", "economy", "balanced", "premium"}


def test_mode_cost_estimates_ordered():
    costs = estimated_mode_costs()
    assert costs["baseline"] <= costs["economy"] <= costs["balanced"] <= costs["premium"]


def _analysis(sid: str, **over):
    base = {
        "asset_id": f"a:{sid}",
        "segment_id": sid,
        "source_file": f"{sid}.mp4",
        "media_type": MediaType.still_video,
        "upload_index": 0,
        "subjects": ["person"],
        "scene": SceneKind.outdoor,
        "shot_type": ShotType.medium,
        "mood": Mood.calm,
        "hook_potential": 0.7,
        "narrative_value": 0.6,
        "crop_confidence": 0.8,
        "visually_grounded_facts": ["person"],
        "analysis_provider": "anthropic",
        "analysis_model": "haiku",
    }
    base.update(over)
    return AssetAnalysis.model_validate(base)


def test_compare_modes_scores_prompt_leak_and_unique_ratio():
    intent = "감성적인 유튜브 브이로그로 만들어줘"
    ids = ["a#s001", "b#s001", "c#s001"]
    story = StoryPlan.model_validate(
        {
            "user_intent": intent,
            "concept": "walk",
            "hook_segment_id": ids[0],
            "ending_segment_id": ids[2],
            "selected_segment_ids": ids,
            "target_duration_sec": 12,
            "narrative_arc": [f"caption: {intent}"],
            "provider": "anthropic",
            "model": "sonnet",
            "style_preset": StylePreset.clean_vlog,
        }
    )
    clips = [
        PlannedClip.model_validate(
            {
                "segment_id": sid,
                "target_duration_sec": 2.0,
                "fit_mode": FitMode.contain,
                "motion": MotionKind.static,
                "transition": TransitionKind.cut,
                "caption": intent if i == 0 else "",
                "caption_grounding": "",
            }
        )
        for i, sid in enumerate(ids)
    ]
    # repeat first asset
    clips.append(
        PlannedClip.model_validate(
            {
                "segment_id": ids[0],
                "target_duration_sec": 2.0,
                "fit_mode": FitMode.contain,
                "motion": MotionKind.static,
                "transition": TransitionKind.cut,
            }
        )
    )
    timeline = TimelinePlan(
        project="t",
        clips=clips,
        total_target_duration_sec=12,
        style_preset=StylePreset.clean_vlog,
    )
    analyses = [_analysis(s) for s in ids]
    metrics = score_artifacts(
        intent=intent,
        story=story,
        timeline=timeline,
        analyses=analyses,
        mode="baseline",
        model_cost_usd=0.0,
    )
    assert metrics["prompt_leakage"] > 0
    assert metrics["unique_asset_ratio"] < 1.0
    assert metrics["repeated_scenes"] >= 1


def test_studio_composer_exists():
    path = REPO / "next-web" / "src" / "components" / "studio" / "StudioComposer.tsx"
    assert path.exists()
    src = path.read_text(encoding="utf-8")
    assert "quality_mode" in src
    assert "telemetry" in src
    assert "ZLOG_API" not in src or "apiBase" in src
    # no secret leakage helpers
    assert "ANTHROPIC" not in src
    assert "api_key" not in src.lower()


def test_model_config_accepts_premium():
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
        quality_mode="premium",
    )
    assert cfg.quality_mode == "premium"
