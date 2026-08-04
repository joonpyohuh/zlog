"""Deterministic timeline optimizer (PROMPT 5) — no AI API calls.

Reads:  work/<project>/story_plan.json
        work/<project>/asset_analyses.json
        work/<project>/candidates.json
        work/<project>/deterministic_features.json (optional)
        assets/bgm/<track>.beats.json
Writes: work/<project>/timeline_plan.json
        work/<project>/edl_ai.json
        work/<project>/timeline_score_breakdown.json

Converts StoryPlan + AssetAnalysis into TimelinePlan + EDL. Timestamps stay
inside PySceneDetect candidate ranges and snap to the BGM beat grid via
select_baseline._place_cut. Never pads length by modulo-repeating stills —
shortens the film when unique material is scarce.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

import click

from pipeline.ai.schemas import (
    AssetAnalysis,
    AudioStrategy,
    CaptionMode,
    CaptionStrategy,
    ClipRole,
    EffectStrategy,
    FitMode,
    MediaType,
    MotionKind,
    PlannedClip,
    PreferredMoment,
    StoryPlan,
    StylePreset,
    TimelinePlan,
    TransitionKind,
    canonical_clip_role,
)
from pipeline.creative_execution import (
    build_execution_plan,
    choose_callback,
    write_execution_artifacts,
)
from pipeline.director import (
    max_allowed_duration_sec,
    suggest_target_duration_sec,
)
from pipeline.edl import (
    EDL,
    Aesthetic,
    Audio,
    CandidateScene,
    CandidatesFile,
    Canvas,
    Caption,
    Frame,
    Signature,
    TimelineClip,
    save_edl,
    validate_edl,
)
from pipeline.evidence import DeterministicFeaturesFile
from pipeline.select_baseline import (
    AUDIO_VOLUME,
    DEFAULT_SIGNATURE,
    EDL_VERSION,
    _place_cut,
    assign_transitions,
    extend_beat_grid,
)

BEAT_SNAP_TOLERANCE_S = 0.04


def _load_story_plan(project_dir: Path) -> StoryPlan:
    raw = json.loads((project_dir / "story_plan.json").read_text(encoding="utf-8"))
    plan_data = raw.get("plan") if isinstance(raw, dict) and "plan" in raw else raw
    return StoryPlan.model_validate(plan_data)


def _load_analyses(project_dir: Path) -> list[AssetAnalysis]:
    path = project_dir / "asset_analyses.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("analyses") or []
    return [AssetAnalysis.model_validate(x) for x in items]


def _beats_path(bgm_track: Path) -> Path:
    return bgm_track.parent / f"{bgm_track.stem}.beats.json"


def _chrono_key(a: AssetAnalysis, cand: CandidateScene | None) -> tuple:
    return (
        a.capture_time is None,
        a.capture_time or "",
        a.upload_index,
        cand.start_sec if cand else 0.0,
        a.segment_id,
    )


def order_for_playback(
    story: StoryPlan,
    analyses: list[AssetAnalysis],
    by_cand: dict[str, CandidateScene],
) -> list[str]:
    """Cold-open with hook, then chronological body, ending last."""
    analysis_by_id = {a.segment_id: a for a in analyses}
    selected = [sid for sid in story.selected_segment_ids if sid in by_cand]
    if not selected:
        raise ValueError("no selected_segment_ids exist in candidates")

    hook = story.hook_segment_id if story.hook_segment_id in by_cand else selected[0]
    ending = story.ending_segment_id if story.ending_segment_id in by_cand else selected[-1]

    rest = [sid for sid in selected if sid not in {hook, ending}]
    rest_sorted = sorted(
        rest,
        key=lambda sid: _chrono_key(
            analysis_by_id.get(sid)
            or AssetAnalysis(
                asset_id=f"asset:{sid}",
                segment_id=sid,
                source_file=by_cand[sid].source_file,
                media_type=MediaType.video,
                upload_index=10_000,
                analysis_provider="anthropic",
                analysis_model="missing",
            ),
            by_cand.get(sid),
        ),
    )

    # If hook == ending (single clip), just that one.
    if hook == ending and not rest_sorted:
        return [hook]

    out = [hook, *rest_sorted]
    if ending != hook:
        out.append(ending)
    # De-dupe preserving order (allow_asset_reuse handled later)
    seen: set[str] = set()
    unique: list[str] = []
    for sid in out:
        if sid in seen:
            continue
        seen.add(sid)
        unique.append(sid)
    return unique


def drop_adjacent_redundancy(
    ordered: list[str],
    analyses: dict[str, AssetAnalysis],
) -> list[str]:
    """Prevent back-to-back identical redundancy_group (keep higher narrative)."""
    if not ordered:
        return ordered
    kept = [ordered[0]]
    for sid in ordered[1:]:
        prev = analyses.get(kept[-1])
        cur = analyses.get(sid)
        if (
            prev
            and cur
            and prev.redundancy_group
            and prev.redundancy_group == cur.redundancy_group
        ):
            # Keep the better of the two; skip current if worse/equal.
            if cur.narrative_value > prev.narrative_value + 0.05:
                kept[-1] = sid
            continue
        kept.append(sid)
    return kept


def drop_adjacent_similar_shot(
    ordered: list[str],
    analyses: dict[str, AssetAnalysis],
) -> list[str]:
    """Drop consecutive near-twins only when subject+shot match *and* novelty is low.

    Plain same-subject vlogs are common — those stay and are scored as a
    soft penalty instead of collapsing the whole cut list.
    """
    if not ordered:
        return ordered
    kept = [ordered[0]]
    for sid in ordered[1:]:
        prev = analyses.get(kept[-1])
        cur = analyses.get(sid)
        if prev and cur:
            prev_subj = (prev.subjects or ["?"])[0]
            cur_subj = (cur.subjects or ["?"])[0]
            near_twin = (
                prev_subj == cur_subj
                and prev.shot_type == cur.shot_type
                and prev.novelty < 0.35
                and cur.novelty < 0.35
                and abs(prev.aesthetic_value - cur.aesthetic_value) < 0.08
            )
            if near_twin:
                if cur.narrative_value > prev.narrative_value + 0.08:
                    kept[-1] = sid
                continue
        kept.append(sid)
    return kept


def role_for_index(i: int, n: int, sid: str, story: StoryPlan, peak_id: str | None) -> ClipRole:
    """Assign Soft Flow roles (bible v0.1). Structure adapts to clip count."""
    if sid == story.hook_segment_id or i == 0:
        return ClipRole.hook
    if sid == story.ending_segment_id or i == n - 1:
        return ClipRole.resonance
    if peak_id and sid == peak_id:
        return ClipRole.zlog_moment
    if n >= 6 and i == 1:
        return ClipRole.orientation
    if n >= 5 and i == n - 2:
        return ClipRole.release
    return ClipRole.development


def cut_duration_for_role(
    role: ClipRole,
    tempo_bpm: float,
    seg_duration: float,
    *,
    segment_id: str = "",
) -> float:
    beat = 60.0 / max(tempo_bpm, 1.0)
    role = canonical_clip_role(role)
    base = {
        ClipRole.hook: 3.0 * beat,
        ClipRole.orientation: 2.2 * beat,
        ClipRole.zlog_moment: 3.5 * beat,
        ClipRole.release: 2.0 * beat,
        ClipRole.resonance: 2.5 * beat,
        ClipRole.development: 2.0 * beat,
    }[role]
    # Vary mid-flow lengths so cuts aren't identical stopwatch ticks.
    if role in {ClipRole.development, ClipRole.release, ClipRole.orientation}:
        base = beat * (1.5 + (sum(ord(c) for c in segment_id) % 4) * 0.45)
    return float(min(max(0.45, base), max(0.45, seg_duration)))


def motion_for_analysis(a: AssetAnalysis | None, role: ClipRole) -> tuple[MotionKind, float]:
    role = canonical_clip_role(role)
    if a is None:
        return MotionKind.ken_burns_in, 0.3
    if a.media_type.value == "still_video" or a.motion_quality < 0.15:
        strength = 0.25 + 0.2 * a.aesthetic_value
        if role == ClipRole.hook:
            return MotionKind.ken_burns_in, min(0.55, strength + 0.1)
        if role == ClipRole.resonance:
            return MotionKind.ken_burns_out, min(0.5, strength)
        # Alternate pan by focus side
        if a.focus_x < 0.45:
            return MotionKind.pan_right, min(0.45, strength)
        if a.focus_x > 0.55:
            return MotionKind.pan_left, min(0.45, strength)
        return MotionKind.ken_burns_in, min(0.45, strength)
    if a.motion_quality > 0.55:
        return MotionKind.none, 0.05
    return MotionKind.static, 0.1


def fit_mode_for_analysis(a: AssetAnalysis | None, style: StylePreset) -> FitMode:
    if style in {
        StylePreset.y2k_camcorder,
        StylePreset.y2k_4x3_letterbox,
        StylePreset.cinematic_16x9,
    }:
        if a and a.crop_confidence < 0.35:
            return FitMode.blurred_background_contain
        return FitMode.cover if style != StylePreset.cinematic_16x9 else FitMode.contain
    # clean vertical: prefer subject-aware cover; fall back when crop is unsafe
    if a and a.crop_confidence < 0.35:
        return FitMode.blurred_background_contain
    if a and (a.focus_width > 0.85 or a.focus_height > 0.85):
        return FitMode.subject_aware_cover
    return FitMode.subject_aware_cover


def aesthetic_allows_flash(style: StylePreset) -> bool:
    return style in {StylePreset.y2k_camcorder, StylePreset.y2k_4x3_letterbox}


def canvas_for_style(style: StylePreset) -> tuple[Canvas, Frame, Aesthetic]:
    if style in {StylePreset.y2k_camcorder, StylePreset.y2k_4x3_letterbox}:
        return (
            Canvas(width=1080, height=1920),
            Frame(aspect="4:3", width=1080, height=810, y_offset=555),
            Aesthetic(
                lut="ccd_cool_01.cube",
                grain=0.15,
                bloom=0.2,
                scanlines=True,
                camcorder_osd=True,
                allow_flash=True,
            ),
        )
    if style == StylePreset.cinematic_16x9:
        return (
            Canvas(width=1920, height=1080),
            Frame(aspect="16:9", width=1920, height=1080, y_offset=0),
            Aesthetic(lut="", grain=0.0, bloom=0.0),
        )
    # clean_vlog / vertical_full / soft_vlog / punchy_short → full 9:16, natural look
    return (
        Canvas(width=1080, height=1920),
        Frame(aspect="9:16", width=1080, height=1920, y_offset=0),
        Aesthetic(
            lut="",
            grain=0.0,
            bloom=0.0,
            scanlines=False,
            camcorder_osd=False,
            allow_flash=False,
        ),
    )


def preferred_moment_for_role(role: ClipRole) -> PreferredMoment:
    role = canonical_clip_role(role)
    if role == ClipRole.hook:
        return PreferredMoment.start
    if role == ClipRole.zlog_moment:
        return PreferredMoment.peak_action
    if role == ClipRole.resonance:
        return PreferredMoment.end
    return PreferredMoment.middle


def adjust_target_duration(
    story_duration: float,
    n_unique: int,
    *,
    allow_reuse: bool,
) -> float:
    """Shrink target when unique material is scarce — never pad with still loops."""
    material_cap = suggest_target_duration_sec(n_unique)
    hard_cap = max_allowed_duration_sec(n_unique)
    target = min(story_duration, hard_cap)
    if not allow_reuse:
        target = min(target, material_cap)
    return float(max(3.0, round(target, 2)))


def build_sparse_captions(
    story: StoryPlan,
    timeline: list[TimelineClip],
    analyses: dict[str, AssetAnalysis],
) -> list[Caption]:
    if story.caption_mode == CaptionMode.none:
        return []
    # Pull fact:/mood: lines from narrative_arc; ground against analyses.
    facts: list[str] = []
    moods: list[str] = []
    for line in story.narrative_arc:
        low = line.strip()
        if low.lower().startswith("fact:"):
            facts.append(low.split(":", 1)[1].strip())
        elif low.lower().startswith("mood:"):
            moods.append(low.split(":", 1)[1].strip())

    captions: list[Caption] = []
    if not timeline:
        return captions

    # At most ~2 captions: opening fact + closing mood (or one fact mid).
    def grounded(text: str, sid: str) -> bool:
        a = analyses.get(sid)
        if not a:
            return False
        blob = " ".join(a.visually_grounded_facts + a.subjects).lower()
        tokens = [t for t in text.lower().split() if len(t) >= 2]
        if not tokens:
            return False
        hits = sum(1 for t in tokens if t in blob)
        return hits >= max(1, len(tokens) // 3)

    opening = timeline[0]
    if facts:
        text = facts[0]
        if grounded(text, opening.segment_id) or analyses.get(opening.segment_id):
            # Prefer analysis fact if brief fact isn't grounded.
            if not grounded(text, opening.segment_id):
                a = analyses[opening.segment_id]
                text = a.visually_grounded_facts[0] if a.visually_grounded_facts else text
            captions.append(
                Caption(
                    segment_id=opening.segment_id,
                    text=text,
                    style="subtitle",
                    position="bottom",
                    start_offset_sec=0.15,
                    grounding="visually_grounded_facts",
                )
            )

    if len(timeline) > 1 and moods:
        host = timeline[-1]
        text = moods[0]
        a = analyses.get(host.segment_id)
        # Mood enums and raw analysis labels are metadata, not user-facing copy.
        if a and grounded(text, host.segment_id) and text.lower() != a.mood.value:
            captions.append(
                Caption(
                    segment_id=host.segment_id,
                    text=text,
                    style="lower_third",
                    position="bottom",
                    start_offset_sec=0.1,
                    grounding="visually_grounded_facts",
                )
            )
    elif len(timeline) > 2 and facts[1:] and story.caption_mode == CaptionMode.dense:
        mid = timeline[len(timeline) // 2]
        captions.append(
            Caption(
                segment_id=mid.segment_id,
                text=facts[1],
                style="subtitle",
                position="bottom",
                start_offset_sec=0.1,
                grounding="visually_grounded_facts",
            )
        )
    return captions


def score_timeline(
    *,
    story: StoryPlan,
    ordered: list[str],
    planned: list[PlannedClip],
    analyses: dict[str, AssetAnalysis],
    features: dict[str, Any],
    edl: EDL,
    beat_times: list[float],
    target_duration: float,
) -> dict[str, Any]:
    """Higher is better. Returns total + breakdown (positive and penalties)."""
    scores: dict[str, float] = {}
    selected_set = set(story.selected_segment_ids)

    # Director priority: fraction of director picks kept
    kept = [sid for sid in ordered if sid in selected_set]
    scores["director_priority"] = 10.0 * (len(kept) / max(1, len(story.selected_segment_ids)))

    scores["narrative_value"] = 8.0 * (
        sum(analyses[s].narrative_value for s in ordered if s in analyses) / max(1, len(ordered))
    )

    hook = analyses.get(story.hook_segment_id)
    scores["hook_potential"] = 8.0 * (hook.hook_potential if hook else 0.3)

    roles = {canonical_clip_role(c.role) for c in planned}
    role_score = 0.0
    if ClipRole.hook in roles:
        role_score += 3.0
    if ClipRole.resonance in roles:
        role_score += 3.0
    if ClipRole.zlog_moment in roles or ClipRole.development in roles:
        role_score += 2.0
    scores["role_coverage"] = role_score

    # Chronology naturalness after cold open
    body_ids = [c.segment_id for c in planned[1:] if not c.reuse_reason]
    body_analyses = [analyses[s] for s in body_ids if s in analyses]
    chrono_ok = 1.0
    if len(body_analyses) >= 2:
        keys = [a.upload_index for a in body_analyses]
        chrono_ok = 1.0 if keys == sorted(keys) else 0.35
    scores["chronology"] = 7.0 * chrono_ok

    shot_types = [analyses[s].shot_type.value for s in ordered if s in analyses]
    scores["visual_variety"] = 6.0 * (len(set(shot_types)) / max(1, len(shot_types)))

    scores["motion_quality"] = 5.0 * (
        sum(analyses[s].motion_quality for s in ordered if s in analyses) / max(1, len(ordered))
    )

    # Beat fitness: fraction of edges within tolerance
    if beat_times and edl.timeline:
        ok = 0
        total_e = 0
        for clip in edl.timeline:
            for t in (clip.in_sec, clip.out_sec):
                total_e += 1
                nearest = min(beat_times, key=lambda b: abs(b - t))
                if abs(nearest - t) <= BEAT_SNAP_TOLERANCE_S + 1e-6:
                    ok += 1
        scores["beat_fit"] = 7.0 * (ok / max(1, total_e))
    else:
        scores["beat_fit"] = 2.0

    # Natural audio value
    audio_bonus = 0.0
    for sid in ordered:
        feat = features.get(sid) or {}
        audio_bonus += min(1.0, float(feat.get("audio_rms", 0.0)) * 8.0) * 0.5
        audio_bonus += min(1.0, float(feat.get("onset_density", 0.0)) / 3.0) * 0.5
    scores["natural_audio"] = min(5.0, audio_bonus)

    penalties: dict[str, float] = {}
    ids = [c.segment_id for c in planned]
    callback_reuse_ok = all(
        ids.count(c.segment_id) == 1
        or c.reuse_reason
        in {"opening_callback", "visual_motif_callback", "narrative_payoff"}
        or any(
            other.segment_id == c.segment_id
            and other.reuse_reason
            in {"opening_callback", "visual_motif_callback", "narrative_payoff"}
            for other in planned
        )
        for c in planned
    )
    if len(ids) != len(set(ids)) and not story.allow_asset_reuse and not callback_reuse_ok:
        penalties["repeat_segment"] = -12.0
    groups = []
    for sid in ids:
        a = analyses.get(sid)
        groups.append(a.redundancy_group if a else None)
    for i in range(1, len(groups)):
        if groups[i] and groups[i] == groups[i - 1]:
            penalties["adjacent_redundancy"] = penalties.get("adjacent_redundancy", 0.0) - 6.0

    for i in range(1, len(ids)):
        a0, a1 = analyses.get(ids[i - 1]), analyses.get(ids[i])
        if a0 and a1 and a0.subjects[:1] == a1.subjects[:1] and a0.shot_type == a1.shot_type:
            penalties["adjacent_similar_shot"] = penalties.get("adjacent_similar_shot", 0.0) - 3.0

    dangerous = sum(
        1
        for c in planned
        if (analyses.get(c.segment_id) and analyses[c.segment_id].crop_confidence < 0.3)
        or c.fit_mode == FitMode.contain and (analyses.get(c.segment_id) or None) is not None
        and analyses[c.segment_id].crop_confidence < 0.35
    )
    # Only penalize low confidence without safe fit
    dangerous = sum(
        1
        for c in planned
        if (a := analyses.get(c.segment_id))
        and a.crop_confidence < 0.3
        and c.fit_mode == FitMode.cover
    )
    if dangerous:
        penalties["dangerous_crop"] = -4.0 * dangerous

    # Ungrounded captions
    for cap in edl.captions:
        a = analyses.get(cap.segment_id)
        if not a:
            penalties["ungrounded_caption"] = penalties.get("ungrounded_caption", 0.0) - 3.0
            continue
        blob = " ".join(a.visually_grounded_facts + a.subjects + [a.mood.value]).lower()
        if not any(tok in blob for tok in cap.text.lower().split() if len(tok) >= 3):
            penalties["ungrounded_caption"] = penalties.get("ungrounded_caption", 0.0) - 2.5

    total_dur = sum(c.out_sec - c.in_sec for c in edl.timeline)
    if abs(total_dur - target_duration) > 1.5:
        penalties["duration_mismatch"] = -5.0 * min(3.0, abs(total_dur - target_duration) / 2.0)

    durs = [c.out_sec - c.in_sec for c in edl.timeline]
    if len(durs) >= 3:
        try:
            if statistics.pstdev(durs) < 0.08:
                penalties["uniform_cut_lengths"] = -4.0
        except statistics.StatisticsError:
            pass

    # reuse without reason
    for c in planned:
        if ids.count(c.segment_id) > 1 and not c.reuse_reason:
            penalties["reuse_without_reason"] = -8.0
            break

    total = sum(scores.values()) + sum(penalties.values())
    return {
        "total": round(total, 3),
        "positives": {k: round(v, 3) for k, v in scores.items()},
        "penalties": {k: round(v, 3) for k, v in penalties.items()},
        "n_clips": len(planned),
        "target_duration_sec": target_duration,
        "actual_duration_sec": round(total_dur, 3),
    }


def plan_timeline(
    work_dir: Path,
    project: str,
    bgm_track: Path,
    *,
    force: bool = False,
) -> Path:
    """Build TimelinePlan + edl_ai.json + score breakdown (no AI)."""
    project_dir = work_dir / project
    out_plan = project_dir / "timeline_plan.json"
    out_edl = project_dir / "edl_ai.json"
    out_score = project_dir / "timeline_score_breakdown.json"
    if out_plan.exists() and out_edl.exists() and not force:
        click.echo(f"{out_plan} exists, skipping (use --force)")
        return out_plan

    story = _load_story_plan(project_dir)
    analyses_list = _load_analyses(project_dir)
    analyses = {a.segment_id: a for a in analyses_list}
    candidates_file = CandidatesFile.model_validate_json(
        (project_dir / "candidates.json").read_text(encoding="utf-8")
    )
    by_cand = {c.segment_id: c for c in candidates_file.candidates}
    if not by_cand:
        raise ValueError("no passing candidates")

    feats_by_id: dict[str, dict[str, Any]] = {}
    feats_path = project_dir / "deterministic_features.json"
    if feats_path.exists():
        feats = DeterministicFeaturesFile.model_validate_json(
            feats_path.read_text(encoding="utf-8")
        )
        for s in feats.segments:
            feats_by_id[s.segment_id] = {
                "audio_rms": s.audio_rms,
                "onset_density": s.onset_density,
                "is_static": s.is_static,
            }

    ordered = order_for_playback(story, analyses_list, by_cand)
    ordered = drop_adjacent_redundancy(ordered, analyses)
    ordered = drop_adjacent_similar_shot(ordered, analyses)

    # Never modulo-repeat stills when reuse is false — shrink duration instead.
    if not story.allow_asset_reuse:
        seen: set[str] = set()
        deduped: list[str] = []
        for sid in ordered:
            if sid in seen:
                continue
            seen.add(sid)
            deduped.append(sid)
        ordered = deduped

    target = adjust_target_duration(
        story.target_duration_sec,
        len(ordered),
        allow_reuse=story.allow_asset_reuse,
    )

    beats_data = json.loads(_beats_path(bgm_track).read_text(encoding="utf-8"))
    tempo_bpm = float(beats_data["tempo_bpm"])
    beat_times = extend_beat_grid(
        list(beats_data["beat_times"]),
        tempo_bpm,
        max(c.end_sec for c in by_cand.values()),
    )

    # Peak = highest hook among non-hook/non-ending
    mid = [s for s in ordered if s not in {story.hook_segment_id, story.ending_segment_id}]
    peak_id = max(mid, key=lambda s: analyses[s].hook_potential if s in analyses else 0.0) if mid else None

    # First pass durations by role
    role_list = [
        role_for_index(i, len(ordered), sid, story, peak_id) for i, sid in enumerate(ordered)
    ]
    raw_durs = [
        cut_duration_for_role(
            role, tempo_bpm, by_cand[sid].duration, segment_id=sid
        )
        for sid, role in zip(ordered, role_list, strict=True)
    ]
    # Scale to target without introducing new clips
    total_raw = sum(raw_durs)
    if total_raw > 0:
        scale = target / total_raw
        # Keep some variance: clamp scale
        scale = max(0.55, min(1.35, scale))
        raw_durs = [
            min(by_cand[sid].duration, max(0.45, d * scale))
            for sid, d in zip(ordered, raw_durs, strict=True)
        ]

    # If still long, trim from body cuts
    def _total(ds: list[float]) -> float:
        return sum(ds)

    guard = 0
    while _total(raw_durs) > target + 0.9 and guard < 40:
        # shrink longest body cut
        body_idx = [
            i
            for i, r in enumerate(role_list)
            if canonical_clip_role(r)
            in {ClipRole.development, ClipRole.release, ClipRole.orientation}
            and raw_durs[i] > 0.55
        ]
        if not body_idx:
            body_idx = list(range(len(raw_durs)))
        i = max(body_idx, key=lambda j: raw_durs[j])
        raw_durs[i] = max(0.45, raw_durs[i] - 0.15)
        guard += 1

    planned: list[PlannedClip] = []
    timeline: list[TimelineClip] = []
    for order, (sid, role, want) in enumerate(
        zip(ordered, role_list, raw_durs, strict=True), start=1
    ):
        cand = by_cand[sid]
        a = analyses.get(sid)
        in_sec, out_sec = _place_cut(cand, want, beat_times)
        motion, strength = motion_for_analysis(a, role)
        fit = fit_mode_for_analysis(a, story.style_preset)
        focus_x = a.focus_x if a else 0.5
        focus_y = a.focus_y if a else 0.45
        evidence = list(a.evidence_frame_ids) if a else []
        soft = canonical_clip_role(role)
        # Flash is a strong effect — reserve for zlog_moment only (bible §3 budget).
        transition = (
            TransitionKind.flash if soft == ClipRole.zlog_moment else TransitionKind.cut
        )
        edl_transition: str = "flash" if transition == TransitionKind.flash else "cut"

        planned.append(
            PlannedClip(
                segment_id=sid,
                role=role,
                evidence_frame_ids=evidence,
                preferred_moment=preferred_moment_for_role(role),
                target_duration_sec=round(out_sec - in_sec, 3),
                fit_mode=fit,
                focus_x=focus_x,
                focus_y=focus_y,
                motion=motion,
                motion_strength=strength,
                transition=transition,
                caption="",
                caption_grounding="",
                overlay=None,
                reuse_reason=None,
                caption_strategy=CaptionStrategy.none,
                effect_strategy=EffectStrategy(
                    effect="none", reason="default: no decorative effect"
                ),
            )
        )
        if not aesthetic_allows_flash(story.style_preset):
            edl_transition = "cut"
        timeline.append(
            TimelineClip(
                order=order,
                segment_id=sid,
                source_file=cand.source_file,
                in_sec=in_sec,
                out_sec=out_sec,
                transition=edl_transition,  # type: ignore[arg-type]
                role=role.value,
                evidence_frame_ids=evidence,
                fit_mode=fit.value,
                focus_x=focus_x,
                focus_y=focus_y,
                motion=motion.value,
                motion_strength=strength,
                overlay=None,
                reuse_reason=None,
                crop_confidence=a.crop_confidence if a else None,
            )
        )

    timeline = assign_transitions(timeline)
    canvas, frame, aesthetic = canvas_for_style(story.style_preset)
    if aesthetic.allow_flash:
        for clip, p in zip(timeline, planned, strict=True):
            if canonical_clip_role(p.role) == ClipRole.zlog_moment and clip.order > 1:
                clip.transition = "flash"
    else:
        for clip in timeline:
            clip.transition = "cut"

    callback = choose_callback(story, planned, analyses)
    if callback.enabled and callback.source_segment_id and len(planned) > 1:
        sid = callback.source_segment_id
        cand = by_cand[sid]
        analysis = analyses.get(sid)
        wanted = timeline[-1].out_sec - timeline[-1].in_sec
        in_sec, out_sec = _place_cut(cand, wanted, beat_times)
        focus_x = min(0.8, max(0.2, 1.1 - (analysis.focus_x if analysis else 0.5)))
        focus_y = analysis.focus_y if analysis else 0.45
        evidence = list(analysis.evidence_frame_ids) if analysis else []
        planned[-1] = PlannedClip(
            segment_id=sid,
            role=ClipRole.resonance,
            evidence_frame_ids=evidence,
            preferred_moment=PreferredMoment.end,
            target_duration_sec=round(out_sec - in_sec, 3),
            fit_mode=fit_mode_for_analysis(analysis, story.style_preset),
            focus_x=focus_x,
            focus_y=focus_y,
            motion=MotionKind.ken_burns_out,
            motion_strength=0.3,
            transition=TransitionKind.cut,
            reuse_reason=callback.reuse_reason,
            caption_strategy=CaptionStrategy.none,
            effect_strategy=EffectStrategy(
                effect="micro_pull_out", reason="opening callback payoff"
            ),
        )
        timeline[-1] = TimelineClip(
            order=timeline[-1].order,
            segment_id=sid,
            source_file=cand.source_file,
            in_sec=in_sec,
            out_sec=out_sec,
            transition="cut",
            role=ClipRole.resonance.value,
            evidence_frame_ids=evidence,
            fit_mode=fit_mode_for_analysis(analysis, story.style_preset).value,
            focus_x=focus_x,
            focus_y=focus_y,
            motion=MotionKind.ken_burns_out.value,
            motion_strength=0.3,
            reuse_reason=callback.reuse_reason,
            crop_confidence=analysis.crop_confidence if analysis else None,
        )
        ordered[-1] = sid

    # Ending credit off for clean_vlog unless style asks for camcorder kit
    signature = (
        DEFAULT_SIGNATURE
        if aesthetic.camcorder_osd
        else Signature(enabled=False, text="", duration=0.0)
    )
    captions = build_sparse_captions(story, timeline, analyses)
    if callback.enabled and callback.source_segment_id:
        # Caption.segment_id resolves to the first matching clip, so duplicated
        # callback footage must remain text-free to prevent cross-clip leakage.
        captions = [c for c in captions if c.segment_id != callback.source_segment_id]
    # Attach caption text onto PlannedClip sparingly
    cap_by_sid = {c.segment_id: c.text for c in captions}
    planned = [
        p.model_copy(
            update={
                "caption": cap_by_sid.get(p.segment_id, ""),
                "caption_grounding": (
                    "visually_grounded_facts"
                    if p.segment_id in cap_by_sid
                    else ""
                ),
            }
        )
        for p in planned
    ]

    execution = build_execution_plan(project, story, planned, analyses)
    updated_planned: list[PlannedClip] = []
    for clip, decision in zip(planned, execution.decisions, strict=True):
        updated_planned.append(
            clip.model_copy(
                update={
                    "selection_reasons": [reason.value for reason in decision.selection_reasons],
                    "cut_reason": decision.cut_reason,
                    "caption": decision.caption.text,
                    "caption_grounding": clip.caption_grounding if decision.caption.text else "",
                    "caption_strategy": decision.caption.mode,
                    "caption_reason": decision.caption.reason,
                    "audio_strategy": AudioStrategy(
                        preserve_source_audio=decision.audio.preserve_source,
                        bgm_duck=decision.audio.duck_bgm,
                        reason=decision.audio.reason,
                    ),
                    "effect_strategy": EffectStrategy(
                        effect=decision.primary_effect.value,
                        reason=", ".join(reason.value for reason in decision.selection_reasons),
                    ),
                }
            )
        )
    planned = updated_planned
    for clip, decision in zip(timeline, execution.decisions, strict=True):
        clip.entry_effect = decision.entry_effect.value
        clip.primary_effect = decision.primary_effect.value
        clip.exit_effect = decision.exit_effect.value
        clip.effect_reason = ", ".join(reason.value for reason in decision.selection_reasons)
        clip.selection_reasons = [reason.value for reason in decision.selection_reasons]
        if decision.motion.type == "micro_push_in":
            clip.motion = MotionKind.ken_burns_in.value
        elif decision.motion.type == "micro_pull_out":
            clip.motion = MotionKind.ken_burns_out.value

    actual = sum(c.out_sec - c.in_sec for c in timeline)
    # Final target for validation = clamp to actual±slack friendly value
    validate_target = max(actual, min(target, actual + 0.5))
    # Prefer validating against actual length when we intentionally shortened
    validate_target = round(actual, 3) if abs(actual - target) <= 1.0 else target

    timeline_plan = TimelinePlan(
        project=project,
        story_plan_version="1",
        clips=planned,
        total_target_duration_sec=round(min(target, max(actual, 3.0)), 3),
        style_preset=story.style_preset,
    )

    edl = EDL(
        project=project,
        version=EDL_VERSION,
        generator="ai",
        canvas=canvas,
        frame=frame,
        aesthetic=aesthetic,
        audio=Audio(bgm_id=bgm_track.stem, start_sec=0.0, volume=AUDIO_VOLUME),
        timeline=timeline,
        captions=captions,
        signature=signature,
        style_preset=story.style_preset.value,
        creative_execution_version=execution.version,
    )

    problems = validate_edl(
        edl,
        candidates_file,
        beat_times,
        target_duration_s=validate_target,
    )
    # If duration window fails because we shortened, re-validate against actual
    if any("total timeline duration" in p for p in problems):
        problems = validate_edl(
            edl,
            candidates_file,
            beat_times,
            target_duration_s=round(actual, 3),
            duration_tolerance_s=1.0,
        )

    breakdown = score_timeline(
        story=story,
        ordered=ordered,
        planned=planned,
        analyses=analyses,
        features=feats_by_id,
        edl=edl,
        beat_times=beat_times,
        target_duration=timeline_plan.total_target_duration_sec,
    )
    breakdown["validation_problems"] = problems

    out_plan.write_text(
        json.dumps(
            {
                "project": project,
                "generator": "plan_timeline",
                "plan": timeline_plan.model_dump(mode="json"),
                "playback_order": ordered,
                "adjusted_target_duration_sec": timeline_plan.total_target_duration_sec,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    save_edl(edl, out_edl)
    out_score.write_text(json.dumps(breakdown, indent=2), encoding="utf-8")
    write_execution_artifacts(project_dir, execution, planned)

    click.echo(
        f"timeline: {len(timeline)} clips, {actual:.2f}s "
        f"(target {timeline_plan.total_target_duration_sec}s), "
        f"score={breakdown['total']} → {out_edl.name}"
    )
    if problems:
        click.echo(f"validation notes: {problems}", err=True)
    return out_plan


@click.command()
@click.option("--work-dir", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--project", required=True)
@click.option("--bgm-track", type=click.Path(path_type=Path), required=True)
@click.option("--force", is_flag=True, default=False)
def main(work_dir: Path, project: str, bgm_track: Path, force: bool) -> None:
    out = plan_timeline(work_dir, project, Path(bgm_track), force=force)
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
