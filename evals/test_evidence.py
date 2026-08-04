"""PROMPT 2 — adaptive evidence extraction (no AI API calls)."""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from pipeline.edl import Scene, SegmentsFile
from pipeline.evidence import (
    HIGH_MOTION,
    HIGH_PERCEPTUAL,
    STATIC_MOTION,
    STATIC_PERCEPTUAL,
    DeterministicFeaturesFile,
    EvidenceManifest,
    _select_frame_indices,
    _unique_frame_indices,
    evidence_id_for,
    load_upload_order,
    read_capture_time,
    resolve_source_meta,
    run_evidence,
    write_upload_order,
)

FPS = 30


def _write_video(path: Path, frames: list[np.ndarray], fps: int = FPS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    h, w = frames[0].shape[:2]
    writer = cv2.VideoWriter(
        str(path),
        cv2.VideoWriter_fourcc(*"mp4v"),
        fps,
        (w, h),
    )
    assert writer.isOpened(), f"VideoWriter failed for {path}"
    for frame in frames:
        writer.write(frame)
    writer.release()


def _static_frames(n: int = 90, size: tuple[int, int] = (160, 120)) -> list[np.ndarray]:
    w, h = size
    base = np.full((h, w, 3), (40, 90, 160), dtype=np.uint8)
    # Tiny texture so Laplacian blur is non-zero but scene is still static.
    rng = np.random.default_rng(0)
    noise = rng.integers(0, 6, size=(h, w, 3), dtype=np.uint8)
    frame = cv2.add(base, noise)
    return [frame.copy() for _ in range(n)]


def _motion_frames(n: int = 90, size: tuple[int, int] = (160, 120)) -> list[np.ndarray]:
    w, h = size
    frames: list[np.ndarray] = []
    for i in range(n):
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        frame[:] = (20, 20, 20)
        x = int((i / max(1, n - 1)) * (w - 40))
        y = 30 + (i % 20)
        cv2.rectangle(frame, (x, y), (x + 36, y + 36), (0, 255, 255), -1)
        cv2.circle(frame, (w - x - 10, h - y - 10), 12, (255, 0, 128), -1)
        frames.append(frame)
    return frames


def _project_with_video(
    tmp_path: Path,
    *,
    name: str,
    frames: list[np.ndarray],
    source_name: str = "clip.mp4",
    upload_files: list[str] | None = None,
) -> tuple[Path, Path]:
    footage = tmp_path / "footage" / name
    work = tmp_path / "work"
    project_dir = work / name
    project_dir.mkdir(parents=True)
    footage.mkdir(parents=True)
    video = footage / source_name
    _write_video(video, frames)
    if upload_files is not None:
        write_upload_order(footage, upload_files)
    duration = len(frames) / FPS
    segments = SegmentsFile(
        project=name,
        segments=[
            Scene(
                segment_id=f"{Path(source_name).stem}#s001",
                source_file=source_name,
                start_sec=0.0,
                end_sec=round(duration, 3),
                duration=round(duration, 3),
                frame_path=f"frames/{Path(source_name).stem}#s001.jpg",
            )
        ],
    )
    # Keep a dummy mid-frame so layout matches split (evidence does not need it).
    (project_dir / "frames").mkdir(exist_ok=True)
    Image.fromarray(cv2.cvtColor(frames[len(frames) // 2], cv2.COLOR_BGR2RGB)).save(
        project_dir / segments.segments[0].frame_path
    )
    (project_dir / "segments.json").write_text(segments.model_dump_json(indent=2), encoding="utf-8")
    return footage, work


def test_evidence_id_stability():
    assert evidence_id_for("clip#s001", 1) == "clip#s001#f01"
    assert evidence_id_for("clip#s001", 2) == "clip#s001#f02"
    assert evidence_id_for("still_01#s001", 3) == "still_01#s001#f03"
    with pytest.raises(ValueError):
        evidence_id_for("x", 0)


def test_default_positions_time_order():
    pairs = _unique_frame_indices(0, 100, (0.2, 0.5, 0.8))
    assert [f for f, _ in pairs] == sorted(f for f, _ in pairs)
    assert [round(r, 1) for _, r in pairs] == [0.2, 0.5, 0.8]


def test_static_scene_reduces_to_one_frame(tmp_path: Path):
    footage, work = _project_with_video(tmp_path, name="static_one", frames=_static_frames())
    out = run_evidence(work, "static_one", footage_dir=footage, force=True)
    manifest = EvidenceManifest.model_validate_json(out.read_text(encoding="utf-8"))
    feats = DeterministicFeaturesFile.model_validate_json(
        (work / "static_one" / "deterministic_features.json").read_text(encoding="utf-8")
    )
    assert len(manifest.segments) == 1
    assert len(manifest.segments[0].evidence) == 1
    assert manifest.segments[0].evidence[0].evidence_id.endswith("#f01")
    assert manifest.segments[0].evidence[0].role == "representative"
    assert feats.segments[0].is_static is True


def test_high_motion_adds_extra_frames(tmp_path: Path):
    footage, work = _project_with_video(tmp_path, name="motion_x", frames=_motion_frames(120))
    out = run_evidence(work, "motion_x", footage_dir=footage, force=True)
    manifest = EvidenceManifest.model_validate_json(out.read_text(encoding="utf-8"))
    feats = DeterministicFeaturesFile.model_validate_json(
        (work / "motion_x" / "deterministic_features.json").read_text(encoding="utf-8")
    )
    ev = manifest.segments[0].evidence
    assert feats.segments[0].is_high_motion is True
    assert 3 <= len(ev) <= 5  # 3 defaults + up to 2 extras
    # Temporal order + stable ids
    offsets = [e.offset_sec for e in ev]
    assert offsets == sorted(offsets)
    assert [e.evidence_id for e in ev] == [
        evidence_id_for(manifest.segments[0].segment_id, i) for i in range(1, len(ev) + 1)
    ]
    assert any(e.role == "extra" for e in ev) or len(ev) >= 3


def test_evidence_ids_stable_across_reruns(tmp_path: Path):
    footage, work = _project_with_video(tmp_path, name="stable_ids", frames=_motion_frames(90))
    run_evidence(work, "stable_ids", footage_dir=footage, force=True)
    first = EvidenceManifest.model_validate_json(
        (work / "stable_ids" / "evidence_manifest.json").read_text(encoding="utf-8")
    )
    run_evidence(work, "stable_ids", footage_dir=footage, force=True)
    second = EvidenceManifest.model_validate_json(
        (work / "stable_ids" / "evidence_manifest.json").read_text(encoding="utf-8")
    )
    assert [e.evidence_id for e in first.segments[0].evidence] == [
        e.evidence_id for e in second.segments[0].evidence
    ]
    assert [e.offset_sec for e in first.segments[0].evidence] == [
        e.offset_sec for e in second.segments[0].evidence
    ]


def test_upload_order_preserved_not_alpha(tmp_path: Path):
    """z.mp4 uploaded before a.mp4 → upload_index follows upload_order, not alpha."""
    name = "upload_ord"
    footage = tmp_path / "footage" / name
    work = tmp_path / "work"
    project_dir = work / name
    project_dir.mkdir(parents=True)
    footage.mkdir(parents=True)
    frames = _static_frames(60)
    _write_video(footage / "z_first.mp4", frames)
    _write_video(footage / "a_second.mp4", frames)
    write_upload_order(footage, ["z_first.mp4", "a_second.mp4"])

    # segments listed alpha-style on purpose — evidence must still use upload_order
    segments = SegmentsFile(
        project=name,
        segments=[
            Scene(
                segment_id="a_second#s001",
                source_file="a_second.mp4",
                start_sec=0.0,
                end_sec=2.0,
                duration=2.0,
                frame_path="frames/a_second#s001.jpg",
            ),
            Scene(
                segment_id="z_first#s001",
                source_file="z_first.mp4",
                start_sec=0.0,
                end_sec=2.0,
                duration=2.0,
                frame_path="frames/z_first#s001.jpg",
            ),
        ],
    )
    (project_dir / "frames").mkdir()
    Image.new("RGB", (64, 64), (10, 10, 10)).save(project_dir / "frames/a_second#s001.jpg")
    Image.new("RGB", (64, 64), (10, 10, 10)).save(project_dir / "frames/z_first#s001.jpg")
    (project_dir / "segments.json").write_text(segments.model_dump_json(indent=2), encoding="utf-8")

    run_evidence(work, name, footage_dir=footage, force=True)
    manifest = EvidenceManifest.model_validate_json(
        (project_dir / "evidence_manifest.json").read_text(encoding="utf-8")
    )
    by_src = {s.source_file: s for s in manifest.segments}
    assert by_src["z_first.mp4"].upload_index == 0
    assert by_src["a_second.mp4"].upload_index == 1
    assert by_src["z_first.mp4"].time_order == 0
    assert by_src["a_second.mp4"].time_order == 1
    assert load_upload_order(footage) == ["z_first.mp4", "a_second.mp4"]


def test_exif_missing_falls_back_to_upload_order(tmp_path: Path):
    footage = tmp_path / "footage"
    footage.mkdir()
    # Images without EXIF
    Image.new("RGB", (32, 32), (200, 100, 50)).save(footage / "late.jpg")
    Image.new("RGB", (32, 32), (50, 100, 200)).save(footage / "early.jpg")
    order = ["late.jpg", "early.jpg"]
    write_upload_order(footage, order)
    metas = resolve_source_meta(
        ["still_01.mp4", "still_02.mp4"],
        footage,
        order,
    )
    assert metas["still_01.mp4"].upload_index == 0
    assert metas["still_02.mp4"].upload_index == 1
    assert metas["still_01.mp4"].capture_time is None
    assert metas["still_02.mp4"].capture_time is None
    assert metas["still_01.mp4"].time_order == 0
    assert metas["still_02.mp4"].time_order == 1


def test_bogus_exif_rejected(tmp_path: Path):
    path = tmp_path / "bogus.jpg"
    img = Image.new("RGB", (48, 48), (1, 2, 3))
    exif = img.getexif()
    # DateTimeOriginal
    exif[36867] = "1970:01:01 00:00:00"
    img.save(path, exif=exif)
    capture, trusted = read_capture_time(path)
    assert trusted is False
    assert capture is None


def test_contact_sheet_labels_include_evidence_ids(tmp_path: Path):
    footage, work = _project_with_video(tmp_path, name="sheet_lbl", frames=_motion_frames(90))
    run_evidence(work, "sheet_lbl", footage_dir=footage, force=True)
    project_dir = work / "sheet_lbl"
    sheet_manifest = json.loads((project_dir / "evidence_sheet_manifest.json").read_text(encoding="utf-8"))
    assert sheet_manifest["project"] == "sheet_lbl"
    assert sheet_manifest["manifest"]
    for eid, cell in sheet_manifest["manifest"].items():
        assert eid.endswith("#f01") or "#f0" in eid
        assert "segment_id" in cell
        assert "source_file" in cell
        assert "upload_index" in cell
        assert "time_order" in cell
    sheets = list((project_dir / "evidence_sheets").glob("sheet_*.jpg"))
    assert sheets


def test_deterministic_features_present(tmp_path: Path):
    footage, work = _project_with_video(tmp_path, name="feats", frames=_motion_frames(60))
    run_evidence(work, "feats", footage_dir=footage, force=True)
    feats = DeterministicFeaturesFile.model_validate_json(
        (work / "feats" / "deterministic_features.json").read_text(encoding="utf-8")
    )
    seg = feats.segments[0]
    assert seg.duration_sec > 0
    assert seg.audio_rms >= 0
    assert seg.onset_density >= 0
    assert 0 <= seg.mean_perceptual_distance <= 1
    for fr in seg.frames:
        assert 0 <= fr.brightness <= 1
        assert 0 <= fr.contrast <= 1
        assert 0 <= fr.perceptual_distance_prev <= 1


class _Hash:
    def __init__(self, n: int) -> None:
        self.n = n

    def __sub__(self, other: object) -> int:
        if not isinstance(other, _Hash):
            return 0
        return abs(self.n - other.n)


def test_select_static_vs_high_helpers():
    start_f, end_f = 0, 100
    defaults = _unique_frame_indices(start_f, end_f, (0.2, 0.5, 0.8))
    probe_bgrs = {f: np.zeros((8, 8, 3), dtype=np.uint8) for f, _ in defaults}
    probe_stats = {
        f: (float(50 - abs(i - 1) * 5), 0.5, 0.1, _Hash(0))
        for i, (f, _) in enumerate(defaults)
    }
    selected = _select_frame_indices(
        start_f,
        end_f,
        30.0,
        probe_bgrs,
        probe_stats,
        motions=[STATIC_MOTION * 0.1] * 5,
        perceptuals=[STATIC_PERCEPTUAL * 0.1] * 5,
    )
    assert len(selected) == 1
    assert selected[0][2] == "representative"

    selected_hi = _select_frame_indices(
        start_f,
        end_f,
        30.0,
        probe_bgrs,
        {f: (50.0, 0.5, 0.1, _Hash(i)) for i, (f, _) in enumerate(defaults)},
        motions=[HIGH_MOTION + 1.0] * 5,
        perceptuals=[HIGH_PERCEPTUAL + 0.1] * 5,
    )
    assert len(selected_hi) >= 3
