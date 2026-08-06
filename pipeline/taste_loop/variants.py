"""Variant generator — one media set + one StyleProfile + one axis -> 4 EditTimelines.

Exactly one axis moves. Everything else on the four EDLs is byte-identical,
which is the only reason a pick tells us anything: if two axes moved at once
there would be no way to know which one earned the click.

The four EDLs are produced by *rewriting* a base EDL the normal pipeline
already made — this layer never re-plans, never calls a model, and never
invents a timecode. Where the avg_cut_duration axis needs new in/out points it
re-derives them from candidates.json through select_baseline._place_cut, so
cut edges stay inside their source segment and stay snapped to the beat grid
(CLAUDE.md 대원칙 2/3).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from pipeline.edl import (
    BEAT_SNAP_TOLERANCE_S,
    EDL,
    CandidatesFile,
    Caption,
    TimelineClip,
)
from pipeline.select_baseline import _place_cut, extend_beat_grid
from pipeline.taste_loop.axes import VARIANTS_PER_ROUND, TasteAxis, spec_for
from pipeline.taste_loop.media_sets import MediaSet
from pipeline.taste_loop.style_profile import StyleProfile

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Fallback caption text when the base EDL has fewer captions than the
# caption_frequency axis asks for. Kept deliberately contentless: this loop
# measures *how often* text appears, not what it says, and inventing
# descriptive text would be a claim about footage nobody looked at.
CAPTION_FILL_TEXT = "·"
CAPTION_FILL_GROUNDING = "taste_loop:caption_frequency_probe"

# Mirrors select_baseline._place_cut's minimum-usable-cut floor. Kept as a
# named constant here because assert_timing_invariants has to know when
# _place_cut was allowed to leave the beat grid.
MIN_CUT_SEC = 0.4


class EditTimeline(BaseModel):
    """One rendered-or-renderable variant: an EDL plus the axis value that made it."""

    timeline_id: str
    round_id: str
    media_set_id: str
    project: str
    axis: TasteAxis
    axis_value: float
    variant_index: int = Field(ge=0)
    edl: EDL
    created_at: str

    @property
    def total_duration_sec(self) -> float:
        return round(sum(c.out_sec - c.in_sec for c in self.edl.timeline), 3)

    @property
    def clip_count(self) -> int:
        return len(self.edl.timeline)

    @property
    def caption_count(self) -> int:
        return len([c for c in self.edl.captions if (c.grounding or "").strip()])


def _load_beat_grid(media_set: MediaSet, repo_root: Path) -> tuple[list[float], float]:
    beats_path = media_set.beats_path(repo_root)
    if not beats_path.exists():
        return [], 0.0
    data = json.loads(beats_path.read_text(encoding="utf-8"))
    beats = [float(b) for b in (data.get("beat_times") or [])]
    tempo = float(data.get("tempo_bpm") or 0.0)
    return beats, tempo


def _apply_avg_cut_duration(
    edl: EDL,
    value: float,
    candidates: CandidatesFile,
    beat_times: list[float],
) -> EDL:
    """Re-place every cut at ~`value` seconds, re-snapping to the beat grid.

    Clip order and segment choice are untouched; only each cut's in/out move.
    _place_cut clamps to the source segment, so a short segment simply cannot
    reach a long target — the realised average will trail the requested value
    on tight footage, and that is the honest behaviour rather than stretching
    beyond what was shot.
    """
    by_id = {c.segment_id: c for c in candidates.candidates}
    out_clips: list[TimelineClip] = []
    for clip in edl.timeline:
        candidate = by_id.get(clip.segment_id)
        if candidate is None:
            # Segment is not in the pass list (older EDL); leave the cut alone
            # rather than guessing a range for it.
            out_clips.append(clip.model_copy(deep=True))
            continue
        in_sec, out_sec = _place_cut(candidate, value, beat_times)
        out_clips.append(clip.model_copy(deep=True, update={"in_sec": in_sec, "out_sec": out_sec}))
    return edl.model_copy(deep=True, update={"timeline": out_clips})


def _target_count(fraction: float, total: int) -> int:
    """How many of `total` items a fraction should cover, at least 1 if > 0."""
    if total <= 0 or fraction <= 0:
        return 0
    return max(1, min(total, round(fraction * total)))


def _apply_caption_frequency(edl: EDL, value: float) -> EDL:
    """Put a caption on `value` of the clips, reusing the base EDL's text.

    Captions are attached to clips in timeline order. When the base EDL has
    fewer authored captions than the target count, the remainder use a
    contentless filler so the axis measures frequency without asserting
    anything about what is on screen.
    """
    clips = edl.timeline
    want = _target_count(value, len(clips))
    if want == 0:
        return edl.model_copy(deep=True, update={"captions": []})

    authored = [c for c in edl.captions if (c.text or "").strip()]
    by_segment = {c.segment_id: c for c in authored}

    # Spread the captions evenly across the timeline instead of front-loading.
    step = len(clips) / want
    chosen_indices = sorted({min(len(clips) - 1, round(i * step)) for i in range(want)})

    captions: list[Caption] = []
    spare = [c for c in authored if c.segment_id not in {clips[i].segment_id for i in chosen_indices}]
    for position, index in enumerate(chosen_indices):
        clip = clips[index]
        clip_duration = clip.out_sec - clip.in_sec
        source = by_segment.get(clip.segment_id)
        if source is None and spare:
            source = spare.pop(0)
        text = (source.text if source else "") or CAPTION_FILL_TEXT
        grounding = (source.grounding if source else "") or CAPTION_FILL_GROUNDING
        style = source.style if source else "subtitle"
        font = source.font if source else "body"
        position_hint = source.position if source else "bottom"
        # Hold the caption for most of the clip, leaving a frame of headroom
        # so validate_edl's end_offset <= clip_duration check always passes.
        end_offset = max(0.05, round(clip_duration - 0.02, 3))
        captions.append(
            Caption(
                segment_id=clip.segment_id,
                text=text,
                style=style,
                font=font,
                position=position_hint,
                start_offset_sec=0.0,
                end_offset_sec=end_offset,
                grounding=grounding,
            )
        )
    # Cap so the renderer's own caption limit never silently drops one — a
    # dropped caption would mean the logged axis_value did not reach the screen.
    return edl.model_copy(
        deep=True, update={"captions": captions, "max_captions": len(captions)}
    )


def _apply_push_in_strength(edl: EDL, value: float) -> EDL:
    """Set Ken Burns push-in strength on every clip that carries motion.

    Clips the planner marked as intentionally still (motion none/static) stay
    still — the axis is about how hard a push-in pushes, not about adding
    movement where the plan deliberately avoided it.
    """
    out_clips: list[TimelineClip] = []
    for clip in edl.timeline:
        if clip.motion in ("none", "static"):
            out_clips.append(clip.model_copy(deep=True))
            continue
        out_clips.append(
            clip.model_copy(
                deep=True, update={"motion": "ken_burns_in", "motion_strength": round(value, 4)}
            )
        )
    return edl.model_copy(deep=True, update={"timeline": out_clips})


def _apply_non_hard_cut_ratio(edl: EDL, value: float) -> EDL:
    """Make `value` of clip entries a flash instead of a hard cut.

    The first clip always stays a hard cut — a flash on frame 0 reads as a
    glitch, not a transition. The renderer gates flashes behind
    aesthetic.allow_flash, so this axis flips that toggle with it; that is the
    same decision, not a second axis.
    """
    clips = edl.timeline
    eligible = list(range(1, len(clips)))
    want = _target_count(value, len(eligible))

    flash_at: set[int] = set()
    if want > 0 and eligible:
        step = len(eligible) / want
        flash_at = {eligible[min(len(eligible) - 1, round(i * step))] for i in range(want)}

    out_clips = [
        clip.model_copy(deep=True, update={"transition": "flash" if i in flash_at else "cut"})
        for i, clip in enumerate(clips)
    ]
    aesthetic = edl.aesthetic.model_copy(deep=True, update={"allow_flash": bool(flash_at)})
    return edl.model_copy(deep=True, update={"timeline": out_clips, "aesthetic": aesthetic})


def assert_timing_invariants(
    edl: EDL,
    candidates: CandidatesFile,
    beat_times: list[float],
    *,
    context: str = "",
) -> None:
    """Re-assert 대원칙 2/3 on a rewritten EDL.

    This deliberately skips edl.validate_edl()'s total-duration check: the
    avg_cut_duration axis *is* a change in total length, so that check would
    fail by design. What must still hold is that no cut escaped its source
    segment and no edge left the beat grid — the two invariants that keep
    timing owned by FFmpeg/PySceneDetect and librosa rather than by this layer.
    """
    by_id = {c.segment_id: c for c in candidates.candidates}
    problems: list[str] = []

    for clip in edl.timeline:
        source = by_id.get(clip.segment_id)
        if source is None:
            continue  # not a pass-list segment; apply_axis left it untouched
        if not (source.start_sec <= clip.in_sec <= source.end_sec):
            problems.append(
                f"order={clip.order} in_sec={clip.in_sec} outside "
                f"[{source.start_sec}, {source.end_sec}]"
            )
        if not (source.start_sec <= clip.out_sec <= source.end_sec):
            problems.append(
                f"order={clip.order} out_sec={clip.out_sec} outside "
                f"[{source.start_sec}, {source.end_sec}]"
            )
        duration = clip.out_sec - clip.in_sec
        if duration <= 0:
            problems.append(f"order={clip.order} has non-positive duration")

        # _place_cut snaps to the grid *except* when the snapped cut would
        # collapse below its minimum-usable-duration floor (a zero-length
        # Remotion Sequence crashes the render). In that case it intentionally
        # returns an off-grid cut. Exempting the floor here keeps this guard
        # aligned with the shared helper's actual contract instead of failing
        # on its documented escape hatch.
        floor = min(MIN_CUT_SEC, source.end_sec - source.start_sec)
        at_floor = duration <= floor + 1e-6
        if beat_times and not at_floor:
            for label, t in (("in_sec", clip.in_sec), ("out_sec", clip.out_sec)):
                delta = min(abs(b - t) for b in beat_times)
                if delta > BEAT_SNAP_TOLERANCE_S:
                    problems.append(
                        f"order={clip.order} {label}={t} is {delta * 1000:.1f}ms off the beat grid"
                    )

    if problems:
        where = f" ({context})" if context else ""
        raise ValueError(f"variant broke timing invariants{where}: " + "; ".join(problems[:8]))


def apply_axis(
    edl: EDL,
    axis: TasteAxis,
    value: float,
    *,
    candidates: CandidatesFile,
    beat_times: list[float],
) -> EDL:
    """Return a copy of `edl` with exactly one axis rewritten to `value`."""
    spec_for(axis).validate_value(value, context="apply_axis")

    if axis is TasteAxis.avg_cut_duration:
        return _apply_avg_cut_duration(edl, value, candidates, beat_times)
    if axis is TasteAxis.caption_frequency:
        return _apply_caption_frequency(edl, value)
    if axis is TasteAxis.push_in_strength:
        return _apply_push_in_strength(edl, value)
    if axis is TasteAxis.non_hard_cut_ratio:
        return _apply_non_hard_cut_ratio(edl, value)
    raise NotImplementedError(f"axis {axis!r} has no apply_axis implementation")


def generate_variants(
    media_set: MediaSet,
    profile: StyleProfile,
    axis: TasteAxis | str,
    *,
    round_id: str | None = None,
    repo_root: Path | None = None,
    n_variants: int = VARIANTS_PER_ROUND,
) -> list[EditTimeline]:
    """Build `n_variants` EditTimelines that differ only along `axis`.

    Values are sampled inside the StyleProfile's current range for that axis
    and each one is validated against the registry's allowed range before use —
    an out-of-range value raises AxisRangeError instead of being clamped.
    """
    axis_enum = TasteAxis(axis) if not isinstance(axis, TasteAxis) else axis
    spec = spec_for(axis_enum)
    root = repo_root or REPO_ROOT

    profile_range = profile.range_for(axis_enum)
    spec.validate_range(profile_range, context=f"StyleProfile v{profile.version}")
    values = [
        spec.validate_value(v, context=f"variant sample for {axis_enum.value}")
        for v in profile_range.sample(n_variants)
    ]

    base_edl = EDL.model_validate_json(
        media_set.base_edl_path(root).read_text(encoding="utf-8")
    )
    candidates = CandidatesFile.model_validate_json(
        media_set.candidates_path(root).read_text(encoding="utf-8")
    )
    beat_times, tempo = _load_beat_grid(media_set, root)
    if beat_times:
        latest = max((c.end_sec for c in candidates.candidates), default=0.0)
        beat_times = extend_beat_grid(beat_times, tempo, latest)

    rid = round_id or uuid.uuid4().hex
    created = datetime.now(UTC).isoformat(timespec="seconds")

    timelines: list[EditTimeline] = []
    for index, value in enumerate(values):
        variant_edl = apply_axis(
            base_edl, axis_enum, value, candidates=candidates, beat_times=beat_times
        )
        assert_timing_invariants(
            variant_edl,
            candidates,
            beat_times,
            context=f"{axis_enum.value}={value}, variant {index}",
        )
        timelines.append(
            EditTimeline(
                timeline_id=f"{rid}-{index}",
                round_id=rid,
                media_set_id=media_set.media_set_id,
                project=media_set.project,
                axis=axis_enum,
                axis_value=value,
                variant_index=index,
                edl=variant_edl,
                created_at=created,
            )
        )
    return timelines
