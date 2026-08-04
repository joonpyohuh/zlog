"""PROMPT 3 — AssetAnalysis validation / escalation / deterministic fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from pipeline.ai.schemas import AssetAnalysis, MediaType, Mood, SceneKind, ShotType
from pipeline.analyze_assets import (
    CROP_CONFIDENCE_LOW,
    UNCERTAINTY_ESCALATE,
    SegmentBundle,
    deterministic_fallback_analysis,
    needs_sonnet_escalation,
    validate_analyses,
    write_debug_report,
)
from pipeline.evidence import EvidenceFrame, EvidenceSegment, SegmentFeatures


def _raw(
    segment_id: str = "clip#s001",
    *,
    evidence_frame_ids: list[str] | None = None,
    **over: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "asset_id": f"asset:{segment_id}",
        "segment_id": segment_id,
        "source_file": "clip.mp4",
        "media_type": MediaType.video.value,
        "upload_index": 0,
        "evidence_frame_ids": evidence_frame_ids
        or [f"{segment_id}#f01", f"{segment_id}#f02", f"{segment_id}#f03"],
        "subjects": ["person"],
        "scene": SceneKind.outdoor.value,
        "shot_type": ShotType.medium.value,
        "action_progression": "walks toward camera then stops",
        "mood": Mood.calm.value,
        "technical_quality": 0.7,
        "aesthetic_value": 0.6,
        "emotional_value": 0.5,
        "narrative_value": 0.55,
        "hook_potential": 0.4,
        "motion_quality": 0.3,
        "novelty": 0.5,
        "focus_x": 0.5,
        "focus_y": 0.45,
        "focus_width": 0.5,
        "focus_height": 0.55,
        "crop_confidence": 0.7,
        "visually_grounded_facts": ["person centered"],
        "uncertainty": 0.2,
        "analysis_provider": "anthropic",
        "analysis_model": "claude-haiku-4-5-20251001",
    }
    base.update(over)
    return base


def _bundle(segment_id: str = "clip#s001", n_frames: int = 3) -> SegmentBundle:
    eids = [f"{segment_id}#f{i:02d}" for i in range(1, n_frames + 1)]
    seg = EvidenceSegment(
        segment_id=segment_id,
        source_file="clip.mp4",
        upload_index=0,
        time_order=0,
        start_sec=0.0,
        end_sec=3.0,
        duration=3.0,
        evidence=[
            EvidenceFrame(
                evidence_id=eid,
                frame_path=f"evidence/{eid}.jpg",
                offset_sec=0.5 * i,
                rel_pos=0.2 * i,
                role="default",
            )
            for i, eid in enumerate(eids, start=1)
        ],
    )
    return SegmentBundle(
        segment=seg,
        features=SegmentFeatures(
            segment_id=segment_id,
            duration_sec=3.0,
            audio_rms=0.01,
            onset_density=0.0,
            mean_motion=0.2,
            mean_perceptual_distance=0.05,
            is_static=True,
            is_high_motion=False,
            frames=[],
        ),
        evidence_paths=[Path(f"evidence/{eid}.jpg") for eid in eids],
        expected_evidence_ids=eids,
    )


def test_rejects_unknown_evidence_id():
    allowed_seg = {"clip#s001"}
    allowed_ev = {"clip#s001": {"clip#s001#f01", "clip#s001#f02"}}
    with pytest.raises(ValueError, match="unknown evidence_frame_id"):
        validate_analyses(
            [_raw(evidence_frame_ids=["clip#s001#f01", "clip#s001#f99"])],
            allowed_segment_ids=allowed_seg,
            allowed_evidence_by_segment=allowed_ev,
            analysis_provider="anthropic",
            analysis_model="claude-haiku-4-5-20251001",
        )


def test_rejects_unknown_segment_id():
    with pytest.raises(ValueError, match="unknown segment_id"):
        validate_analyses(
            [_raw("nope#s001")],
            allowed_segment_ids={"clip#s001"},
            allowed_evidence_by_segment={"clip#s001": {"clip#s001#f01"}},
            analysis_provider="anthropic",
            analysis_model="m",
        )


def test_rejects_duplicate_segment_output():
    allowed = {"clip#s001"}
    ev = {"clip#s001": {"clip#s001#f01", "clip#s001#f02", "clip#s001#f03"}}
    with pytest.raises(ValueError, match="duplicate segment_id"):
        validate_analyses(
            [_raw(), _raw()],
            allowed_segment_ids=allowed,
            allowed_evidence_by_segment=ev,
            analysis_provider="anthropic",
            analysis_model="m",
        )


def test_score_range_validation():
    with pytest.raises(ValidationError):
        AssetAnalysis.model_validate(_raw(technical_quality=1.5))
    with pytest.raises(ValidationError):
        AssetAnalysis.model_validate(_raw(focus_x=-0.1))
    with pytest.raises(ValidationError):
        AssetAnalysis.model_validate(_raw(uncertainty=2.0))


def test_escalation_high_uncertainty():
    a = AssetAnalysis.model_validate(_raw(uncertainty=UNCERTAINTY_ESCALATE))
    d = needs_sonnet_escalation(a, expected_evidence_ids=a.evidence_frame_ids)
    assert "high_uncertainty" in d.reasons
    assert d.should_escalate


def test_escalation_low_crop_confidence():
    a = AssetAnalysis.model_validate(
        _raw(crop_confidence=CROP_CONFIDENCE_LOW - 0.01, uncertainty=0.1)
    )
    d = needs_sonnet_escalation(a, expected_evidence_ids=a.evidence_frame_ids)
    assert "low_crop_confidence" in d.reasons


def test_escalation_scattered_subjects():
    a = AssetAnalysis.model_validate(
        _raw(
            subjects=["a", "b", "c"],
            focus_width=0.8,
            focus_height=0.8,
            crop_confidence=0.3,
            uncertainty=0.1,
            action_progression="clear walk across frame",
        )
    )
    d = needs_sonnet_escalation(a, expected_evidence_ids=a.evidence_frame_ids)
    assert "scattered_subjects" in d.reasons


def test_escalation_ambiguous_action():
    a = AssetAnalysis.model_validate(
        _raw(action_progression="unknown", uncertainty=0.1, crop_confidence=0.8)
    )
    d = needs_sonnet_escalation(a, expected_evidence_ids=a.evidence_frame_ids)
    assert "ambiguous_action_progression" in d.reasons


def test_escalation_missing_evidence_ids():
    eids = ["clip#s001#f01", "clip#s001#f02", "clip#s001#f03"]
    a = AssetAnalysis.model_validate(
        _raw(
            evidence_frame_ids=["clip#s001#f01"],
            uncertainty=0.1,
            crop_confidence=0.8,
            action_progression="clear motion left to right",
        )
    )
    d = needs_sonnet_escalation(a, expected_evidence_ids=eids)
    assert "missing_evidence_ids" in d.reasons


def test_escalation_validation_failed():
    d = needs_sonnet_escalation(
        None,
        expected_evidence_ids=["x"],
        validation_failed=True,
    )
    assert d.reasons == ["structured_output_validation_failed"]


def test_no_escalation_when_clean():
    a = AssetAnalysis.model_validate(_raw())
    d = needs_sonnet_escalation(a, expected_evidence_ids=a.evidence_frame_ids)
    assert d.reasons == []
    assert not d.should_escalate


def test_deterministic_fallback_on_api_failure():
    b = _bundle("still_01#s001")
    b.segment.source_file = "still_01.mp4"
    a = deterministic_fallback_analysis(b)
    assert a.segment_id == "still_01#s001"
    assert a.media_type == MediaType.still_video
    assert a.evidence_frame_ids == b.expected_evidence_ids
    assert 0.0 <= a.technical_quality <= 1.0
    assert a.uncertainty >= 0.5
    assert a.analysis_model == "deterministic"
    assert a.visually_grounded_facts


def test_debug_report_written(tmp_path: Path):
    b = _bundle()
    # Create tiny placeholder images so relative paths resolve in HTML.
    for eid, rel in zip(b.expected_evidence_ids, b.segment.evidence, strict=True):
        path = tmp_path / rel.frame_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8\xff\xd9")  # minimal jpeg-ish bytes
    b.evidence_paths = [tmp_path / e.frame_path for e in b.segment.evidence]
    a = AssetAnalysis.model_validate(_raw())
    out = write_debug_report(
        tmp_path,
        "demo",
        [a],
        {a.segment_id: needs_sonnet_escalation(a, expected_evidence_ids=a.evidence_frame_ids)},
        [b],
    )
    body = out.read_text(encoding="utf-8")
    assert "clip#s001" in body
    assert "asset analysis" in body


def test_validate_analyses_happy_path():
    allowed = {"clip#s001", "clip#s002"}
    ev = {
        "clip#s001": {"clip#s001#f01", "clip#s001#f02", "clip#s001#f03"},
        "clip#s002": {"clip#s002#f01"},
    }
    out = validate_analyses(
        [
            _raw("clip#s001"),
            _raw(
                "clip#s002",
                evidence_frame_ids=["clip#s002#f01"],
                source_file="other.mp4",
                upload_index=1,
            ),
        ],
        allowed_segment_ids=allowed,
        allowed_evidence_by_segment=ev,
        analysis_provider="anthropic",
        analysis_model="claude-haiku-4-5-20251001",
    )
    assert [a.segment_id for a in out] == ["clip#s001", "clip#s002"]
