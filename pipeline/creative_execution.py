"""Deterministic creative execution decisions from existing Zlog artifacts."""

from __future__ import annotations

import html
import json
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pipeline.ai.schemas import (
    AssetAnalysis,
    CaptionStrategy,
    ClipRole,
    CreativeAudio,
    CreativeCallback,
    CreativeCaption,
    CreativeDecision,
    CreativeEffectBudget,
    CreativeExecutionPlan,
    CreativeMotion,
    EffectId,
    MediaType,
    Mood,
    PlannedClip,
    ReasonCode,
    SceneKind,
    ShotType,
    StoryPlan,
    canonical_clip_role,
)

CALLBACK_REASONS = frozenset({"opening_callback", "visual_motif_callback", "narrative_payoff"})


@dataclass(frozen=True)
class EffectDefinition:
    intensity: str
    allowed_roles: frozenset[ClipRole]
    max_per_video: int
    requires_caption: bool = False
    supports_still: bool = True
    supports_video: bool = True


ALL_ROLES = frozenset(
    {
        ClipRole.hook,
        ClipRole.orientation,
        ClipRole.development,
        ClipRole.zlog_moment,
        ClipRole.release,
        ClipRole.resonance,
    }
)

EFFECT_REGISTRY: dict[EffectId, EffectDefinition] = {
    EffectId.clean_cut: EffectDefinition("subtle", ALL_ROLES, 99),
    EffectId.micro_push_in: EffectDefinition(
        "subtle", ALL_ROLES - {ClipRole.resonance}, 99
    ),
    EffectId.micro_pull_out: EffectDefinition(
        "subtle", frozenset({ClipRole.release, ClipRole.resonance}), 99
    ),
    EffectId.reaction_punch_in: EffectDefinition(
        "strong", frozenset({ClipRole.hook, ClipRole.zlog_moment}), 1
    ),
    EffectId.blur_caption_focus: EffectDefinition(
        "medium",
        frozenset({ClipRole.orientation, ClipRole.development}),
        1,
        requires_caption=True,
    ),
    EffectId.freeze_reaction_hold: EffectDefinition(
        "strong", frozenset({ClipRole.zlog_moment}), 1
    ),
    EffectId.soft_reveal: EffectDefinition(
        "medium", frozenset({ClipRole.hook, ClipRole.orientation}), 1
    ),
    EffectId.ambient_outro: EffectDefinition(
        "subtle", frozenset({ClipRole.resonance}), 1
    ),
}


def effect_budget_for_duration(duration_sec: float) -> CreativeEffectBudget:
    scale = max(1.0, min(3.0, duration_sec / 10.0))
    return CreativeEffectBudget(
        strong_effects_max=round(2 * scale),
        medium_effects_max=round(3 * scale),
        caption_focus_effects_max=1,
    )


def choose_callback(
    story: StoryPlan,
    clips: list[PlannedClip],
    analyses: dict[str, AssetAnalysis],
) -> CreativeCallback:
    """One meaningful opening callback; short or weak cuts stay unique."""
    hook = analyses.get(story.hook_segment_id)
    enabled = bool(
        len(clips) >= 5
        and hook
        and story.hook_segment_id != story.ending_segment_id
        and hook.hook_potential >= 0.7
        and max(hook.emotional_value, hook.narrative_value) >= 0.55
    )
    if not enabled:
        return CreativeCallback()
    return CreativeCallback(
        enabled=True,
        source_segment_id=story.hook_segment_id,
        reuse_reason="opening_callback",
        alternate_crop=True,
    )


def _has_reaction(a: AssetAnalysis | None) -> bool:
    if not a:
        return False
    text = " ".join([a.action_progression, *a.visually_grounded_facts]).lower()
    return bool(a.subjects) and any(
        token in text for token in ("smile", "laugh", "gaze", "reaction", "look", "face")
    )


def _caption_text(clip: PlannedClip, a: AssetAnalysis | None) -> str:
    text = clip.caption.strip()
    if not text or not clip.caption_grounding.strip():
        return ""
    labels = {item.value for item in (*Mood, *SceneKind, *ShotType)}
    if text.lower() in labels:
        return ""
    if len(text.split()) > 5 and text.lower().startswith(("person ", "a person ")):
        return ""
    if a and text not in a.visually_grounded_facts:
        blob = " ".join(a.visually_grounded_facts + a.subjects).lower()
        if not any(token in blob for token in text.lower().split() if len(token) >= 3):
            return ""
    return text


def _effects_for(
    role: ClipRole,
    clip: PlannedClip,
    analysis: AssetAnalysis | None,
    used: Counter[EffectId],
    budget: CreativeEffectBudget,
) -> tuple[EffectId, EffectId, EffectId]:
    role = canonical_clip_role(role)
    entry = EffectId.clean_cut
    primary = EffectId.clean_cut
    exit_effect = EffectId.clean_cut

    if role == ClipRole.hook:
        entry = EffectId.soft_reveal
        if (
            analysis
            and analysis.crop_confidence >= 0.45
            and analysis.hook_potential >= 0.7
            and _has_reaction(analysis)
            and used[EffectId.reaction_punch_in] == 0
        ):
            primary = EffectId.reaction_punch_in
        else:
            primary = EffectId.micro_push_in
    elif role == ClipRole.orientation:
        primary = EffectId.micro_push_in
    elif role == ClipRole.development:
        if clip.caption and used[EffectId.blur_caption_focus] == 0:
            primary = EffectId.blur_caption_focus
        elif analysis and analysis.media_type != MediaType.video:
            primary = EffectId.micro_push_in
    elif role == ClipRole.zlog_moment:
        strong_used = sum(
            used[e]
            for e in (EffectId.reaction_punch_in, EffectId.freeze_reaction_hold)
        )
        if (
            analysis
            and analysis.crop_confidence >= 0.45
            and _has_reaction(analysis)
            and used[EffectId.reaction_punch_in] == 0
            and strong_used < budget.strong_effects_max
        ):
            primary = EffectId.reaction_punch_in
        elif (
            analysis
            and analysis.emotional_value >= 0.55
            and used[EffectId.freeze_reaction_hold] == 0
            and strong_used < budget.strong_effects_max
        ):
            primary = EffectId.freeze_reaction_hold
    elif role == ClipRole.release:
        primary = EffectId.clean_cut
    elif role == ClipRole.resonance:
        primary = EffectId.micro_pull_out
        exit_effect = EffectId.ambient_outro

    for effect in (entry, primary, exit_effect):
        if effect != EffectId.clean_cut:
            used[effect] += 1
    return entry, primary, exit_effect


def build_execution_plan(
    project: str,
    story: StoryPlan,
    clips: list[PlannedClip],
    analyses: dict[str, AssetAnalysis],
) -> CreativeExecutionPlan:
    duration = sum(clip.target_duration_sec for clip in clips)
    budget = effect_budget_for_duration(duration)
    callback = CreativeCallback()
    reused = [clip for clip in clips if clip.reuse_reason in CALLBACK_REASONS]
    if reused:
        callback = CreativeCallback(
            enabled=True,
            source_segment_id=reused[-1].segment_id,
            reuse_reason=reused[-1].reuse_reason,  # type: ignore[arg-type]
            alternate_crop=True,
        )

    used: Counter[EffectId] = Counter()
    decisions: list[CreativeDecision] = []
    for clip in clips:
        role = canonical_clip_role(clip.role)
        analysis = analyses.get(clip.segment_id)
        caption_text = _caption_text(clip, analysis)
        safe_clip = clip.model_copy(
            update={
                "caption": caption_text,
                "caption_grounding": clip.caption_grounding if caption_text else "",
            }
        )
        entry, primary, exit_effect = _effects_for(role, safe_clip, analysis, used, budget)
        reasons: list[ReasonCode] = []
        if role == ClipRole.hook:
            reasons.append(ReasonCode.hook_potential)
        if role == ClipRole.orientation:
            reasons.append(ReasonCode.spatial_orientation)
        if role == ClipRole.zlog_moment:
            reasons.append(ReasonCode.narrative_payoff)
        if role == ClipRole.resonance:
            reasons.append(ReasonCode.ending_breath)
        if _has_reaction(analysis):
            reasons.append(ReasonCode.reaction_visible)
        if caption_text:
            reasons.append(ReasonCode.important_caption)
        if analysis and analysis.crop_confidence < 0.45:
            reasons.append(ReasonCode.low_crop_confidence)
        if clip.reuse_reason:
            reasons.append(ReasonCode.callback_payoff)
        if not reasons:
            reasons.append(ReasonCode.no_effect_needed)

        motion_type = "none"
        if primary == EffectId.micro_push_in:
            motion_type = "micro_push_in"
        elif primary == EffectId.micro_pull_out:
            motion_type = "micro_pull_out"
        preserve_audio = bool(analysis and analysis.media_type == MediaType.video)
        decisions.append(
            CreativeDecision(
                segment_id=clip.segment_id,
                narrative_role=role,
                selection_reasons=reasons,
                cut_reason=f"{role.value} advances the Soft Flow arc",
                entry_effect=entry,
                primary_effect=primary,
                exit_effect=exit_effect,
                motion=CreativeMotion(
                    type=motion_type,  # type: ignore[arg-type]
                    strength=clip.motion_strength,
                    focus_x=clip.focus_x,
                    focus_y=clip.focus_y,
                ),
                caption=CreativeCaption(
                    mode=CaptionStrategy.contextual if caption_text else CaptionStrategy.none,
                    text=caption_text,
                    reason=(
                        "grounded visual context" if caption_text else "image or sound carries the beat"
                    ),
                ),
                audio=CreativeAudio(
                    preserve_source=preserve_audio,
                    duck_bgm=preserve_audio and role in {ClipRole.hook, ClipRole.zlog_moment},
                    reason=("preserve available natural audio" if preserve_audio else "no source audio required"),
                ),
                reuse_reason=clip.reuse_reason,  # type: ignore[arg-type]
                confidence=round(1.0 - (analysis.uncertainty if analysis else 0.5), 3),
            )
        )

    plan = CreativeExecutionPlan(
        project=project,
        creative_intent=story.concept,
        opening_strategy="soft_reveal" if decisions[0].entry_effect == EffectId.soft_reveal else "cold_open",
        ending_strategy="ambient_hold",
        callback=callback,
        effect_budget=budget,
        decisions=decisions,
    )
    validate_execution_plan(plan, analyses)
    return plan


def validate_execution_plan(
    plan: CreativeExecutionPlan,
    analyses: dict[str, AssetAnalysis],
) -> None:
    effects = [
        effect
        for decision in plan.decisions
        for effect in (decision.entry_effect, decision.primary_effect, decision.exit_effect)
        if effect != EffectId.clean_cut
    ]
    counts = Counter(effects)
    strong = sum(count for effect, count in counts.items() if EFFECT_REGISTRY[effect].intensity == "strong")
    medium = sum(count for effect, count in counts.items() if EFFECT_REGISTRY[effect].intensity == "medium")
    if strong > plan.effect_budget.strong_effects_max:
        raise ValueError("strong effect budget exceeded")
    if medium > plan.effect_budget.medium_effects_max:
        raise ValueError("medium effect budget exceeded")
    if counts[EffectId.blur_caption_focus] > plan.effect_budget.caption_focus_effects_max:
        raise ValueError("caption focus effect budget exceeded")

    for decision in plan.decisions:
        role = canonical_clip_role(decision.narrative_role)
        for effect in (decision.entry_effect, decision.primary_effect, decision.exit_effect):
            definition = EFFECT_REGISTRY[effect]
            if role not in definition.allowed_roles:
                raise ValueError(f"{effect.value} is not allowed for {role.value}")
            if counts[effect] > definition.max_per_video:
                raise ValueError(f"{effect.value} exceeds max_per_video")
            if definition.requires_caption and not decision.caption.text:
                raise ValueError(f"{effect.value} requires a caption")
        analysis = analyses.get(decision.segment_id)
        if (
            decision.primary_effect == EffectId.reaction_punch_in
            and analysis
            and analysis.crop_confidence < 0.45
        ):
            raise ValueError("reaction_punch_in rejected for low crop confidence")


def write_execution_artifacts(
    project_dir: Path,
    plan: CreativeExecutionPlan,
    clips: list[PlannedClip],
) -> None:
    (project_dir / "creative_execution_plan.json").write_text(
        plan.model_dump_json(indent=2), encoding="utf-8"
    )
    counts = Counter(
        effect.value
        for decision in plan.decisions
        for effect in (decision.entry_effect, decision.primary_effect, decision.exit_effect)
        if effect != EffectId.clean_cut
    )
    budget_report = {
        "budget": plan.effect_budget.model_dump(),
        "used": dict(sorted(counts.items())),
        "strong_used": sum(count for effect, count in counts.items() if EFFECT_REGISTRY[EffectId(effect)].intensity == "strong"),
        "medium_used": sum(count for effect, count in counts.items() if EFFECT_REGISTRY[EffectId(effect)].intensity == "medium"),
        "callback_used": plan.callback.enabled,
    }
    (project_dir / "effect_budget_report.json").write_text(
        json.dumps(budget_report, indent=2), encoding="utf-8"
    )

    rows = []
    for index, (decision, clip) in enumerate(zip(plan.decisions, clips, strict=True), start=1):
        rows.append(
            "<tr>"
            f"<td>{index}</td><td>{html.escape(decision.segment_id)}</td>"
            f"<td>{decision.narrative_role.value}</td>"
            f"<td>{decision.primary_effect.value}</td>"
            f"<td>{html.escape(', '.join(reason.value for reason in decision.selection_reasons))}</td>"
            f"<td>{html.escape(decision.caption.text or '—')}</td>"
            f"<td>{html.escape(clip.caption_grounding or '—')}</td>"
            f"<td>{html.escape(decision.reuse_reason or '—')}</td>"
            f"<td>{clip.target_duration_sec:.2f}s</td>"
            f"<td>{html.escape(clip.evidence_frame_ids[0] if clip.evidence_frame_ids else '—')}</td>"
            "</tr>"
        )
    report = f"""<!doctype html><html><head><meta charset="utf-8"><title>Creative execution</title>
<style>body{{font:14px system-ui;background:#111;color:#eee;padding:24px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #444;padding:8px;text-align:left}}th{{background:#222}}</style></head>
<body><h1>{html.escape(plan.project)} · Creative Execution v1</h1>
<p>Opening: {plan.opening_strategy} · Ending: {plan.ending_strategy} · Callback: {plan.callback.enabled}</p>
<table><thead><tr><th>#</th><th>Segment</th><th>Role</th><th>Effect</th><th>Reason</th><th>Caption</th><th>Grounding</th><th>Callback</th><th>Duration</th><th>Preview frame</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></body></html>"""
    (project_dir / "creative_execution_report.html").write_text(report, encoding="utf-8")


def preserve_previous_render(project_dir: Path) -> None:
    previous = project_dir / "final.mp4"
    if not previous.exists():
        return
    comparison = project_dir / "comparison"
    comparison.mkdir(parents=True, exist_ok=True)
    shutil.copy2(previous, comparison / "before.mp4")


def finalize_comparison(project_dir: Path) -> None:
    """Keep honest same-input artifacts when a prior render was available."""
    final = project_dir / "final.mp4"
    plan = project_dir / "creative_execution_plan.json"
    if not final.exists() or not plan.exists():
        return
    comparison = project_dir / "comparison"
    comparison.mkdir(parents=True, exist_ok=True)
    shutil.copy2(final, comparison / "after.mp4")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return
    for label in ("before", "after"):
        video = comparison / f"{label}.mp4"
        if not video.exists():
            continue
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(video),
                "-vf",
                "fps=1/2,scale=240:-1,tile=3x2",
                "-frames:v",
                "1",
                str(comparison / f"contact_sheet_{label}.jpg"),
            ],
            check=True,
        )
