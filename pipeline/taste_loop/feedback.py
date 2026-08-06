"""Resolve "what was on screen at t?" from an EditTimeline.

The point of the section-review feature: the person marks a moment and says
good or bad. They never type a number. The system already knows which clip was
playing and which effects were on it, so code reads that back out of the
timeline JSON.

Anything that cannot be read stays None. There is no inference here — a
guessed shot_type would poison the very dataset this exists to collect.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from pipeline.edl import EDL, TimelineClip
from pipeline.taste_loop.store import ClipFeedback, Verdict
from pipeline.taste_loop.variants import EditTimeline

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TimelinePosition(BaseModel):
    """Where a wall-clock offset into the rendered video lands."""

    clip_id: str | None
    clip_order: int | None
    shot_type: str | None
    active_presets: list[str]
    active_params: dict[str, Any]
    caption_active: bool
    clip_local_offset_sec: float | None


def _clip_windows(edl: EDL) -> list[tuple[float, float, TimelineClip]]:
    """[(start, end, clip), ...] in playback time, from clip durations alone."""
    windows: list[tuple[float, float, TimelineClip]] = []
    cursor = 0.0
    for clip in edl.timeline:
        duration = max(0.0, clip.out_sec - clip.in_sec)
        windows.append((cursor, cursor + duration, clip))
        cursor += duration
    return windows


def _caption_active_at(edl: EDL, clip: TimelineClip, local_offset: float) -> bool:
    """Was a *renderable* caption on screen for this clip at that offset?

    Mirrors the renderer's own gate (grounding and text both non-empty, capped
    at max_captions) so the recorded flag matches what was actually visible
    rather than what the EDL merely lists.
    """
    renderable = [
        c
        for c in edl.captions
        if (c.grounding or "").strip() and (c.text or "").strip()
    ]
    cap = max(0, int(edl.max_captions))
    visible = renderable[:cap]

    clip_duration = max(0.0, clip.out_sec - clip.in_sec)
    for caption in visible:
        if caption.segment_id != clip.segment_id:
            continue
        start = caption.start_offset_sec
        end = clip_duration if caption.end_offset_sec is None else caption.end_offset_sec
        if start <= local_offset <= end:
            return True
    return False


def resolve_position(edl: EDL, timestamp_sec: float) -> TimelinePosition:
    """Read back the render state at `timestamp_sec` of the finished video."""
    if timestamp_sec < 0:
        raise ValueError(f"timestamp_sec must be >= 0, got {timestamp_sec}")

    windows = _clip_windows(edl)
    match: tuple[float, float, TimelineClip] | None = None
    for start, end, clip in windows:
        if start <= timestamp_sec < end:
            match = (start, end, clip)
            break
    # A mark past the last clip lands on the ending credit / signature, where
    # no clip is playing. That is a real position, and it stays unresolved
    # rather than being snapped onto the nearest clip.
    if match is None:
        return TimelinePosition(
            clip_id=None,
            clip_order=None,
            shot_type=None,
            active_presets=[],
            active_params={},
            caption_active=False,
            clip_local_offset_sec=None,
        )

    start, _end, clip = match
    local_offset = round(timestamp_sec - start, 3)

    presets: list[str] = [f"style_preset:{edl.style_preset}"]
    if clip.transition and clip.transition != "cut":
        presets.append(f"transition:{clip.transition}")
    if clip.motion and clip.motion not in ("none", "static"):
        presets.append(f"motion:{clip.motion}")
    if clip.fit_mode:
        presets.append(f"fit_mode:{clip.fit_mode}")
    if (edl.aesthetic.lut or "").strip():
        presets.append(f"lut:{edl.aesthetic.lut}")
    if edl.aesthetic.scanlines:
        presets.append("scanlines")
    if edl.aesthetic.camcorder_osd:
        presets.append("camcorder_osd")

    caption_active = _caption_active_at(edl, clip, local_offset)

    params: dict[str, Any] = {
        "clip_order": clip.order,
        "segment_id": clip.segment_id,
        "source_file": clip.source_file,
        "in_sec": clip.in_sec,
        "out_sec": clip.out_sec,
        "clip_duration_sec": round(clip.out_sec - clip.in_sec, 3),
        "transition": clip.transition,
        "motion": clip.motion,
        "motion_strength": clip.motion_strength,
        "fit_mode": clip.fit_mode,
        "focus_x": clip.focus_x,
        "focus_y": clip.focus_y,
        "role": clip.role,
        "grain": edl.aesthetic.grain,
        "bloom": edl.aesthetic.bloom,
        "allow_flash": edl.aesthetic.allow_flash,
        "lut": edl.aesthetic.lut or None,
        "caption_active": caption_active,
    }

    # shot_type is a tag.py/analyze_assets concept, not an EDL field. If the
    # planner did not put a role on the clip there is nothing to report, and
    # None is the correct answer.
    shot_type = clip.role or None

    return TimelinePosition(
        clip_id=clip.segment_id,
        clip_order=clip.order,
        shot_type=shot_type,
        active_presets=presets,
        active_params=params,
        caption_active=caption_active,
        clip_local_offset_sec=local_offset,
    )


def build_feedback(
    timeline: EditTimeline,
    *,
    timestamp_sec: float,
    verdict: Verdict,
    comment: str | None = None,
) -> ClipFeedback:
    """Turn a human mark (moment + good/bad) into a full ClipFeedback row."""
    position = resolve_position(timeline.edl, timestamp_sec)
    text = (comment or "").strip()
    return ClipFeedback(
        id=uuid.uuid4().hex,
        timeline_id=timeline.timeline_id,
        timestamp_sec=round(float(timestamp_sec), 3),
        clip_id=position.clip_id,
        shot_type=position.shot_type,
        active_presets=position.active_presets,
        active_params=position.active_params,
        caption_active=position.caption_active,
        verdict=verdict,
        comment=text or None,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
