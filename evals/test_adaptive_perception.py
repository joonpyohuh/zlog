"""Deterministic contracts and end-to-end adaptive-perception checks."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.adaptive_perception import evaluate_perception, run_adaptive_perception
from pipeline.edl import Scene, SegmentsFile
from pipeline.evidence import (
    DeterministicFeaturesFile,
    EvidenceFrame,
    EvidenceManifest,
    EvidenceSegment,
    FrameFeatures,
    SegmentFeatures,
    SourceMeta,
)
from pipeline.perception_models import (
    AnalysisBudget,
    BoundaryDecisionsArtifact,
    EditingGoal,
    HierarchicalTimeline,
    ProcessingCostReport,
)
from pipeline.perception_tools import PerceptionToolbox


def _write_project(tmp_path: Path) -> tuple[Path, Path]:
    work = tmp_path / "work"
    project_dir = work / "adaptive_fixture"
    project_dir.mkdir(parents=True)
    scenes = []
    evidence_segments = []
    features = []
    values = [
        (0.46, 0.18, 0.20, 0.04, 0.01),
        (0.48, 0.20, 0.22, 0.05, 0.01),
        (0.78, 0.52, 2.40, 0.48, 0.08),
    ]
    for index, (brightness, contrast, motion, perceptual, rms) in enumerate(values, 1):
        segment_id = f"journey#s{index:03d}"
        start = float((index - 1) * 2)
        end = float(index * 2)
        evidence_id = f"{segment_id}#f01"
        scenes.append(
            Scene(
                segment_id=segment_id,
                source_file="journey.mp4",
                start_sec=start,
                end_sec=end,
                duration=2.0,
                frame_path=f"frames/{segment_id}.jpg",
            )
        )
        evidence_segments.append(
            EvidenceSegment(
                segment_id=segment_id,
                source_file="journey.mp4",
                upload_index=0,
                time_order=0,
                start_sec=start,
                end_sec=end,
                duration=2.0,
                evidence=[
                    EvidenceFrame(
                        evidence_id=evidence_id,
                        frame_path=f"evidence/{evidence_id}.jpg",
                        offset_sec=start + 1.0,
                        rel_pos=0.5,
                        role="representative",
                    )
                ],
            )
        )
        features.append(
            SegmentFeatures(
                segment_id=segment_id,
                duration_sec=2.0,
                audio_rms=rms,
                onset_density=rms * 10,
                mean_motion=motion,
                mean_perceptual_distance=perceptual,
                is_static=motion < 0.35,
                is_high_motion=motion > 1.8,
                frames=[
                    FrameFeatures(
                        evidence_id=evidence_id,
                        blur=180.0,
                        brightness=brightness,
                        contrast=contrast,
                        motion_magnitude=motion,
                        perceptual_distance_prev=perceptual,
                    )
                ],
            )
        )
    (project_dir / "segments.json").write_text(
        SegmentsFile(project="adaptive_fixture", segments=scenes).model_dump_json(
            indent=2
        ),
        encoding="utf-8",
    )
    (project_dir / "evidence_manifest.json").write_text(
        EvidenceManifest(
            project="adaptive_fixture",
            sources=[
                SourceMeta(source_file="journey.mp4", upload_index=0, time_order=0)
            ],
            segments=evidence_segments,
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    (project_dir / "deterministic_features.json").write_text(
        DeterministicFeaturesFile(
            project="adaptive_fixture", segments=features
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    return work, project_dir


def test_vertical_slice_writes_hierarchy_tools_cost_and_cache(tmp_path: Path) -> None:
    work, project_dir = _write_project(tmp_path)
    goal = EditingGoal(target_duration_seconds=60, pacing="slow")
    out = run_adaptive_perception(work, "adaptive_fixture", editing_goal=goal)
    timeline = HierarchicalTimeline.model_validate_json(out.read_text(encoding="utf-8"))

    levels = {str(node.level) for node in timeline.nodes}
    assert levels == {
        "frame",
        "shot",
        "atomic_event",
        "semantic_scene",
        "narrative_beat",
        "chapter",
    }
    assert any(
        node.level == "semantic_scene" and len(node.child_ids) >= 2
        for node in timeline.nodes
    )
    assert timeline.boundary_candidate_ids
    assert all(node.source_ranges for node in timeline.nodes)

    toolbox = PerceptionToolbox(project_dir)
    summary = toolbox.execute({"tool": "get_timeline_summary", "arguments": {}})
    assert summary["result"]["level_counts"]["atomic_event"] == 3
    search = toolbox.execute(
        {"tool": "search_events", "arguments": {"query": "activity", "topK": 200}}
    )
    assert 0 < len(search["result"]) <= toolbox.MAX_RESULTS
    assert toolbox.inspect_expression("journey#s001")["status"] == "unavailable"

    run_adaptive_perception(work, "adaptive_fixture", editing_goal=goal)
    report = ProcessingCostReport.model_validate_json(
        (project_dir / "perception_cost_report.json").read_text(encoding="utf-8")
    )
    assert report.cache_hits == 5
    assert report.cache_misses == 0
    assert report.multimodal_calls == 0
    assert report.text_model_calls == 0
    assert report.estimated_cost_usd == 0


def test_boundary_evaluation_is_repeatable(tmp_path: Path) -> None:
    work, project_dir = _write_project(tmp_path)
    run_adaptive_perception(work, "adaptive_fixture")
    annotations = tmp_path / "annotations.json"
    annotations.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "asset": "journey.mp4",
                "boundaries": [
                    {"timestamp_ms": 2000, "label": "shot_change"},
                    {"timestamp_ms": 4000, "label": "activity_change"},
                ],
            }
        ),
        encoding="utf-8",
    )
    result = evaluate_perception(project_dir, annotations)
    metrics = json.loads(result.read_text(encoding="utf-8"))
    assert metrics["boundary_precision"] == 1.0
    assert metrics["boundary_recall"] == 1.0
    assert metrics["boundary_f1"] == 1.0
    assert metrics["hierarchy_parent_link_rate"] == 1.0
    assert metrics["estimated_cost_usd"] == 0.0


def test_goal_and_frame_budget_change_only_relevant_cached_stages(tmp_path: Path) -> None:
    work, project_dir = _write_project(tmp_path)
    run_adaptive_perception(
        work,
        "adaptive_fixture",
        editing_goal=EditingGoal(target_duration_seconds=60, pacing="slow"),
        budget=AnalysisBudget(max_representative_frames=1),
    )
    slow = HierarchicalTimeline.model_validate_json(
        (project_dir / "hierarchical_timeline.json").read_text(encoding="utf-8")
    )
    assert slow.quality_status == "degraded"
    assert "representative_frame_budget_applied" in slow.degraded_reasons
    slow_decisions = BoundaryDecisionsArtifact.model_validate_json(
        (project_dir / "boundary_decisions.json").read_text(encoding="utf-8")
    )
    assert [item.decision for item in slow_decisions.decisions] == ["merge", "uncertain"]

    run_adaptive_perception(
        work,
        "adaptive_fixture",
        editing_goal=EditingGoal(target_duration_seconds=15, pacing="fast"),
        budget=AnalysisBudget(max_representative_frames=1),
    )
    fast = HierarchicalTimeline.model_validate_json(
        (project_dir / "hierarchical_timeline.json").read_text(encoding="utf-8")
    )
    report = ProcessingCostReport.model_validate_json(
        (project_dir / "perception_cost_report.json").read_text(encoding="utf-8")
    )
    fast_decisions = BoundaryDecisionsArtifact.model_validate_json(
        (project_dir / "boundary_decisions.json").read_text(encoding="utf-8")
    )
    assert [item.decision for item in fast_decisions.decisions] == ["merge", "split"]
    assert sum(node.level == "semantic_scene" for node in fast.nodes) == 2
    assert report.cache_hits == 3
    assert report.cache_misses == 2
