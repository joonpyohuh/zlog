"""Cross-provider plan evaluation — code checks + GPT Luna + conditional Sol (PROMPT 8).

Reads:  work/<project>/story_plan.json
        work/<project>/timeline_plan.json
        work/<project>/asset_analyses.json
        work/<project>/edl_ai.json (optional, style conflict)
        work/<project>/director_sheets/*.jpg or contact_sheets/*.jpg
        work/<project>/note.txt
Writes: work/<project>/plan_evaluation.json
        work/<project>/story_plan_revision.json (Claude revise path)
        work/<project>/sol_repair.json (only when Sol actually runs)
        work/<project>/plan_evaluation_usage.json

Luna evaluates; it does not direct. Timestamps / new scenes / unknown IDs forbidden.
At most: code+Luna → optional Claude revise once → re-eval → baseline OR one Sol call.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import click

from pipeline.ai.config import ModelConfig, is_premium_mode, load_model_config
from pipeline.ai.schemas import (
    AssetAnalysis,
    EvaluationFailure,
    FitMode,
    PlanEvaluation,
    StoryPlan,
    StylePreset,
    TimelinePlan,
    story_plan_against_candidates,
)
from pipeline.ai.usage import CallUsage
from pipeline.director import (
    deterministic_story_plan,
    intent_leaks_into_captions,
    load_user_intent,
    max_allowed_duration_sec,
    text_similarity,
    validate_story_plan,
)
from pipeline.plan_timeline import plan_timeline

IMPORTANT_CODES = frozenset(
    {
        "prompt_leakage",
        "segment_repeat",
        "redundancy_repeat",
        "adjacent_similar",
        "ungrounded_caption",
        "chronology_violation",
        "risky_crop",
        "caption_density",
        "weak_hook",
        "excessive_duration",
        "style_conflict",
    }
)

RISKY_FIT = {
    FitMode.cover,
    FitMode.subject_aware_cover,
    FitMode.smart_crop,
}
CROP_CONF_FLOOR = 0.35
HOOK_FLOOR = 0.35
MAX_SPARSE_CAPTIONS = 3
INTENT_SIM_FLOOR = 0.72

LunaFn = Callable[..., tuple[PlanEvaluation, CallUsage]]
ReviseFn = Callable[..., tuple[StoryPlan, CallUsage]]
SolFn = Callable[..., tuple[StoryPlan, CallUsage]]


def summarize_analyses(analyses: list[AssetAnalysis]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for a in analyses:
        rows.append(
            {
                "segment_id": a.segment_id,
                "subjects": a.subjects,
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
                "visually_grounded_facts": a.visually_grounded_facts[:4],
                "upload_index": a.upload_index,
                "capture_time": a.capture_time,
                "uncertainty": a.uncertainty,
            }
        )
    return rows


def _fail(code: str, message: str, segment_id: str | None = None) -> EvaluationFailure:
    return EvaluationFailure(code=code, message=message, segment_id=segment_id)


def _load_story(project_dir: Path) -> StoryPlan:
    raw = json.loads((project_dir / "story_plan.json").read_text(encoding="utf-8"))
    data = raw.get("plan") if isinstance(raw, dict) and "plan" in raw else raw
    return StoryPlan.model_validate(data)


def _load_timeline(project_dir: Path) -> TimelinePlan | None:
    path = project_dir / "timeline_plan.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    data = raw.get("plan") if isinstance(raw, dict) and "plan" in raw else raw
    return TimelinePlan.model_validate(data)


def _load_analyses(project_dir: Path) -> list[AssetAnalysis]:
    path = project_dir / "asset_analyses.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [AssetAnalysis.model_validate(x) for x in (data.get("analyses") or [])]


def _load_edl(project_dir: Path) -> dict[str, Any] | None:
    path = project_dir / "edl_ai.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _contact_sheets(project_dir: Path) -> list[Path]:
    sheets: list[Path] = []
    for folder in ("director_sheets", "contact_sheets"):
        d = project_dir / folder
        if d.is_dir():
            sheets.extend(sorted(d.glob("*.jpg")))
    return sheets[:4]


def _chrono_key(a: AssetAnalysis) -> tuple:
    return (a.capture_time is None, a.capture_time or "", a.upload_index, a.segment_id)


def _caption_texts(story: StoryPlan, timeline: TimelinePlan | None) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    for line in story.narrative_arc:
        low = line.lower().strip()
        if low.startswith(("fact:", "mood:", "caption:")) or "caption" in low:
            out.append((line, None))
    if timeline:
        for c in timeline.clips:
            if (c.caption or "").strip():
                out.append((c.caption, c.segment_id))
    return out


def code_evaluate_plan(
    *,
    user_intent: str,
    story: StoryPlan,
    timeline: TimelinePlan | None,
    analyses: list[AssetAnalysis],
    edl: dict[str, Any] | None = None,
) -> list[EvaluationFailure]:
    """Deterministic checks — no API. Failures cite segment_id when possible."""
    failures: list[EvaluationFailure] = []
    by_id = {a.segment_id: a for a in analyses}
    clips = list(timeline.clips) if timeline else []

    # 1) prompt leakage / high intent↔caption similarity
    for text, sid in _caption_texts(story, timeline):
        leaks = intent_leaks_into_captions(user_intent, [text])
        if leaks or text_similarity(user_intent, text) >= INTENT_SIM_FLOOR:
            failures.append(
                _fail(
                    "prompt_leakage",
                    f"caption too similar to user edit brief: {text[:80]!r}",
                    sid,
                )
            )

    # 2) same segment repeated
    if clips and not story.allow_asset_reuse:
        counts = Counter(c.segment_id for c in clips)
        for sid, n in counts.items():
            if n > 1:
                failures.append(
                    _fail("segment_repeat", f"segment appears {n} times", sid)
                )

    # 3) same redundancy group repeated
    if clips:
        seen_groups: dict[str, str] = {}
        for c in clips:
            a = by_id.get(c.segment_id)
            g = a.redundancy_group if a else None
            if not g:
                continue
            if g in seen_groups and seen_groups[g] != c.segment_id:
                failures.append(
                    _fail(
                        "redundancy_repeat",
                        f"redundancy_group={g!r} reused after {seen_groups[g]}",
                        c.segment_id,
                    )
                )
            else:
                seen_groups.setdefault(g, c.segment_id)

    # 4) adjacent similar scenes
    for i in range(1, len(clips)):
        prev, cur = by_id.get(clips[i - 1].segment_id), by_id.get(clips[i].segment_id)
        if not prev or not cur:
            continue
        same_subjects = bool(set(prev.subjects) & set(cur.subjects))
        same_scene = prev.scene == cur.scene and prev.shot_type == cur.shot_type
        if same_subjects and same_scene and cur.novelty < 0.45:
            failures.append(
                _fail(
                    "adjacent_similar",
                    "adjacent clips share subject/scene/shot with low novelty",
                    cur.segment_id,
                )
            )

    # 5) captions without visual grounding
    if clips:
        for c in clips:
            text = (c.caption or "").strip()
            if text and not (c.caption_grounding or "").strip():
                failures.append(
                    _fail(
                        "ungrounded_caption",
                        f"caption lacks grounding metadata: {text[:60]!r}",
                        c.segment_id,
                    )
                )

    # 6) unjustified chronology violation (body after cold-open hook)
    if len(clips) >= 3:
        body = clips[1:]
        keys = []
        for c in body:
            a = by_id.get(c.segment_id)
            if a:
                keys.append((_chrono_key(a), c.segment_id))
        for i in range(1, len(keys)):
            if keys[i][0] < keys[i - 1][0]:
                failures.append(
                    _fail(
                        "chronology_violation",
                        f"body order regresses after {keys[i - 1][1]}",
                        keys[i][1],
                    )
                )
                break

    # 7) risky crop
    if clips:
        for c in clips:
            a = by_id.get(c.segment_id)
            conf = a.crop_confidence if a is not None else 1.0
            if c.fit_mode in RISKY_FIT and conf < CROP_CONF_FLOOR:
                failures.append(
                    _fail(
                        "risky_crop",
                        f"fit_mode={c.fit_mode.value} with crop_confidence={conf:.2f}",
                        c.segment_id,
                    )
                )

    # 8) caption density
    n_caps = sum(1 for c in clips if (c.caption or "").strip())
    if story.caption_mode.value == "dense" or n_caps > MAX_SPARSE_CAPTIONS:
        sid = next((c.segment_id for c in clips if (c.caption or "").strip()), None)
        failures.append(
            _fail(
                "caption_density",
                f"too many captions ({n_caps}) for sparse vlog",
                sid,
            )
        )

    # 9) weak hook
    hook = by_id.get(story.hook_segment_id)
    if hook is not None and hook.hook_potential < HOOK_FLOOR:
        failures.append(
            _fail(
                "weak_hook",
                f"hook_potential={hook.hook_potential:.2f} below {HOOK_FLOOR}",
                story.hook_segment_id,
            )
        )

    # 10) duration vs unique material
    n_unique = len(set(c.segment_id for c in clips)) if clips else len(set(story.selected_segment_ids))
    cap = max_allowed_duration_sec(max(1, n_unique))
    dur = (
        timeline.total_target_duration_sec
        if timeline is not None
        else story.target_duration_sec
    )
    if dur > cap:
        failures.append(
            _fail(
                "excessive_duration",
                f"duration {dur}s exceeds cap {cap}s for {n_unique} unique clips",
                story.hook_segment_id,
            )
        )

    # 11) style vs render effects conflict
    style = story.style_preset
    cleanish = style in {
        StylePreset.clean_vlog,
        StylePreset.vertical_full,
        StylePreset.soft_vlog,
    }
    if cleanish and edl:
        aes = edl.get("aesthetic") or {}
        sig = edl.get("signature") or {}
        conflicts: list[str] = []
        if aes.get("camcorder_osd"):
            conflicts.append("camcorder_osd")
        if aes.get("scanlines"):
            conflicts.append("scanlines")
        if (aes.get("lut") or "").strip():
            conflicts.append("lut")
        if aes.get("allow_flash"):
            conflicts.append("allow_flash")
        if sig.get("enabled"):
            conflicts.append("ending_credit")
        flashes = [
            c.get("segment_id")
            for c in (edl.get("timeline") or [])
            if c.get("transition") == "flash"
        ]
        if flashes:
            conflicts.append("flash_transition")
        if conflicts:
            failures.append(
                _fail(
                    "style_conflict",
                    f"{style.value} conflicts with render effects: {conflicts}",
                    flashes[0] if flashes else story.hook_segment_id,
                )
            )

    return failures


def scores_from_code_failures(failures: list[EvaluationFailure]) -> dict[str, float]:
    """Map failure codes → PlanEvaluation score dims (1.0 good, 0.0 bad)."""
    codes = {f.code for f in failures}

    def dim(bad: str, weight: float = 0.0) -> float:
        return weight if bad in codes else 1.0

    # redundancy_score / prompt_leakage: high means "lots of that problem" in schema naming
    # Keep consistent with prior tests: prompt_leakage=0.9 means bad leakage present.
    return {
        "overall_score": max(0.0, 1.0 - 0.12 * len(failures)),
        "narrative_coherence": 0.35 if "chronology_violation" in codes else 0.85,
        "hook_strength": 0.25 if "weak_hook" in codes else 0.8,
        "redundancy_score": (
            0.85
            if {"segment_repeat", "redundancy_repeat", "adjacent_similar"} & codes
            else 0.15
        ),
        "chronology_score": 0.3 if "chronology_violation" in codes else 0.9,
        "caption_grounding": 0.2 if "ungrounded_caption" in codes else 0.85,
        "prompt_leakage": 0.9 if "prompt_leakage" in codes else 0.05,
        "crop_safety": 0.25 if "risky_crop" in codes else 0.9,
        "visual_variety": 0.35 if "adjacent_similar" in codes else 0.8,
        "duration_suitability": 0.25 if "excessive_duration" in codes else 0.85,
    }


def merge_evaluations(
    code_failures: list[EvaluationFailure],
    luna: PlanEvaluation,
) -> PlanEvaluation:
    """Union failures; revision required if either side flags important issues."""
    by_key: dict[tuple[str, str | None], EvaluationFailure] = {}
    for f in code_failures:
        by_key[(f.code, f.segment_id)] = f
    for f in luna.failures:
        by_key.setdefault((f.code, f.segment_id), f)
    merged = list(by_key.values())
    important = [f for f in merged if f.code in IMPORTANT_CODES]
    requires = bool(important) or luna.requires_revision
    scores = scores_from_code_failures(code_failures)
    return PlanEvaluation(
        overall_score=min(luna.overall_score, scores["overall_score"]),
        narrative_coherence=min(luna.narrative_coherence, scores["narrative_coherence"]),
        hook_strength=min(luna.hook_strength, scores["hook_strength"]),
        redundancy_score=max(luna.redundancy_score, scores["redundancy_score"]),
        chronology_score=min(luna.chronology_score, scores["chronology_score"]),
        caption_grounding=min(luna.caption_grounding, scores["caption_grounding"]),
        prompt_leakage=max(luna.prompt_leakage, scores["prompt_leakage"]),
        crop_safety=min(luna.crop_safety, scores["crop_safety"]),
        visual_variety=min(luna.visual_variety, scores["visual_variety"]),
        duration_suitability=min(luna.duration_suitability, scores["duration_suitability"]),
        failures=merged,
        recommended_changes=list(luna.recommended_changes),
        requires_revision=requires,
        evaluator_provider=luna.evaluator_provider,
        evaluator_model=luna.evaluator_model,
    )


def important_failures(failures: list[EvaluationFailure]) -> list[EvaluationFailure]:
    return [f for f in failures if f.code in IMPORTANT_CODES]


def judgments_conflict(
    *,
    code_failures: list[EvaluationFailure],
    luna: PlanEvaluation,
    story: StoryPlan,
) -> bool:
    """Claude-ish confidence vs independent GPT/code judgment diverge hard."""
    code_bad = bool(important_failures(code_failures))
    luna_bad = luna.requires_revision or bool(important_failures(luna.failures))
    if luna_bad and story.confidence >= 0.85 and luna.overall_score <= 0.4:
        return True
    if code_bad and not luna_bad and luna.overall_score >= 0.85:
        return True
    if luna_bad and not code_bad and luna.overall_score <= 0.35:
        return True
    return False


def should_call_sol(
    *,
    quality_mode: str,
    important_failures_remain: bool,
    claude_revision_failed: bool,
    judgments_conflict_flag: bool,
) -> bool:
    """Sol only once, and only under premium + remaining user-visible issues."""
    if not is_premium_mode(quality_mode):
        return False
    if not important_failures_remain:
        return False
    if not claude_revision_failed:
        return False
    # Conflict is an extra gate when Claude already failed once.
    return True if judgments_conflict_flag or claude_revision_failed else False


def _write_story(project_dir: Path, project: str, plan: StoryPlan, *, source: str) -> None:
    payload = {
        "project": project,
        "generator": "evaluate_plan",
        "source": source,
        "plan": plan.model_dump(mode="json"),
    }
    (project_dir / "story_plan.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _default_luna(
    *,
    user_intent: str,
    story: StoryPlan,
    timeline: TimelinePlan | None,
    analyses: list[AssetAnalysis],
    sheets: list[Path],
    config: ModelConfig,
) -> tuple[PlanEvaluation, CallUsage]:
    from pipeline.ai.openai_provider import OpenAIProvider

    provider = OpenAIProvider(default_model=config.evaluator_model)
    # Provider dumps analyses as JSON; stage also persists summarize_analyses().
    return provider.evaluate_plan(
        user_intent=user_intent,
        plan=story,
        analyses=analyses,
        allowed_segment_ids={a.segment_id for a in analyses} | set(story.selected_segment_ids),
        model=config.evaluator_model,
        timeline=timeline,
        image_paths=sheets,
    )


def _default_claude_revise(
    *,
    user_intent: str,
    story: StoryPlan,
    evaluation: PlanEvaluation,
    analyses: list[AssetAnalysis],
    config: ModelConfig,
) -> tuple[StoryPlan, CallUsage]:
    from pipeline.ai.anthropic_provider import AnthropicProvider

    provider = AnthropicProvider(default_model=config.director_model)
    repaired, usage = provider.repair_story_plan(
        user_intent=user_intent,
        plan=story,
        evaluation=evaluation,
        analyses=analyses,
        allowed_segment_ids={a.segment_id for a in analyses},
        model=config.director_model,
    )
    # Provenance: keep original brief
    repaired = repaired.model_copy(
        update={
            "user_intent": user_intent,
            "provider": "anthropic",
            "model": config.director_model,
        }
    )
    return repaired, usage


def _default_sol_repair(
    *,
    user_intent: str,
    story: StoryPlan,
    evaluation: PlanEvaluation,
    analyses: list[AssetAnalysis],
    config: ModelConfig,
) -> tuple[StoryPlan, CallUsage]:
    from pipeline.ai.openai_provider import OpenAIProvider

    provider = OpenAIProvider(default_model=config.repair_model)
    repaired, usage = provider.repair_story_plan(
        user_intent=user_intent,
        plan=story,
        evaluation=evaluation,
        analyses=analyses,
        allowed_segment_ids={a.segment_id for a in analyses},
        model=config.repair_model,
    )
    repaired = repaired.model_copy(
        update={
            "user_intent": user_intent,
            "provider": "openai",
            "model": config.repair_model,
        }
    )
    return repaired, usage


def _passing_luna_stub() -> PlanEvaluation:
    return PlanEvaluation(
        overall_score=0.9,
        narrative_coherence=0.9,
        hook_strength=0.85,
        redundancy_score=0.1,
        chronology_score=0.9,
        caption_grounding=0.9,
        prompt_leakage=0.05,
        crop_safety=0.9,
        visual_variety=0.85,
        duration_suitability=0.9,
        failures=[],
        recommended_changes=[],
        requires_revision=False,
        evaluator_provider="openai",
        evaluator_model="gpt-5.6-luna",
    )


def evaluate_and_repair(
    work_dir: Path,
    project: str,
    bgm_track: Path | None = None,
    *,
    config: ModelConfig | None = None,
    force: bool = False,
    luna_fn: LunaFn | None = None,
    claude_revise_fn: ReviseFn | None = None,
    sol_repair_fn: SolFn | None = None,
    skip_luna: bool = False,
    rebuild_timeline: bool = True,
) -> Path:
    """Run code + Luna evaluation with at most one Claude revise and optional Sol."""
    cfg = config or load_model_config()
    project_dir = work_dir / project
    out_path = project_dir / "plan_evaluation.json"
    if out_path.exists() and not force:
        click.echo(f"{out_path} exists, skipping (use --force)")
        return out_path

    story = _load_story(project_dir)
    timeline = _load_timeline(project_dir)
    analyses = _load_analyses(project_dir)
    edl = _load_edl(project_dir)
    brief = load_user_intent(project_dir, story.user_intent)
    sheets = _contact_sheets(project_dir)
    allowed = {a.segment_id for a in analyses} | set(story.selected_segment_ids)
    usages: list[dict[str, Any]] = []

    def _run_code(
        s: StoryPlan, t: TimelinePlan | None, e: dict[str, Any] | None
    ) -> list[EvaluationFailure]:
        return code_evaluate_plan(
            user_intent=brief, story=s, timeline=t, analyses=analyses, edl=e
        )

    def _run_luna(s: StoryPlan, t: TimelinePlan | None) -> PlanEvaluation:
        if skip_luna:
            return _passing_luna_stub()
        if luna_fn is not None:
            ev, usage = luna_fn(
                user_intent=brief,
                plan=s,
                timeline=t,
                analyses=analyses,
                image_paths=sheets,
                allowed_segment_ids=allowed,
                model=cfg.evaluator_model,
            )
        else:
            ev, usage = _default_luna(
                user_intent=brief,
                story=s,
                timeline=t,
                analyses=analyses,
                sheets=sheets,
                config=cfg,
            )
        usages.append(usage.as_dict())
        return ev

    def _claude_revise(s: StoryPlan, ev: PlanEvaluation) -> StoryPlan:
        if claude_revise_fn is not None:
            revised, usage = claude_revise_fn(
                user_intent=brief,
                plan=s,
                evaluation=ev,
                analyses=analyses,
                allowed_segment_ids=allowed,
                model=cfg.director_model,
            )
        else:
            revised, usage = _default_claude_revise(
                user_intent=brief,
                story=s,
                evaluation=ev,
                analyses=analyses,
                config=cfg,
            )
        usages.append(usage.as_dict())
        return revised

    def _sol_repair(s: StoryPlan, ev: PlanEvaluation) -> StoryPlan:
        if sol_repair_fn is not None:
            repaired, usage = sol_repair_fn(
                user_intent=brief,
                plan=s,
                evaluation=ev,
                analyses=analyses,
                allowed_segment_ids=allowed,
                model=cfg.repair_model,
            )
        else:
            repaired, usage = _default_sol_repair(
                user_intent=brief,
                story=s,
                evaluation=ev,
                analyses=analyses,
                config=cfg,
            )
        usages.append(usage.as_dict())
        return repaired

    code_fail = _run_code(story, timeline, edl)
    luna = _run_luna(story, timeline)
    merged = merge_evaluations(code_fail, luna)
    conflict = judgments_conflict(code_failures=code_fail, luna=luna, story=story)
    last_luna = luna

    outcome = "pass"
    sol_used = False
    revision_path: Path | None = None
    sol_path: Path | None = None
    current = story

    if not merged.requires_revision and not important_failures(merged.failures):
        outcome = "pass"
    else:
        # First failure → Claude Sonnet revises StoryPlan once
        outcome = "claude_revise"
        try:
            revised = _claude_revise(current, merged)
            story_plan_against_candidates(revised, allowed)
            errs = validate_story_plan(
                revised, allowed_segment_ids=allowed, analyses=analyses
            )
            if errs:
                raise ValueError("; ".join(errs))
            current = revised
            revision_path = project_dir / "story_plan_revision.json"
            revision_path.write_text(
                json.dumps(
                    {
                        "project": project,
                        "source": "claude_sonnet_revise",
                        "based_on_failures": [
                            f.model_dump(mode="json") for f in merged.failures
                        ],
                        "plan": current.model_dump(mode="json"),
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            _write_story(project_dir, project, current, source="claude_sonnet_revise")
            if rebuild_timeline and bgm_track is not None:
                plan_timeline(work_dir, project, bgm_track, force=True)
                timeline = _load_timeline(project_dir)
                edl = _load_edl(project_dir)
        except Exception as exc:  # noqa: BLE001
            usages.append(
                CallUsage(
                    provider="anthropic",
                    model=cfg.director_model,
                    operation="repair_story_plan",
                    ok=False,
                    error=str(exc)[:300],
                ).as_dict()
            )
            current = story

        code_fail2 = _run_code(current, timeline, edl)
        last_luna = _run_luna(current, timeline)
        merged = merge_evaluations(code_fail2, last_luna)
        remain = important_failures(merged.failures)
        claude_failed = bool(remain)
        conflict = conflict or judgments_conflict(
            code_failures=code_fail2, luna=last_luna, story=current
        )

        if not remain and not merged.requires_revision:
            outcome = "pass_after_claude"
        elif should_call_sol(
            quality_mode=cfg.quality_mode,
            important_failures_remain=bool(remain),
            claude_revision_failed=claude_failed,
            judgments_conflict_flag=conflict,
        ):
            outcome = "sol_repair"
            try:
                repaired = _sol_repair(current, merged)
                story_plan_against_candidates(repaired, allowed)
                current = repaired.model_copy(update={"user_intent": brief})
                sol_used = True
                sol_path = project_dir / "sol_repair.json"
                sol_path.write_text(
                    json.dumps(
                        {
                            "project": project,
                            "source": "gpt_sol_repair",
                            "model": cfg.repair_model,
                            "based_on_failures": [
                                f.model_dump(mode="json") for f in merged.failures
                            ],
                            "plan": current.model_dump(mode="json"),
                        },
                        indent=2,
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                _write_story(project_dir, project, current, source="gpt_sol_repair")
                if rebuild_timeline and bgm_track is not None:
                    plan_timeline(work_dir, project, bgm_track, force=True)
                    timeline = _load_timeline(project_dir)
                    edl = _load_edl(project_dir)
                # No third LLM eval loop — code snapshot only after Sol
                code_fail3 = _run_code(current, timeline, edl)
                merged = merge_evaluations(code_fail3, last_luna)
                remain = important_failures(merged.failures)
                outcome = "pass_after_sol" if not remain else "sol_exhausted"
            except Exception as exc:  # noqa: BLE001
                usages.append(
                    CallUsage(
                        provider="openai",
                        model=cfg.repair_model,
                        operation="repair_story_plan",
                        ok=False,
                        error=str(exc)[:300],
                    ).as_dict()
                )
                outcome = "sol_failed_fallback_baseline"
                current = deterministic_story_plan(
                    brief, analyses, model="deterministic"
                )
                _write_story(
                    project_dir, project, current, source="deterministic_after_sol_fail"
                )
                if rebuild_timeline and bgm_track is not None:
                    plan_timeline(work_dir, project, bgm_track, force=True)
        else:
            # balanced / economy: clean deterministic baseline, no Sol
            outcome = "baseline_fallback"
            current = deterministic_story_plan(brief, analyses, model="deterministic")
            _write_story(
                project_dir, project, current, source="deterministic_balanced_fallback"
            )
            revision_path = revision_path or (project_dir / "story_plan_revision.json")
            if not revision_path.exists():
                revision_path.write_text(
                    json.dumps(
                        {
                            "project": project,
                            "source": "deterministic_balanced_fallback",
                            "based_on_failures": [
                                f.model_dump(mode="json") for f in merged.failures
                            ],
                            "plan": current.model_dump(mode="json"),
                        },
                        indent=2,
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
            if rebuild_timeline and bgm_track is not None:
                plan_timeline(work_dir, project, bgm_track, force=True)
                timeline = _load_timeline(project_dir)
                edl = _load_edl(project_dir)
            code_fail_b = _run_code(current, timeline, edl)
            merged = merge_evaluations(code_fail_b, last_luna)

    payload = {
        "project": project,
        "outcome": outcome,
        "quality_mode": cfg.quality_mode,
        "sol_used": sol_used,
        "judgments_conflict": conflict,
        "code_failures": [f.model_dump(mode="json") for f in code_fail],
        "evaluation": merged.model_dump(mode="json"),
        "story_plan_revision": str(revision_path.name) if revision_path else None,
        "sol_repair": str(sol_path.name) if sol_path and sol_used else None,
        "analyses_summary": summarize_analyses(analyses),
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    total_cost = sum(float(u.get("estimated_cost_usd") or 0) for u in usages)
    usage_path = project_dir / "plan_evaluation_usage.json"
    usage_path.write_text(
        json.dumps(
            {
                "project": project,
                "evaluator_model": cfg.evaluator_model,
                "repair_model": cfg.repair_model,
                "director_model": cfg.director_model,
                "quality_mode": cfg.quality_mode,
                "outcome": outcome,
                "sol_used": sol_used,
                "calls": usages,
                "estimated_cost_usd_total": round(total_cost, 6),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    click.echo(
        f"plan evaluation: outcome={outcome} sol_used={sol_used} "
        f"failures={len(merged.failures)} cost≈${total_cost:.4f} → {out_path}"
    )
    return out_path


@click.command()
@click.option("--work-dir", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--project", required=True)
@click.option("--bgm-track", type=click.Path(path_type=Path), default=None)
@click.option("--force", is_flag=True, default=False)
@click.option("--skip-luna", is_flag=True, default=False, help="Code-only (offline)")
def main(
    work_dir: Path,
    project: str,
    bgm_track: Path | None,
    force: bool,
    skip_luna: bool,
) -> None:
    out = evaluate_and_repair(
        work_dir,
        project,
        Path(bgm_track) if bgm_track else None,
        force=force,
        skip_luna=skip_luna,
        rebuild_timeline=bool(bgm_track),
    )
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
