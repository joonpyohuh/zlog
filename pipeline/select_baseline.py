"""Heuristic (no-AI) segment selection — the baseline / fallback EDL builder.

Reads:  work/<project>/candidates.json
        assets/bgm/<track>.beats.json
Writes: work/<project>/edl_baseline.json  (EDL, generator="baseline"; see edl.py)

Picks passing candidates by sharpness to fill target_duration_s and snaps
every in/out point to the beat grid. No AI call — this is the baseline
select_ai.py gets compared against later.

Cut rhythm (in beats, at the BGM's own tempo — never hand-timed):
- intro:  INTRO_BEATS_PER_CUT-beat cuts over the first INTRO_DURATION_FRACTION
          of the target duration
- main:   MAIN_BEATS_PER_CUT-beat cuts over the middle
- outro:  OUTRO_BEATS_PER_CUT-beat rapid cuts over the last OUTRO_DURATION_FRACTION
"""

from __future__ import annotations

import json
from pathlib import Path

import click
import numpy as np

from pipeline.captions import build_captions
from pipeline.edl import (
    Aesthetic,
    Audio,
    Canvas,
    CandidatesFile,
    CandidateScene,
    DURATION_TOLERANCE_S,
    EDL,
    Frame,
    Signature,
    TimelineClip,
    validate_edl,
)

INTRO_BEATS_PER_CUT = 4
MAIN_BEATS_PER_CUT = 2
OUTRO_BEATS_PER_CUT = 1
INTRO_DURATION_FRACTION = 0.2
OUTRO_DURATION_FRACTION = 0.2

DEFAULT_CANVAS = Canvas(width=1080, height=1920)
DEFAULT_FRAME = Frame(aspect="4:3", width=1080, height=810, y_offset=555)
DEFAULT_AESTHETIC = Aesthetic(lut="ccd_cool_01.cube", grain=0.15, bloom=0.2)
DEFAULT_SIGNATURE = Signature(enabled=True, text="directed by zlog", duration=2.0)
AUDIO_VOLUME = 0.8

EDL_VERSION = "1.0"


def _beats_path(bgm_track: Path) -> Path:
    return bgm_track.parent / f"{bgm_track.stem}.beats.json"


def _cut_durations(target_duration_s: float, tempo_bpm: float) -> list[float]:
    """Build the ordered list of per-cut durations (seconds) covering
    intro -> main -> outro, per the beats-per-cut rules above.
    """
    beat_sec = 60.0 / tempo_bpm
    intro_cut = INTRO_BEATS_PER_CUT * beat_sec
    main_cut = MAIN_BEATS_PER_CUT * beat_sec
    outro_cut = OUTRO_BEATS_PER_CUT * beat_sec

    intro_target = target_duration_s * INTRO_DURATION_FRACTION
    outro_target = target_duration_s * OUTRO_DURATION_FRACTION
    main_target = target_duration_s - intro_target - outro_target

    n_intro = max(1, round(intro_target / intro_cut))
    n_main = max(1, round(main_target / main_cut))
    n_outro = max(1, round(outro_target / outro_cut))

    return [intro_cut] * n_intro + [main_cut] * n_main + [outro_cut] * n_outro


def _nearest_beat(t: float, beat_times: list[float]) -> float:
    return min(beat_times, key=lambda b: abs(b - t))


def extend_beat_grid(beat_times: list[float], tempo_bpm: float, until_sec: float) -> list[float]:
    """Continue the beat grid at the track's own tempo past the end of the
    BGM so source footage longer than the track can still snap its cut
    points. Purely arithmetic on librosa's grid — no invented rhythm.
    (Without this, any candidate past the last beat fails validation and
    the AI selector hard-fails whenever the model picks a late segment.)
    """
    if not beat_times or tempo_bpm <= 0:
        return beat_times
    beat = 60.0 / tempo_bpm
    times = list(beat_times)
    t = times[-1]
    while t < until_sec + beat:
        t += beat
        times.append(round(t, 6))
    return times


def expand_timeline_to_target(
    timeline: list[TimelineClip],
    pool: list[CandidateScene],
    beat_times: list[float],
    target_duration_s: float,
    tempo_bpm: float,
) -> list[TimelineClip]:
    """If the cut list is short (few uploads), rebuild from the pool with reuse
    so total length approaches target_duration_s. Timing still comes only from
    candidate ranges + beat snap — no invented timestamps.
    """
    if not pool:
        return timeline
    total = sum(c.out_sec - c.in_sec for c in timeline)
    if total >= target_duration_s * 0.85:
        # Close to target already; _place_cut's clamping to short segments can
        # still leave the total outside the ±1s validation window, so top up
        # with beat-length cuts instead of rebuilding the whole cut list.
        return _pad_to_target(timeline, pool, beat_times, target_duration_s, tempo_bpm)

    cut_durations = _cut_durations(target_duration_s, tempo_bpm)
    # Cap cut count when the pool is tiny — many 0.5s reuses thrash Remotion
    # and leave caption sequences too short for fade ramps.
    max_cuts = max(6, min(18, len(pool) * 5))
    if len(cut_durations) > max_cuts:
        each = target_duration_s / max_cuts
        cut_durations = [each] * max_cuts
    by_id = {c.segment_id: c for c in pool}
    preferred = [by_id[c.segment_id] for c in timeline if c.segment_id in by_id]
    ranked = preferred or sorted(pool, key=lambda c: c.quality.blur_score, reverse=True)
    selected = [ranked[i % len(ranked)] for i in range(len(cut_durations))]

    out: list[TimelineClip] = []
    for order, (candidate, cut_duration) in enumerate(zip(selected, cut_durations), start=1):
        in_sec, out_sec = _place_cut(candidate, cut_duration, beat_times)
        out.append(
            TimelineClip(
                order=order,
                segment_id=candidate.segment_id,
                source_file=candidate.source_file,
                in_sec=in_sec,
                out_sec=out_sec,
            )
        )
    return _pad_to_target(out, pool, beat_times, target_duration_s, tempo_bpm)


def _pad_to_target(
    timeline: list[TimelineClip],
    pool: list[CandidateScene],
    beat_times: list[float],
    target_duration_s: float,
    tempo_bpm: float,
) -> list[TimelineClip]:
    """Append 1–2-beat cuts (reusing the sharpest candidates) until the
    total lands inside validate_edl's ±DURATION_TOLERANCE_S window. Reads
    like the outro's rapid-cut montage; timing still comes from candidate
    ranges + beat snap only.
    """
    beat_sec = 60.0 / tempo_bpm
    total = sum(c.out_sec - c.in_sec for c in timeline)
    ranked = sorted(pool, key=lambda c: c.quality.blur_score, reverse=True)
    attempts = 0
    while total < target_duration_s - DURATION_TOLERANCE_S * 0.9 and attempts < len(ranked) * 4:
        candidate = ranked[attempts % len(ranked)]
        attempts += 1
        remaining = target_duration_s - total
        want = max(beat_sec, min(2 * beat_sec, remaining))
        in_sec, out_sec = _place_cut(candidate, want, beat_times)
        duration = out_sec - in_sec
        if duration < 0.05 or total + duration > target_duration_s + DURATION_TOLERANCE_S:
            continue
        timeline.append(
            TimelineClip(
                order=len(timeline) + 1,
                segment_id=candidate.segment_id,
                source_file=candidate.source_file,
                in_sec=in_sec,
                out_sec=out_sec,
            )
        )
        total += duration
    return timeline


def assign_transitions(timeline: list[TimelineClip]) -> list[TimelineClip]:
    """Mark the clips that open the middle and final thirds of the cut with a
    'flash' entry — the renderer burns a short white flash there, giving the
    intro->main->outro sections a visible pulse. Deterministic; shared by
    both selectors so their EDLs stay comparable.
    """
    n = len(timeline)
    if n < 3:
        return timeline
    flash_at = {n // 3, (2 * n) // 3}
    flash_at.discard(0)
    for i, clip in enumerate(timeline):
        clip.transition = "flash" if i in flash_at else "cut"
    return timeline


def _place_cut(candidate: CandidateScene, cut_duration_s: float, beat_times: list[float]) -> tuple[float, float]:
    """Center cut_duration_s on the candidate's segment, clamp to the
    segment's own [start_sec, end_sec] (never invent a wider window than
    the source shot), then snap both edges to the nearest beat.
    """
    seg_start, seg_end = candidate.start_sec, candidate.end_sec
    dur = min(cut_duration_s, seg_end - seg_start)
    mid = (seg_start + seg_end) / 2
    raw_in = mid - dur / 2
    raw_out = mid + dur / 2

    if raw_in < seg_start:
        shift = seg_start - raw_in
        raw_in += shift
        raw_out += shift
    if raw_out > seg_end:
        shift = raw_out - seg_end
        raw_in -= shift
        raw_out -= shift
    raw_in = max(raw_in, seg_start)
    raw_out = min(raw_out, seg_end)

    if not beat_times:
        return round(raw_in, 3), round(raw_out, 3)

    snapped_in = min(max(_nearest_beat(raw_in, beat_times), seg_start), seg_end)
    snapped_out = min(max(_nearest_beat(raw_out, beat_times), seg_start), seg_end)
    if snapped_out <= snapped_in:
        snapped_in, snapped_out = raw_in, raw_out
    # Beat snap can collapse a cut to a few frames — Remotion Sequence with
    # 0 duration crashes, and caption fades need a usable window.
    min_cut = min(0.4, seg_end - seg_start)
    if snapped_out - snapped_in < min_cut:
        mid = (seg_start + seg_end) / 2
        half = min_cut / 2
        snapped_in = max(seg_start, mid - half)
        snapped_out = min(seg_end, snapped_in + min_cut)
        snapped_in = max(seg_start, snapped_out - min_cut)
    return round(snapped_in, 3), round(snapped_out, 3)


def select_baseline(
    work_dir: Path,
    project: str,
    bgm_track: Path,
    target_duration_s: float,
) -> Path:
    """Build a baseline EDL from candidates.json + the beat grid and write
    it to work/<project>/edl_baseline.json.
    """
    project_dir = work_dir / project
    candidates_file = CandidatesFile.model_validate_json(
        (project_dir / "candidates.json").read_text(encoding="utf-8")
    )
    pool = candidates_file.candidates
    if not pool:
        raise ValueError(f"no passing candidates in {project_dir / 'candidates.json'}")

    beats_data = json.loads(_beats_path(bgm_track).read_text(encoding="utf-8"))
    tempo_bpm = beats_data["tempo_bpm"]
    beat_times = extend_beat_grid(
        beats_data["beat_times"], tempo_bpm, max(c.end_sec for c in pool)
    )

    cut_durations = _cut_durations(target_duration_s, tempo_bpm)
    pool_sorted = sorted(pool, key=lambda c: (c.source_file, c.start_sec))
    n_cuts = len(cut_durations)

    if len(pool_sorted) >= n_cuts:
        buckets = np.array_split(np.array(pool_sorted, dtype=object), n_cuts)
        selected = [max(bucket, key=lambda c: c.quality.blur_score) for bucket in buckets]
    else:
        # Few clips (common for photo uploads): reuse sharpest shots to fill target length.
        ranked = sorted(pool_sorted, key=lambda c: c.quality.blur_score, reverse=True)
        selected = [ranked[i % len(ranked)] for i in range(n_cuts)]
        click.echo(
            f"reusing {len(ranked)} candidate(s) across {n_cuts} cuts to approach "
            f"{target_duration_s:.0f}s"
        )

    timeline: list[TimelineClip] = []
    for order, (candidate, cut_duration) in enumerate(zip(selected, cut_durations), start=1):
        in_sec, out_sec = _place_cut(candidate, cut_duration, beat_times)
        timeline.append(
            TimelineClip(
                order=order,
                segment_id=candidate.segment_id,
                source_file=candidate.source_file,
                in_sec=in_sec,
                out_sec=out_sec,
            )
        )
    timeline = expand_timeline_to_target(timeline, pool, beat_times, target_duration_s, tempo_bpm)
    timeline = assign_transitions(timeline)

    by_id = {c.segment_id: c for c in pool}
    note_path = project_dir / "note.txt"
    note = note_path.read_text(encoding="utf-8").strip() if note_path.exists() else None
    edl = EDL(
        project=project,
        version=EDL_VERSION,
        generator="baseline",
        canvas=DEFAULT_CANVAS,
        frame=DEFAULT_FRAME,
        aesthetic=DEFAULT_AESTHETIC,
        audio=Audio(bgm_id=bgm_track.stem, start_sec=0.0, volume=AUDIO_VOLUME),
        timeline=timeline,
        captions=build_captions(timeline, by_id, note=note),
        signature=DEFAULT_SIGNATURE,
    )

    problems = validate_edl(edl, candidates_file, beat_times, target_duration_s)
    if problems:
        click.echo("edl_baseline.json validation problems:")
        for p in problems:
            click.echo(f"  - {p}")
    else:
        click.echo("edl_baseline.json passed validation")

    out_path = project_dir / "edl_baseline.json"
    out_path.write_text(edl.model_dump_json(indent=2), encoding="utf-8")
    return out_path


@click.command()
@click.option("--work-dir", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--project", required=True)
@click.option("--bgm-track", type=click.Path(path_type=Path), required=True)
@click.option("--target-duration-s", type=float, default=15.0)
def main(work_dir: Path, project: str, bgm_track: Path, target_duration_s: float) -> None:
    out = select_baseline(work_dir, project, bgm_track, target_duration_s)
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
