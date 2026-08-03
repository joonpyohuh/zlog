"""EDL (Edit Decision List) schema + validation.

This module owns the data contracts that every other pipeline stage reads
or writes. No video/audio processing happens here.

Reads:  work/<project>/segments.json, work/<project>/segments_filtered.json,
        work/<project>/edl.json (for validate())
Writes: nothing on its own — save_edl() is called by select_baseline.py /
        select_ai.py.

Rule: an EDLClip's in_tc/out_tc must come from an existing Scene's
timecodes (or a beat-grid-snapped subset of them). Nothing in this module
invents a timestamp.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel


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


class FilteredScene(Scene):
    """A Scene annotated by filter.py with quality-filter results."""

    sharpness_score: float | None = None
    exposure_score: float | None = None
    is_duplicate: bool = False
    keep: bool = True
    discard_reason: str | None = None


class EDLClip(BaseModel):
    """One chosen segment in the final cut, snapped to the beat grid."""

    segment_id: str  # must reference a segment_id from segments_filtered.json
    source_file: str
    in_tc: float
    out_tc: float
    order: int
    beat_snap_in: float | None = None
    beat_snap_out: float | None = None
    transition: Literal["cut"] = "cut"


class EDL(BaseModel):
    """The full edit decision list for one project."""

    project: str
    bgm_track: str
    target_duration_s: float
    created_by: Literal["baseline", "ai"]
    clips: list[EDLClip] = []


def load_segments(path: Path) -> list[FilteredScene]:
    """Read segments.json / segments_filtered.json into FilteredScene objects."""
    raise NotImplementedError


def save_edl(edl: EDL, path: Path) -> None:
    """Write an EDL to work/<project>/edl.json."""
    raise NotImplementedError


def load_edl(path: Path) -> EDL:
    """Read work/<project>/edl.json back into an EDL object."""
    raise NotImplementedError


def validate_edl(edl: EDL, scenes: list[FilteredScene]) -> list[str]:
    """Return a list of validation problems (empty = valid).

    TODO:
    - every clip.segment_id exists in `scenes` and is kept (keep=True)
    - clip.in_tc/out_tc fall within that scene's [start_sec, end_sec]
    - clip.in_tc/out_tc equal a beat-grid timestamp when beat snapping is
      required (see beats.py)
    - clips are contiguous/ordered by `order` with no timecode overlap
    - sum of clip durations falls within [15, 60] seconds
    """
    raise NotImplementedError
