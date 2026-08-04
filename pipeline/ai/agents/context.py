"""Minimal context builders — no full-project dumps (PROMPT 1.5)."""

from __future__ import annotations

from typing import Any

from pipeline.ai.schemas import AssetAnalysis, PlanEvaluation, StoryPlan, TimelinePlan


def _thin_analysis(a: AssetAnalysis) -> dict[str, Any]:
    return {
        "segment_id": a.segment_id,
        "subjects": a.subjects[:6],
        "scene": a.scene.value if a.scene else None,
        "shot_type": a.shot_type.value if a.shot_type else None,
        "mood": a.mood.value,
        "hook_potential": a.hook_potential,
        "narrative_value": a.narrative_value,
        "novelty": a.novelty,
        "redundancy_group": a.redundancy_group,
        "crop_confidence": a.crop_confidence,
        "focus_x": a.focus_x,
        "focus_y": a.focus_y,
        "visually_grounded_facts": (a.visually_grounded_facts or [])[:4],
        "upload_index": a.upload_index,
        "capture_time": a.capture_time,
        "uncertainty": a.uncertainty,
        "evidence_frame_ids": (a.evidence_frame_ids or [])[:6],
    }


def build_analysis_context(
    *,
    segment_id: str,
    evidence_frame_ids: list[str],
    deterministic_features: dict[str, Any] | None,
    user_intent: str,
    neighbor_summaries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Asset Analysis Agent — one segment bundle + tiny neighbors."""
    intent = (user_intent or "").strip()
    return {
        "segment_id": segment_id,
        "evidence_frame_ids": list(evidence_frame_ids)[:8],
        "deterministic_features": {
            k: deterministic_features.get(k)
            for k in (
                "audio_rms",
                "onset_density",
                "is_static",
                "is_high_motion",
                "duration_sec",
            )
            if deterministic_features and k in deterministic_features
        },
        "user_intent_brief": intent[:240],
        "neighbor_summaries": (neighbor_summaries or [])[:4],
    }


def build_director_context(
    *,
    user_intent: str,
    analyses: list[AssetAnalysis],
    audio_summary: dict[str, Any] | None = None,
    strong_sheet_path: str | None = None,
    ambiguous_sheet_path: str | None = None,
) -> dict[str, Any]:
    """Director — summaries + chronology, not raw frames of every asset."""
    ordered = sorted(
        analyses,
        key=lambda a: (a.capture_time is None, a.capture_time or "", a.upload_index),
    )
    return {
        "user_intent": (user_intent or "").strip()[:2000],
        "analyses_summary": [_thin_analysis(a) for a in ordered],
        "chronology_segment_ids": [a.segment_id for a in ordered],
        "audio_summary": audio_summary or {"available": False},
        "contact_sheets": {
            "strong": strong_sheet_path,
            "ambiguous": ambiguous_sheet_path,
        },
    }


def build_evaluator_context(
    *,
    user_intent: str,
    story: StoryPlan,
    timeline: TimelinePlan | None,
    analyses: list[AssetAnalysis],
    contact_sheet_paths: list[str] | None = None,
    top_alternates: list[AssetAnalysis] | None = None,
) -> dict[str, Any]:
    selected = set(story.selected_segment_ids)
    chosen = [_thin_analysis(a) for a in analyses if a.segment_id in selected]
    alts = top_alternates or [
        a for a in analyses if a.segment_id not in selected
    ]
    alts_sorted = sorted(alts, key=lambda a: a.hook_potential, reverse=True)[:6]
    return {
        "user_intent": (user_intent or "").strip()[:2000],
        "story_plan": {
            "concept": story.concept,
            "style_preset": story.style_preset.value,
            "target_duration_sec": story.target_duration_sec,
            "hook_segment_id": story.hook_segment_id,
            "ending_segment_id": story.ending_segment_id,
            "selected_segment_ids": story.selected_segment_ids,
            "caption_mode": story.caption_mode.value,
            "narrative_arc": story.narrative_arc[:12],
            "allow_asset_reuse": story.allow_asset_reuse,
            "confidence": story.confidence,
        },
        "timeline_summary": {
            "clip_segment_ids": [c.segment_id for c in timeline.clips] if timeline else [],
            "total_target_duration_sec": timeline.total_target_duration_sec if timeline else None,
            "captions": [
                {"segment_id": c.segment_id, "text": c.caption, "grounding": c.caption_grounding}
                for c in (timeline.clips if timeline else [])
                if (c.caption or "").strip()
            ][:8],
        },
        "selected_analyses": chosen,
        "alternate_candidates": [_thin_analysis(a) for a in alts_sorted],
        "contact_sheet_paths": list(contact_sheet_paths or [])[:4],
    }


def build_repair_context(
    *,
    user_intent: str,
    original_plan: StoryPlan,
    current_plan: StoryPlan,
    evaluation: PlanEvaluation,
    failure_segment_ids: list[str],
    alternate_candidates: list[AssetAnalysis],
    mutable_fields: list[str] | None = None,
) -> dict[str, Any]:
    """Repair Agent — failures + allowed fields only."""
    codes = [f.code for f in evaluation.failures]
    return {
        "user_intent": (user_intent or "").strip()[:2000],
        "original_selected": original_plan.selected_segment_ids,
        "current_plan": {
            "selected_segment_ids": current_plan.selected_segment_ids,
            "hook_segment_id": current_plan.hook_segment_id,
            "ending_segment_id": current_plan.ending_segment_id,
            "target_duration_sec": current_plan.target_duration_sec,
            "style_preset": current_plan.style_preset.value,
            "caption_mode": current_plan.caption_mode.value,
            "narrative_arc": current_plan.narrative_arc[:12],
        },
        "failure_reason_codes": codes,
        "problem_segment_ids": list(dict.fromkeys(failure_segment_ids))[:20],
        "alternate_candidates": [_thin_analysis(a) for a in alternate_candidates[:8]],
        "mutable_fields": mutable_fields
        or [
            "selected_segment_ids",
            "hook_segment_id",
            "ending_segment_id",
            "target_duration_sec",
            "style_preset",
            "caption_mode",
            "narrative_arc",
            "allow_asset_reuse",
        ],
        "forbidden": [
            "invented_timestamps",
            "new_segment_ids_outside_candidates",
            "full_rewrite_unrelated_to_failures",
        ],
    }


def context_keys(ctx: dict[str, Any]) -> set[str]:
    """Helper for tests — which top-level keys were included."""
    return set(ctx.keys())
