"""Generate a tiny local video and exercise the full perception vertical slice."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from pipeline.adaptive_perception import evaluate_perception, run_adaptive_perception
from pipeline.evidence import run_evidence
from pipeline.filter import filter_scenes
from pipeline.perception_models import (
    EditingGoal,
    HierarchicalTimeline,
    ProcessingCostReport,
)
from pipeline.perception_tools import PerceptionToolbox
from pipeline.split import run_split

FPS = 24
FRAMES_PER_SCENE = 36


def _scene_frames(
    base: tuple[int, int, int], accent: tuple[int, int, int]
) -> list[np.ndarray]:
    frames: list[np.ndarray] = []
    rng = np.random.default_rng(sum(base))
    texture = rng.integers(0, 28, size=(180, 320, 3), dtype=np.uint8)
    for index in range(FRAMES_PER_SCENE):
        frame = np.full((180, 320, 3), base, dtype=np.uint8)
        frame = cv2.add(frame, texture)
        x = 24 + index * 6
        cv2.circle(frame, (x, 90), 28, accent, -1)
        cv2.putText(
            frame,
            "ZLOG",
            (190, 105),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (245, 245, 245),
            2,
            cv2.LINE_AA,
        )
        frames.append(frame)
    return frames


def _write_video(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fourcc = int.from_bytes(b"mp4v", "little")
    writer = cv2.VideoWriter(str(path), fourcc, FPS, (320, 180))
    if not writer.isOpened():
        raise RuntimeError(f"could not create fixture video: {path}")
    scenes = [
        _scene_frames((38, 72, 128), (45, 210, 245)),
        _scene_frames((42, 135, 58), (240, 190, 35)),
        _scene_frames((145, 35, 125), (45, 235, 150)),
    ]
    for frame in (frame for scene in scenes for frame in scene):
        writer.write(frame)
    writer.release()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", type=Path, default=Path("work"))
    parser.add_argument("--footage-root", type=Path, default=Path("footage"))
    args = parser.parse_args()
    project = "adaptive_perception_fixture"
    footage_dir = args.footage_root / project
    project_dir = args.work_root / project
    _write_video(footage_dir / "journey.mp4")

    run_split(footage_dir, args.work_root, force=True)
    run_evidence(args.work_root, project, footage_dir=footage_dir, force=True)
    filter_scenes(args.work_root, project)
    goal = EditingGoal(
        target_duration_seconds=60,
        platform="youtube",
        pacing="slow",
        narrative_preference="calm chronological travel diary",
    )
    timeline_path = run_adaptive_perception(
        args.work_root,
        project,
        footage_dir=footage_dir,
        editing_goal=goal,
        force=True,
    )
    # A second run is the executable cache check used by the evaluation report.
    run_adaptive_perception(
        args.work_root,
        project,
        footage_dir=footage_dir,
        editing_goal=goal,
    )
    annotation_path = Path(__file__).parent / "fixtures" / "perception_boundaries.json"
    evaluation_path = evaluate_perception(
        project_dir, annotation_path, tolerance_ms=300
    )
    timeline = HierarchicalTimeline.model_validate_json(
        timeline_path.read_text(encoding="utf-8")
    )
    report = ProcessingCostReport.model_validate_json(
        (project_dir / "perception_cost_report.json").read_text(encoding="utf-8")
    )
    summary = PerceptionToolbox(project_dir).get_timeline_summary()
    semantic_merges = sum(
        node.level == "semantic_scene" and len(node.child_ids) > 1
        for node in timeline.nodes
    )
    if semantic_merges < 1:
        raise AssertionError("fixture expected at least one merged semantic scene")
    print(
        json.dumps(
            {
                "timeline": str(timeline_path),
                "evaluation": str(evaluation_path),
                "summary": summary,
                "semantic_merges": semantic_merges,
                "cache_hits": report.cache_hits,
                "estimated_cost_usd": report.estimated_cost_usd,
                "multimodal_calls": report.multimodal_calls,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
