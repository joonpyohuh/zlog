from __future__ import annotations

import json
from pathlib import Path

from pipeline.editorial_intelligence import (
    build_timeline,
    build_trip_graph,
    parse_editing_intent,
    select_highlights,
    timeline_to_edl,
)
from pipeline.music_catalog import MusicTrack, recommend_music


def _write_project(tmp_path: Path) -> Path:
    project = tmp_path / "trip"
    project.mkdir()
    segments = [
        {
            "segment_id": f"s{i}",
            "source_file": source,
            "start_sec": 0.0,
            "end_sec": 8.0,
            "duration": 8.0,
            "frame_path": f"frames/s{i}.jpg",
        }
        for i, source in enumerate(("airport.mp4", "train.mp4", "coffee.mp4"), 1)
    ]
    (project / "segments.json").write_text(
        json.dumps({"project": "trip", "segments": segments}), encoding="utf-8"
    )
    candidates = []
    rejected = []
    for index, segment in enumerate(segments):
        item = {
            **segment,
            "quality": {
                "blur_score": 100.0,
                "brightness": 120.0,
                "phash": str(index),
                "verdict": "pass" if index != 1 else "blurry",
            },
        }
        (candidates if index != 1 else rejected).append(item)
    (project / "candidates.json").write_text(
        json.dumps({"project": "trip", "candidates": candidates, "rejected": rejected}),
        encoding="utf-8",
    )
    analyses = []
    for index, (segment, scene, action, facts) in enumerate(
        zip(
            segments,
            ("transit", "transit", "food"),
            ("Arriving at the airport", "Friends laugh on the train", "Making coffee"),
            (("airport sign",), ("friends smile together",), ("coffee is poured",)),
            strict=True,
        )
    ):
        analyses.append(
            {
                "asset_id": f"a{index}",
                "segment_id": segment["segment_id"],
                "source_file": segment["source_file"],
                "media_type": "video",
                "upload_index": index,
                "capture_time": f"2026-08-0{1 + index // 2}T10:00:00",
                "scene": scene,
                "action_progression": action,
                "motion_quality": 0.7,
                "visually_grounded_facts": list(facts),
                "analysis_provider": "anthropic",
                "analysis_model": "fixture",
            }
        )
    (project / "asset_analyses.json").write_text(
        json.dumps({"analyses": analyses}), encoding="utf-8"
    )
    return project


def test_trip_graph_preserves_rejected_context_and_observed_days(
    tmp_path: Path,
) -> None:
    project = _write_project(tmp_path)
    graph = build_trip_graph(project, "trip")

    assert graph.input_mode == "multiple_clips"
    assert len(graph.scenes) == 3
    assert graph.scenes[1].technical_quality == "limited"
    assert [day.date for day in graph.days] == ["2026-08-01", "2026-08-02"]


def test_long_and_short_are_distinct_content_led_edits(tmp_path: Path) -> None:
    graph = build_trip_graph(_write_project(tmp_path), "trip")
    intent = parse_editing_intent("친구와 커피를 중심으로 빠르고 자막은 적게")
    long = build_timeline(
        graph, intent, select_highlights(graph, intent, "long"), "long"
    )
    short = build_timeline(
        graph, intent, select_highlights(graph, intent, "short"), "short"
    )

    assert [item.scene_id for item in long.video_items] == [
        scene.scene_id for scene in graph.scenes
    ]
    assert short.video_items[0].scene_id == graph.scenes[1].scene_id
    assert short.video_items[-1].scene_id == graph.scenes[-1].scene_id
    assert short.duration_frames <= short.fps * 40
    assert sum(item.effect.effect_id != "cut" for item in short.video_items) <= 2
    assert all(item.effect.reason for item in (*long.video_items, *short.video_items))
    assert long.target_duration_sec == 600
    assert short.target_duration_sec == 35

    edl = timeline_to_edl(short)
    assert edl.audio.enabled is False
    assert edl.audio.bgm_id == ""
    assert any(clip.primary_effect == "reaction_punch_in" for clip in edl.timeline)


def test_prompt_unknowns_and_music_contract_have_no_fake_scores(tmp_path: Path) -> None:
    graph = build_trip_graph(_write_project(tmp_path), "trip")
    intent = parse_editing_intent("")
    tracks = [
        MusicTrack(
            music_id="calm",
            title="Quiet Walk",
            artist="Zlog",
            file="assets/bgm/demo_track.wav",
            mood_tags=["calm", "travel"],
            energy="low",
        )
    ]

    assert intent.pacing is None
    assert intent.desired_mood is None
    assert intent.unknowns
    recommended = recommend_music(graph, intent, tracks)
    assert recommended[0].music_id == "calm"
    assert "score" not in recommended[0].model_dump()


def test_short_budget_never_drops_the_payoff(tmp_path: Path) -> None:
    graph = build_trip_graph(_write_project(tmp_path), "trip")
    first, middle, ending = graph.scenes
    middle_copies = [
        middle.model_copy(
            update={
                "scene_id": f"scene-middle-{index}",
                "segment_id": f"middle-{index}",
                "chronological_index": index + 1,
            }
        )
        for index in range(18)
    ]
    ending = ending.model_copy(update={"chronological_index": 19})
    graph = graph.model_copy(
        update={
            "scenes": [first, *middle_copies, ending],
            "events": [
                graph.events[0].model_copy(
                    update={
                        "scene_ids": [first.scene_id]
                        + [scene.scene_id for scene in middle_copies]
                    }
                ),
                graph.events[-1].model_copy(update={"scene_ids": [ending.scene_id]}),
            ],
        }
    )
    intent = parse_editing_intent("빠르게")
    timeline = build_timeline(
        graph, intent, select_highlights(graph, intent, "short"), "short"
    )

    assert timeline.video_items[-1].scene_id == ending.scene_id
    assert timeline.duration_frames <= timeline.fps * 35
