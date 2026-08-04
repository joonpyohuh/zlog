"""Offline mode-comparison metrics for hybrid pipeline artifacts (PROMPT 9)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline.ai.schemas import AssetAnalysis, StoryPlan, TimelinePlan
from pipeline.director import intent_leaks_into_captions, text_similarity
from pipeline.evaluate_plan import code_evaluate_plan

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "evals" / "scenarios" / "catalog.json"


def load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def unique_asset_ratio(timeline: TimelinePlan) -> float:
    ids = [c.segment_id for c in timeline.clips]
    if not ids:
        return 0.0
    return len(set(ids)) / len(ids)


def repeated_scene_count(timeline: TimelinePlan) -> int:
    from collections import Counter

    counts = Counter(c.segment_id for c in timeline.clips)
    return sum(1 for n in counts.values() if n > 1)


def caption_grounding_rate(timeline: TimelinePlan) -> float:
    caps = [c for c in timeline.clips if (c.caption or "").strip()]
    if not caps:
        return 1.0
    ok = sum(1 for c in caps if (c.caption_grounding or "").strip())
    return ok / len(caps)


def prompt_leak_score(intent: str, story: StoryPlan, timeline: TimelinePlan) -> float:
    texts = list(story.narrative_arc)
    texts.extend(c.caption for c in timeline.clips if c.caption)
    leaks = intent_leaks_into_captions(intent, texts)
    if not texts:
        return 0.0
    return len(leaks) / max(1, len(texts))


def crop_failure_count(timeline: TimelinePlan, analyses: list[AssetAnalysis]) -> int:
    fails = code_evaluate_plan(
        user_intent="",
        story=StoryPlan.model_validate(
            {
                "user_intent": "x",
                "concept": "c",
                "hook_segment_id": timeline.clips[0].segment_id,
                "ending_segment_id": timeline.clips[-1].segment_id,
                "selected_segment_ids": list({c.segment_id for c in timeline.clips}),
                "target_duration_sec": timeline.total_target_duration_sec,
                "provider": "anthropic",
                "model": "test",
            }
        ),
        timeline=timeline,
        analyses=analyses,
    )
    return sum(1 for f in fails if f.code == "risky_crop")


def score_artifacts(
    *,
    intent: str,
    story: StoryPlan,
    timeline: TimelinePlan,
    analyses: list[AssetAnalysis],
    mode: str,
    processing_time_s: float = 0.0,
    model_cost_usd: float = 0.0,
    escalated: bool = False,
    render_ok: bool = True,
) -> dict[str, Any]:
    leaks = prompt_leak_score(intent, story, timeline)
    return {
        "mode": mode,
        "prompt_leakage": round(leaks, 4),
        "unique_asset_ratio": round(unique_asset_ratio(timeline), 4),
        "repeated_scenes": repeated_scene_count(timeline),
        "chronology_ok": not any(
            f.code == "chronology_violation"
            for f in code_evaluate_plan(
                user_intent=intent,
                story=story,
                timeline=timeline,
                analyses=analyses,
            )
        ),
        "hook_quality": next(
            (a.hook_potential for a in analyses if a.segment_id == story.hook_segment_id),
            0.0,
        ),
        "caption_grounding": round(caption_grounding_rate(timeline), 4),
        "crop_failures": crop_failure_count(timeline, analyses),
        "render_success": bool(render_ok),
        "processing_time_s": processing_time_s,
        "model_cost_usd": model_cost_usd,
        "escalation_rate": 1.0 if escalated else 0.0,
        "intent_similarity_max": round(
            max(
                (
                    text_similarity(intent, c.caption)
                    for c in timeline.clips
                    if c.caption
                ),
                default=0.0,
            ),
            4,
        ),
    }


def estimated_mode_costs() -> dict[str, float]:
    return dict(load_catalog().get("cost_estimates_usd_per_short") or {})
