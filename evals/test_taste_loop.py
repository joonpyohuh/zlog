"""Tests for the taste loop (variant generation, feedback resolution, narrowing).

These lock down the properties the whole loop depends on:
- exactly one axis differs between variants in a round
- an out-of-range value raises instead of being quietly clamped
- cut edges stay inside their source segment and on the beat grid
- feedback columns are derived from the timeline, never invented
- an axis under the sample threshold is left alone
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.edl import (
    EDL,
    Aesthetic,
    Audio,
    CandidateScene,
    CandidatesFile,
    Canvas,
    Caption,
    Frame,
    Quality,
    Signature,
    TimelineClip,
)
from pipeline.taste_loop.axes import (
    REGISTRY,
    AxisRange,
    AxisRangeError,
    TasteAxis,
    spec_for,
)
from pipeline.taste_loop.feedback import build_feedback, resolve_position
from pipeline.taste_loop.media_sets import (
    MediaSet,
    MediaSetRegistry,
    next_media_set_for_round,
)
from pipeline.taste_loop.store import ComparisonRound, LocalTasteStore, RenderedVariant
from pipeline.taste_loop.style_profile import (
    default_profile,
    load_latest,
    save_new_version,
)
from pipeline.taste_loop.update_profile import compute_outcomes, narrow_range
from pipeline.taste_loop.variants import (
    EditTimeline,
    apply_axis,
    assert_timing_invariants,
)

BEATS = [round(i * 0.5, 3) for i in range(200)]


def _candidates() -> CandidatesFile:
    return CandidatesFile(
        project="t",
        candidates=[
            CandidateScene(
                segment_id=f"raw#s{i:03d}",
                source_file="raw.mp4",
                start_sec=i * 10.0,
                end_sec=i * 10.0 + 8.0,
                duration=8.0,
                frame_path=f"frames/raw#s{i:03d}.jpg",
                quality=Quality(blur_score=100.0 - i, brightness=120.0, phash="0" * 16, verdict="pass"),
            )
            for i in range(6)
        ],
    )


def _edl(n_clips: int = 6) -> EDL:
    candidates = _candidates().candidates[:n_clips]
    return EDL(
        project="t",
        version="1.0",
        generator="baseline",
        canvas=Canvas(width=1080, height=1920),
        frame=Frame(aspect="9:16", width=1080, height=1920, y_offset=0),
        aesthetic=Aesthetic(lut="", grain=0.0, bloom=0.0),
        audio=Audio(bgm_id="demo", start_sec=0.0, volume=0.8),
        timeline=[
            TimelineClip(
                order=i + 1,
                segment_id=c.segment_id,
                source_file=c.source_file,
                in_sec=c.start_sec + 1.0,
                out_sec=c.start_sec + 3.0,
                motion="ken_burns_in",
                motion_strength=0.35,
            )
            for i, c in enumerate(candidates)
        ],
        captions=[
            Caption(
                segment_id=candidates[0].segment_id,
                text="첫 컷",
                start_offset_sec=0.0,
                end_offset_sec=1.0,
                grounding="test",
            )
        ],
        signature=Signature(enabled=True, text="directed by zlog", duration=2.0),
    )


# --- axis registry -------------------------------------------------------


def test_out_of_range_value_raises_not_clamped():
    spec = spec_for(TasteAxis.push_in_strength)
    with pytest.raises(AxisRangeError):
        spec.validate_value(0.95)  # allowed high is 0.8
    with pytest.raises(AxisRangeError):
        spec.validate_value(-0.1)


def test_apply_axis_rejects_out_of_range():
    with pytest.raises(AxisRangeError):
        apply_axis(
            _edl(), TasteAxis.avg_cut_duration, 99.0, candidates=_candidates(), beat_times=BEATS
        )


def test_sample_spans_range_inclusive():
    values = AxisRange(low=0.4, high=4.0).sample(4)
    assert values[0] == 0.4
    assert values[-1] == 4.0
    assert values == sorted(values)


# --- variant generation --------------------------------------------------


@pytest.mark.parametrize("axis", list(REGISTRY))
def test_only_one_axis_differs(axis: TasteAxis):
    """Every field the *other* axes own must be identical across variants."""
    base = _edl()
    candidates = _candidates()
    spec = spec_for(axis)
    variants = [
        apply_axis(base, axis, v, candidates=candidates, beat_times=BEATS)
        for v in spec.allowed.sample(4)
    ]

    def fingerprint(edl: EDL) -> dict:
        return {
            "segments": [c.segment_id for c in edl.timeline],
            "order": [c.order for c in edl.timeline],
            "cuts": None
            if axis is TasteAxis.avg_cut_duration
            else [(c.in_sec, c.out_sec) for c in edl.timeline],
            "captions": None
            if axis is TasteAxis.caption_frequency
            else [(c.segment_id, c.text) for c in edl.captions],
            "motion": None
            if axis is TasteAxis.push_in_strength
            else [(c.motion, c.motion_strength) for c in edl.timeline],
            "transitions": None
            if axis is TasteAxis.non_hard_cut_ratio
            else [c.transition for c in edl.timeline],
        }

    first = fingerprint(variants[0])
    for variant in variants[1:]:
        assert fingerprint(variant) == first, f"{axis.value} leaked into another axis"


def test_avg_cut_duration_changes_total_and_stays_in_segment():
    base = _edl()
    candidates = _candidates()
    by_id = {c.segment_id: c for c in candidates.candidates}

    short = apply_axis(base, TasteAxis.avg_cut_duration, 0.5, candidates=candidates, beat_times=BEATS)
    long = apply_axis(base, TasteAxis.avg_cut_duration, 4.0, candidates=candidates, beat_times=BEATS)

    short_total = sum(c.out_sec - c.in_sec for c in short.timeline)
    long_total = sum(c.out_sec - c.in_sec for c in long.timeline)
    assert long_total > short_total

    for edl in (short, long):
        for clip in edl.timeline:
            source = by_id[clip.segment_id]
            assert source.start_sec <= clip.in_sec <= source.end_sec
            assert source.start_sec <= clip.out_sec <= source.end_sec
            assert clip.out_sec > clip.in_sec


@pytest.mark.parametrize("axis", list(REGISTRY))
def test_every_axis_keeps_timing_invariants(axis: TasteAxis):
    """No axis may push a cut out of its segment or off the beat grid."""
    candidates = _candidates()
    for value in spec_for(axis).allowed.sample(4):
        variant = apply_axis(_edl(), axis, value, candidates=candidates, beat_times=BEATS)
        assert_timing_invariants(variant, candidates, BEATS, context=f"{axis.value}={value}")


def test_timing_invariant_check_catches_an_out_of_segment_cut():
    """The guard must actually fail on a bad EDL, not just pass on good ones."""
    candidates = _candidates()
    broken = _edl()
    broken.timeline[0].out_sec = 9999.0
    with pytest.raises(ValueError, match="outside"):
        assert_timing_invariants(broken, candidates, BEATS)


def test_timing_invariant_check_catches_an_off_beat_cut():
    candidates = _candidates()
    broken = _edl()
    # Well above _place_cut's min-duration floor, so the floor exemption
    # cannot mask the off-grid edge.
    broken.timeline[0].in_sec = 1.17  # far off the 0.5s grid
    broken.timeline[0].out_sec = 4.0
    with pytest.raises(ValueError, match="off the beat grid"):
        assert_timing_invariants(broken, candidates, BEATS)


def test_short_cuts_may_leave_the_grid_when_place_cut_hits_its_floor():
    """_place_cut trades the grid for a usable duration; the guard allows that."""
    candidates = _candidates()
    edl = _edl()
    # A sub-floor cut sitting off the grid — exactly what _place_cut emits
    # when a beat-snapped cut would collapse.
    edl.timeline[0].in_sec = 1.17
    edl.timeline[0].out_sec = 1.57  # 0.4s == the floor
    assert_timing_invariants(edl, candidates, BEATS)


def test_avg_cut_duration_stays_snapped_to_beats():
    result = apply_axis(
        _edl(), TasteAxis.avg_cut_duration, 2.0, candidates=_candidates(), beat_times=BEATS
    )
    for clip in result.timeline:
        for edge in (clip.in_sec, clip.out_sec):
            assert min(abs(edge - b) for b in BEATS) <= 0.04


def test_caption_frequency_scales_and_is_not_truncated_by_renderer_cap():
    base = _edl(6)
    none = apply_axis(base, TasteAxis.caption_frequency, 0.0, candidates=_candidates(), beat_times=BEATS)
    all_ = apply_axis(base, TasteAxis.caption_frequency, 1.0, candidates=_candidates(), beat_times=BEATS)

    assert none.captions == []
    assert len(all_.captions) == len(base.timeline)
    # max_captions must rise with the count or the renderer would silently
    # drop captions the logged axis value claims are there.
    assert all_.max_captions >= len(all_.captions)


def test_caption_offsets_fit_their_clip():
    result = apply_axis(_edl(), TasteAxis.caption_frequency, 1.0, candidates=_candidates(), beat_times=BEATS)
    by_segment = {c.segment_id: c for c in result.timeline}
    for caption in result.captions:
        clip = by_segment[caption.segment_id]
        duration = clip.out_sec - clip.in_sec
        assert 0 <= caption.start_offset_sec < duration
        assert caption.end_offset_sec is not None
        assert caption.start_offset_sec < caption.end_offset_sec <= duration + 1e-6


def test_push_in_strength_leaves_static_clips_still():
    base = _edl()
    base.timeline[0].motion = "static"
    result = apply_axis(base, TasteAxis.push_in_strength, 0.8, candidates=_candidates(), beat_times=BEATS)
    assert result.timeline[0].motion == "static"
    assert result.timeline[1].motion_strength == 0.8


def test_non_hard_cut_ratio_never_flashes_the_first_clip():
    for value in (0.0, 0.2, 0.4, 0.6):
        result = apply_axis(
            _edl(), TasteAxis.non_hard_cut_ratio, value, candidates=_candidates(), beat_times=BEATS
        )
        assert result.timeline[0].transition == "cut"
    zero = apply_axis(_edl(), TasteAxis.non_hard_cut_ratio, 0.0, candidates=_candidates(), beat_times=BEATS)
    high = apply_axis(_edl(), TasteAxis.non_hard_cut_ratio, 0.6, candidates=_candidates(), beat_times=BEATS)
    assert all(c.transition == "cut" for c in zero.timeline)
    assert any(c.transition == "flash" for c in high.timeline)
    # The renderer gates flashes behind allow_flash; the axis must flip it.
    assert high.aesthetic.allow_flash is True
    assert zero.aesthetic.allow_flash is False


# --- feedback resolution -------------------------------------------------


def _timeline(edl: EDL) -> EditTimeline:
    return EditTimeline(
        timeline_id="tl-1",
        round_id="r-1",
        media_set_id="ms-1",
        project="t",
        axis=TasteAxis.push_in_strength,
        axis_value=0.4,
        variant_index=0,
        edl=edl,
        created_at="2026-08-06T00:00:00+00:00",
    )


def test_resolve_position_finds_the_playing_clip():
    edl = _edl(3)  # each clip is 2.0s -> [0,2), [2,4), [4,6)
    assert resolve_position(edl, 0.5).clip_id == edl.timeline[0].segment_id
    assert resolve_position(edl, 2.5).clip_id == edl.timeline[1].segment_id
    assert resolve_position(edl, 5.9).clip_id == edl.timeline[2].segment_id


def test_resolve_position_past_the_end_is_null_not_guessed():
    position = resolve_position(_edl(3), 99.0)
    assert position.clip_id is None
    assert position.shot_type is None
    assert position.active_presets == []
    assert position.active_params == {}


def test_feedback_fills_presets_and_params_from_the_timeline():
    edl = apply_axis(_edl(4), TasteAxis.non_hard_cut_ratio, 0.6, candidates=_candidates(), beat_times=BEATS)
    row = build_feedback(_timeline(edl), timestamp_sec=0.5, verdict="good", comment=" 좋다 ")

    assert row.verdict == "good"
    assert row.comment == "좋다"
    assert row.clip_id == edl.timeline[0].segment_id
    # The human supplied only a moment and a verdict; these came from the EDL.
    assert any(p.startswith("motion:") for p in row.active_presets)
    assert row.active_params["motion_strength"] == edl.timeline[0].motion_strength
    assert row.active_params["transition"] == edl.timeline[0].transition


def test_caption_active_matches_the_renderer_gate():
    edl = _edl(3)
    # Base EDL's single caption covers clip 0 for its first second.
    assert resolve_position(edl, 0.5).caption_active is True
    assert resolve_position(edl, 2.5).caption_active is False

    # An ungrounded caption is invisible to the renderer, so it must not
    # count as active here either.
    edl.captions[0].grounding = ""
    assert resolve_position(edl, 0.5).caption_active is False


def test_empty_comment_is_stored_as_null():
    assert build_feedback(_timeline(_edl()), timestamp_sec=0.1, verdict="bad", comment="   ").comment is None


# --- narrowing -----------------------------------------------------------


def _round(axis: TasteAxis, value: float, index: int, *, rejected: bool = False) -> ComparisonRound:
    timeline = EditTimeline(
        timeline_id=f"tl-{index}",
        round_id=f"r-{index}",
        media_set_id="ms-1",
        project="t",
        axis=axis,
        axis_value=value,
        variant_index=0,
        edl=_edl(2),
        created_at="2026-08-06T00:00:00+00:00",
    )
    return ComparisonRound(
        id=f"r-{index}",
        media_set_id="ms-1",
        axis=axis,
        candidates=[timeline],
        winner_id=None if rejected else timeline.timeline_id,
        resolved=True,
        rejected_all=rejected,
        created_at="2026-08-06T00:00:00+00:00",
    )


def test_axis_under_threshold_is_left_alone():
    profile = default_profile()
    rounds = [_round(TasteAxis.avg_cut_duration, 1.5, i) for i in range(4)]
    outcomes = {o.axis: o for o in compute_outcomes(profile, rounds)}
    assert outcomes[TasteAxis.avg_cut_duration].after is None
    assert "데이터 부족" in outcomes[TasteAxis.avg_cut_duration].reason


def test_axis_at_threshold_narrows():
    profile = default_profile()
    rounds = [_round(TasteAxis.avg_cut_duration, v, i) for i, v in enumerate([1.4, 1.5, 1.6, 1.5, 1.4])]
    outcome = {o.axis: o for o in compute_outcomes(profile, rounds)}[TasteAxis.avg_cut_duration]
    assert outcome.after is not None
    assert outcome.after.span < outcome.before.span
    assert outcome.after.low >= outcome.before.low
    assert outcome.after.high <= outcome.before.high


def test_rejected_all_rounds_are_not_votes():
    profile = default_profile()
    rounds = [_round(TasteAxis.push_in_strength, 0.8, i, rejected=True) for i in range(8)]
    outcome = {o.axis: o for o in compute_outcomes(profile, rounds)}[TasteAxis.push_in_strength]
    assert outcome.sample_size == 0
    assert outcome.rejected_all_count == 8
    assert outcome.after is None


def test_narrowing_respects_min_span_and_registry_bounds():
    spec = spec_for(TasteAxis.push_in_strength)
    # Identical winners would collapse the range to a point without min_span.
    narrowed = narrow_range(TasteAxis.push_in_strength, spec.allowed, [0.4] * 6)
    assert narrowed.span >= spec.min_span - 1e-9
    assert spec.allowed.contains(narrowed.low)
    assert spec.allowed.contains(narrowed.high)


def test_narrowing_never_widens():
    current = AxisRange(low=0.3, high=0.5)
    narrowed = narrow_range(TasteAxis.caption_frequency, current, [0.05, 0.9, 0.4, 0.4, 0.4])
    assert narrowed.low >= current.low
    assert narrowed.high <= current.high


# --- versioning / registry -----------------------------------------------


def test_new_profile_version_keeps_the_old_file(tmp_path: Path):
    profile = load_latest(tmp_path)
    assert profile.version == 1
    ranges = dict(profile.ranges)
    ranges[TasteAxis.push_in_strength] = AxisRange(low=0.2, high=0.4)
    new_profile, path = save_new_version(profile, ranges, [], profile_dir=tmp_path)

    assert new_profile.version == 2
    assert path.exists()
    assert (tmp_path / "v1.json").exists(), "previous version must survive"
    assert new_profile.narrowed_from_version == 1


def test_media_set_rotation_prefers_the_least_used():
    registry = MediaSetRegistry(
        sets=[
            MediaSet(media_set_id=f"ms{i}", project=f"p{i}", footage_dir="", work_dir="", bgm_track="b")
            for i in range(3)
        ]
    )
    assert next_media_set_for_round(registry, ["ms0", "ms0", "ms1"]).media_set_id == "ms2"
    assert next_media_set_for_round(registry, ["ms0", "ms1", "ms2"]).media_set_id == "ms0"


def test_local_store_round_trip_and_pick_validation(tmp_path: Path):
    store = LocalTasteStore(tmp_path)
    round_ = _round(TasteAxis.avg_cut_duration, 1.2, 0)
    round_.winner_id = None
    round_.resolved = False
    store.save_round(round_)

    assert store.get_round(round_.id) is not None
    picked = store.record_pick(round_.id, "tl-0")
    assert picked.resolved and picked.winner_id == "tl-0"

    with pytest.raises(ValueError):
        store.record_pick(round_.id, "not-a-candidate")


def test_round_json_is_loadable_by_the_web_layer(tmp_path: Path):
    """The UI reads these files directly; the shape must stay plain JSON."""
    store = LocalTasteStore(tmp_path)
    store.save_round(_round(TasteAxis.caption_frequency, 0.5, 0))
    raw = json.loads((tmp_path / "rounds" / "r-0.json").read_text(encoding="utf-8"))
    assert raw["axis"] == "caption_frequency"
    assert raw["candidates"][0]["axis_value"] == 0.5
    assert isinstance(raw["candidates"][0]["edl"]["timeline"], list)


def test_failed_render_is_recorded_not_dropped(tmp_path: Path):
    """A round with a broken variant stays reviewable, with the failure visible."""
    store = LocalTasteStore(tmp_path)
    round_ = _round(TasteAxis.avg_cut_duration, 1.2, 0)
    round_.renders = [
        RenderedVariant(
            timeline_id="tl-0",
            variant_index=0,
            axis=TasteAxis.avg_cut_duration,
            axis_value=1.2,
            video_path=None,
            ok=False,
            error="delayRender timeout",
            attempts=3,
        )
    ]
    store.save_round(round_)

    reloaded = store.get_round(round_.id)
    assert reloaded is not None
    assert reloaded.render_for("tl-0") is not None
    assert reloaded.render_for("tl-0").ok is False
    assert "delayRender" in reloaded.render_for("tl-0").error


def test_rerender_replaces_only_the_failed_records(tmp_path: Path):
    """rerender must not disturb variants that already rendered fine."""
    store = LocalTasteStore(tmp_path)
    round_ = _round(TasteAxis.avg_cut_duration, 1.2, 0)
    good = RenderedVariant(
        timeline_id="tl-good",
        variant_index=0,
        axis=TasteAxis.avg_cut_duration,
        axis_value=1.2,
        video_path="work/x/good.mp4",
        ok=True,
    )
    bad = RenderedVariant(
        timeline_id="tl-0",
        variant_index=1,
        axis=TasteAxis.avg_cut_duration,
        axis_value=1.2,
        video_path=None,
        ok=False,
        error="boom",
    )
    round_.renders = [good, bad]
    store.save_round(round_)

    # Mirrors the merge in cli.rerender_cmd.
    fresh = [
        RenderedVariant(
            timeline_id="tl-0",
            variant_index=1,
            axis=TasteAxis.avg_cut_duration,
            axis_value=1.2,
            video_path="work/x/fixed.mp4",
            ok=True,
            attempts=2,
        )
    ]
    by_id = {r.timeline_id: r for r in fresh}
    round_.renders = [by_id.get(r.timeline_id, r) for r in round_.renders]
    store.save_round(round_)

    reloaded = store.get_round(round_.id)
    assert reloaded is not None
    assert reloaded.render_for("tl-good").video_path == "work/x/good.mp4"
    assert reloaded.render_for("tl-0").ok is True
    assert reloaded.render_for("tl-0").video_path == "work/x/fixed.mp4"
