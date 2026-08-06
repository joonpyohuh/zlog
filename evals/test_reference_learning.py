from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from pipeline.edit_presets import get_preset
from pipeline.reference_learning import analyze_workspace, init_workspace
from pipeline.reference_models import (
    ManualEditMarker,
    ReferenceAnnotation,
    ReferenceTrainingExample,
)


def _write_two_scene_video(path: Path) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (160, 90))
    if not writer.isOpened():
        pytest.skip("OpenCV mp4 writer is unavailable")
    try:
        for color in ((0, 0, 255), (255, 0, 0)):
            frame = np.full((90, 160, 3), color, dtype=np.uint8)
            for _ in range(60):
                writer.write(frame)
    finally:
        writer.release()


def test_preset_registry_rejects_unknown_or_wrong_category() -> None:
    assert get_preset("slow_push_subject", "motion").renderer_status == "implemented"
    with pytest.raises(ValueError, match="Unknown edit preset"):
        get_preset("invented-effect")
    with pytest.raises(ValueError, match="not caption"):
        get_preset("clean_cut", "caption")


def test_reference_workspace_builds_valid_versioned_artifacts(tmp_path: Path) -> None:
    workspace = init_workspace(tmp_path / "references")
    video_path = workspace.inbox / "two-scene.mp4"
    _write_two_scene_video(video_path)
    annotation = ReferenceAnnotation(
        license="owned",
        annotator="test",
        style_tags=["fixture"],
        caption_presets=["minimal_bottom"],
        transition_presets=["clean_cut"],
        markers=[
            ManualEditMarker(
                timestamp_ms=1000,
                category="motion",
                preset_id="punch_in",
                note="hook",
            )
        ],
    )
    (workspace.annotations / "two-scene.mp4.json").write_text(
        annotation.model_dump_json(indent=2), encoding="utf-8"
    )

    examples = analyze_workspace(workspace.root)

    assert len(examples) == 1
    example = examples[0]
    assert example.schema_version == "1.0"
    assert example.reference_media.duration_ms == pytest.approx(4000, abs=100)
    assert example.reference_edit_analysis.pacing.cut_count >= 1
    assert example.reference_edit_analysis.captions.status == "observed"
    assert example.reference_edit_analysis.editorial_motion.status == "observed"
    assert example.reference_edit_analysis.markers[0].timestamp_ms == 1000
    assert example.provenance.analysis_origin == "hybrid"
    assert example.provenance.license == "owned"
    assert example.split in {"train", "validation", "test"}

    artifact_dir = next(path for path in workspace.library.iterdir() if path.is_dir())
    training_path = artifact_dir / "training_example.json"
    loaded = ReferenceTrainingExample.model_validate_json(
        training_path.read_text(encoding="utf-8")
    )
    assert loaded.example_id == example.example_id
    assert (workspace.library / "catalog.json").exists()
    assert (workspace.schemas / "preset_registry.json").exists()
