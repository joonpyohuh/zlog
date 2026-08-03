"""EDL (Edit Decision List) schema + validation.

This module owns the data contracts that every other pipeline stage reads
or writes. No video/audio processing happens here.

Reads:  work/<project>/segments.json, work/<project>/candidates.json,
        work/<project>/edl_baseline.json / edl_ai.json (for validate_edl())
Writes: nothing on its own — save_edl() is called by select_baseline.py /
        select_ai.py.

Rule: an EDL timeline clip's in_sec/out_sec must come from an existing
candidate's timecodes (a beat-grid-snapped subset of them). Nothing in
this module invents a timestamp.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

BEAT_SNAP_TOLERANCE_S = 0.04
DURATION_TOLERANCE_S = 1.0


class Scene(BaseModel):
    """One PySceneDetect-derived segment, as written by split.py."""

    segment_id: str
    source_file: str
    start_sec: float
    end_sec: float
    duration: float
    frame_path: str


class SegmentsFile(BaseModel):
    """The full contents of work/<project>/segments.json."""

    project: str
    segments: list[Scene] = []


class Quality(BaseModel):
    """filter.py's verdict on one segment's representative frame."""

    blur_score: float
    brightness: float
    phash: str
    verdict: Literal["pass", "blurry", "underexposed", "overexposed", "duplicate"]
    duplicate_of: str | None = None


class Tags(BaseModel):
    """tag.py's structured read on one candidate frame."""

    shot: Literal["wide", "medium", "close"]
    subject: Literal["person", "food", "landscape", "object", "text"]
    face_visible: bool
    mood: Literal["bright", "calm", "lively", "moody"]
    keep_score: int  # 1-5


class CandidateScene(Scene):
    """A Scene annotated by filter.py with a quality verdict, and — once
    tag.py has run — by structured tags."""

    quality: Quality
    tags: Tags | None = None


class CandidatesFile(BaseModel):
    """The full contents of work/<project>/candidates.json.

    `candidates` holds verdict == "pass" segments only; `rejected` holds
    everything filter.py discarded, kept around so thresholds can be
    tuned by inspecting real rejections instead of guessing blind.
    """

    project: str
    candidates: list[CandidateScene] = []
    rejected: list[CandidateScene] = []


class Canvas(BaseModel):
    width: int
    height: int


class Frame(BaseModel):
    aspect: str
    width: int
    height: int
    y_offset: int


class Aesthetic(BaseModel):
    lut: str
    grain: float
    bloom: float


class Audio(BaseModel):
    bgm_id: str
    start_sec: float
    volume: float


class TimelineClip(BaseModel):
    """One chosen segment in the final cut, snapped to the beat grid.

    `transition` describes how this clip *enters*: "cut" is a hard cut,
    "flash" is a 3-frame white flash burned in by the renderer at section
    boundaries (intro->main->outro). Purely cosmetic — timing still comes
    from the beat grid.
    """

    order: int
    segment_id: str  # must reference a segment_id from candidates.json's "candidates"
    source_file: str
    in_sec: float
    out_sec: float
    transition: Literal["cut", "flash"] = "cut"


class Caption(BaseModel):
    """On-screen text tied to a timeline clip — never absolute wall-clock.

    Timing is relative to the *played* window of the referenced clip
    (duration = out_sec - in_sec). LLM/selectors only choose text + which
    segment_id to attach to; they do not invent absolute timestamps.
    """

    segment_id: str  # must reference a clip already on edl.timeline
    text: str
    style: Literal["title", "subtitle", "lower_third"] = "subtitle"
    font: Literal["body", "display"] = "body"
    position: Literal["top", "center", "bottom"] = "bottom"
    start_offset_sec: float = 0.0
    end_offset_sec: float | None = None  # None = until end of that clip


class Signature(BaseModel):
    enabled: bool
    text: str
    duration: float


class EDL(BaseModel):
    """The full edit decision list for one project."""

    project: str
    version: str
    generator: Literal["baseline", "ai"]
    canvas: Canvas
    frame: Frame
    aesthetic: Aesthetic
    audio: Audio
    timeline: list[TimelineClip] = []
    captions: list[Caption] = []
    signature: Signature


def load_segments(path: Path) -> SegmentsFile:
    """Read segments.json into a SegmentsFile."""
    return SegmentsFile.model_validate_json(path.read_text(encoding="utf-8"))


def load_candidates(path: Path) -> CandidatesFile:
    """Read candidates.json into a CandidatesFile."""
    return CandidatesFile.model_validate_json(path.read_text(encoding="utf-8"))


def save_edl(edl: EDL, path: Path) -> None:
    """Write an EDL to work/<project>/edl_baseline.json or edl_ai.json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(edl.model_dump_json(indent=2), encoding="utf-8")


def load_edl(path: Path) -> EDL:
    """Read an edl_baseline.json / edl_ai.json back into an EDL object."""
    return EDL.model_validate_json(path.read_text(encoding="utf-8"))


def validate_edl(
    edl: EDL,
    candidates: CandidatesFile,
    beat_times: list[float],
    target_duration_s: float,
    beat_tolerance_s: float = BEAT_SNAP_TOLERANCE_S,
    duration_tolerance_s: float = DURATION_TOLERANCE_S,
) -> list[str]:
    """Return a list of validation problems (empty = valid).

    Checks:
    - every clip.segment_id exists in candidates.candidates (the "pass" list)
    - clip.in_sec/out_sec fall within that candidate's [start_sec, end_sec]
    - clip.in_sec/out_sec each land within beat_tolerance_s of some beat
      in beat_times
    - sum of clip durations falls within target_duration_s +/- duration_tolerance_s
    - every caption.segment_id is on the timeline, offsets fit that clip's
      played duration, and text is non-empty
    """
    problems: list[str] = []
    by_id = {c.segment_id: c for c in candidates.candidates}
    rejected_ids = {c.segment_id: c.quality.verdict for c in candidates.rejected}

    for clip in edl.timeline:
        candidate = by_id.get(clip.segment_id)
        if candidate is None:
            if clip.segment_id in rejected_ids:
                problems.append(
                    f"order={clip.order} segment_id={clip.segment_id!r} was rejected "
                    f"by filter.py (verdict={rejected_ids[clip.segment_id]!r}), not a valid candidate"
                )
            else:
                problems.append(
                    f"order={clip.order} segment_id={clip.segment_id!r} does not exist "
                    f"in candidates.json"
                )
            continue

        if not (candidate.start_sec <= clip.in_sec <= candidate.end_sec):
            problems.append(
                f"order={clip.order} segment_id={clip.segment_id!r} in_sec={clip.in_sec} "
                f"is outside source range [{candidate.start_sec}, {candidate.end_sec}]"
            )
        if not (candidate.start_sec <= clip.out_sec <= candidate.end_sec):
            problems.append(
                f"order={clip.order} segment_id={clip.segment_id!r} out_sec={clip.out_sec} "
                f"is outside source range [{candidate.start_sec}, {candidate.end_sec}]"
            )

        if beat_times:
            for label, t in (("in_sec", clip.in_sec), ("out_sec", clip.out_sec)):
                nearest = min(beat_times, key=lambda b: abs(b - t))
                delta = abs(nearest - t)
                if delta > beat_tolerance_s:
                    problems.append(
                        f"order={clip.order} segment_id={clip.segment_id!r} {label}={t} is "
                        f"{delta * 1000:.1f}ms from the nearest beat ({nearest}), "
                        f"exceeds {beat_tolerance_s * 1000:.0f}ms tolerance"
                    )

    total_duration = sum(clip.out_sec - clip.in_sec for clip in edl.timeline)
    if abs(total_duration - target_duration_s) > duration_tolerance_s:
        problems.append(
            f"total timeline duration {total_duration:.3f}s is more than "
            f"{duration_tolerance_s}s away from target {target_duration_s}s"
        )

    # First occurrence wins — expand_timeline_to_target may reuse the same
    # segment_id with shorter later cuts; captions are authored against the
    # first host clip (see pipeline/captions.py).
    timeline_by_id: dict[str, TimelineClip] = {}
    for clip in edl.timeline:
        timeline_by_id.setdefault(clip.segment_id, clip)
    for i, cap in enumerate(edl.captions):
        clip = timeline_by_id.get(cap.segment_id)
        if clip is None:
            problems.append(
                f"captions[{i}] segment_id={cap.segment_id!r} is not on the timeline"
            )
            continue
        clip_dur = clip.out_sec - clip.in_sec
        if not cap.text.strip():
            problems.append(f"captions[{i}] segment_id={cap.segment_id!r} has empty text")
        if cap.start_offset_sec < 0 or cap.start_offset_sec >= clip_dur:
            problems.append(
                f"captions[{i}] segment_id={cap.segment_id!r} "
                f"start_offset_sec={cap.start_offset_sec} outside clip duration {clip_dur:.3f}s"
            )
        end = clip_dur if cap.end_offset_sec is None else cap.end_offset_sec
        if end <= cap.start_offset_sec or end > clip_dur + 1e-6:
            problems.append(
                f"captions[{i}] segment_id={cap.segment_id!r} "
                f"end_offset_sec={cap.end_offset_sec} invalid for start={cap.start_offset_sec} "
                f"clip_duration={clip_dur:.3f}s"
            )

    return problems
