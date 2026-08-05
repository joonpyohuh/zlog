"""Render before/after MP4s from the bundled six-photo fixture."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from pipeline.ai.schemas import (
    AssetAnalysis,
    CaptionMode,
    MediaType,
    Mood,
    SceneKind,
    ShotType,
    StoryPlan,
    StylePreset,
)
from pipeline.edl import CandidateScene, CandidatesFile, Quality
from pipeline.plan_timeline import plan_timeline
from pipeline.select_baseline import select_baseline
from run import _stage_grade, _stage_render, _which

REPO = Path(__file__).resolve().parents[1]
PROJECT = "creative_fixture_v1"
WORK = REPO / "work"
PROJECT_DIR = WORK / PROJECT
FOOTAGE_DIR = REPO / "footage" / PROJECT
PHOTOS = REPO / "evals" / "fixtures" / "photos_vlog_same_subject"
BGM = REPO / "assets" / "bgm" / "demo_track.wav"


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True, cwd=REPO)


def _make_sources() -> None:
    FOOTAGE_DIR.mkdir(parents=True, exist_ok=True)
    for index in range(1, 7):
        source = PHOTOS / f"photo_{index:02d}.jpg"
        target = FOOTAGE_DIR / f"still_{index:02d}.mp4"
        _run(
            [
                _which("ffmpeg"),
                "-y",
                "-loglevel",
                "error",
                "-loop",
                "1",
                "-i",
                str(source),
                "-t",
                "4.5",
                "-vf",
                "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
                "-r",
                "30",
                "-pix_fmt",
                "yuv420p",
                str(target),
            ]
        )


def _write_inputs() -> None:
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    ids = [f"still_{index:02d}#s001" for index in range(1, 7)]
    candidates: list[CandidateScene] = []
    analyses: list[AssetAnalysis] = []
    for index, segment_id in enumerate(ids):
        source_file = f"still_{index + 1:02d}.mp4"
        candidates.append(
            CandidateScene(
                segment_id=segment_id,
                source_file=source_file,
                start_sec=0.0,
                end_sec=4.5,
                duration=4.5,
                frame_path=f"frames/{segment_id}.jpg",
                quality=Quality(
                    blur_score=220 + index,
                    brightness=0.55,
                    phash=f"fixture-{index}",
                    verdict="pass",
                ),
            )
        )
        analyses.append(
            AssetAnalysis(
                asset_id=f"asset:{segment_id}",
                segment_id=segment_id,
                source_file=source_file,
                media_type=MediaType.still_video,
                upload_index=index,
                evidence_frame_ids=[f"{segment_id}#f01"],
                subjects=["portrait oval"],
                scene=SceneKind.portrait,
                shot_type=[ShotType.close, ShotType.medium, ShotType.detail][index % 3],
                action_progression="portrait appears to react and look toward camera",
                mood=Mood.calm,
                technical_quality=0.8,
                aesthetic_value=0.72 + index * 0.02,
                emotional_value=0.78 if index in {0, 3} else 0.52,
                narrative_value=0.8 if index in {0, 3} else 0.6,
                hook_potential=0.92 if index == 0 else (0.8 if index == 3 else 0.45),
                motion_quality=0.05,
                novelty=0.55 + index * 0.04,
                focus_x=0.42 + index * 0.03,
                focus_y=0.44,
                focus_width=0.5,
                focus_height=0.55,
                crop_confidence=0.82,
                visually_grounded_facts=["warm portrait oval"],
                uncertainty=0.1,
                analysis_provider="anthropic",
                analysis_model="fixture-no-api",
            )
        )
    story = StoryPlan(
        user_intent="quiet photo diary",
        concept="a quiet portrait sequence with one remembered reaction",
        tone=Mood.calm,
        target_duration_sec=18.0,
        style_preset=StylePreset.clean_vlog,
        hook_segment_id=ids[0],
        ending_segment_id=ids[-1],
        selected_segment_ids=ids,
        narrative_arc=[
            "hook",
            "orientation",
            "development",
            "release",
            "resonance",
            "fact: warm portrait oval",
        ],
        caption_mode=CaptionMode.sparse,
        allow_asset_reuse=False,
        provider="anthropic",
        model="fixture-no-api",
    )
    (PROJECT_DIR / "candidates.json").write_text(
        CandidatesFile(project=PROJECT, candidates=candidates).model_dump_json(indent=2),
        encoding="utf-8",
    )
    (PROJECT_DIR / "asset_analyses.json").write_text(
        json.dumps(
            {"project": PROJECT, "analyses": [a.model_dump(mode="json") for a in analyses]},
            indent=2,
        ),
        encoding="utf-8",
    )
    (PROJECT_DIR / "story_plan.json").write_text(
        json.dumps({"project": PROJECT, "plan": story.model_dump(mode="json")}, indent=2),
        encoding="utf-8",
    )


def _contact_sheet(video: Path, output: Path) -> None:
    _run(
        [
            _which("ffmpeg"),
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vf",
            "fps=1/2,scale=240:-1,tile=3x2",
            "-frames:v",
            "1",
            str(output),
        ]
    )


def main() -> None:
    _make_sources()
    _write_inputs()
    comparison = PROJECT_DIR / "comparison"
    comparison.mkdir(parents=True, exist_ok=True)
    for name in (
        "edl_ai.json",
        "timeline_plan.json",
        "creative_execution_plan.json",
        "effect_budget_report.json",
        "creative_execution_report.html",
    ):
        (PROJECT_DIR / name).unlink(missing_ok=True)

    select_baseline(WORK, PROJECT, BGM, 18.0)
    _stage_render(PROJECT_DIR, force=True, scale=0.35, concurrency=2)
    _stage_grade(PROJECT_DIR, force=True)
    shutil.copy2(PROJECT_DIR / "final.mp4", comparison / "before.mp4")
    _contact_sheet(comparison / "before.mp4", comparison / "contact_sheet_before.jpg")

    plan_timeline(WORK, PROJECT, BGM, force=True)
    _stage_render(PROJECT_DIR, force=True, scale=0.35, concurrency=2)
    _stage_grade(PROJECT_DIR, force=True)
    shutil.copy2(PROJECT_DIR / "final.mp4", comparison / "after.mp4")
    _contact_sheet(comparison / "after.mp4", comparison / "contact_sheet_after.jpg")
    print(f"wrote {comparison}")


if __name__ == "__main__":
    main()
