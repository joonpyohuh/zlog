"""PROMPT 5 — deterministic timeline optimizer (no AI API)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

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
from pipeline.edl import (
    CandidateScene,
    CandidatesFile,
    Quality,
    load_edl,
    validate_edl,
)
from pipeline.plan_timeline import (
    adjust_target_duration,
    drop_adjacent_redundancy,
    order_for_playback,
    plan_timeline,
)
from pipeline.select_baseline import extend_beat_grid

REPO = Path(__file__).resolve().parents[1]
BGM = REPO / "assets" / "bgm" / "demo_track.wav"


def _analysis(segment_id: str, **over: Any) -> AssetAnalysis:
    idx = 0
    if segment_id.startswith("still_"):
        try:
            idx = int(segment_id.split("_")[1][:2]) - 1
        except ValueError:
            idx = 0
    base: dict[str, Any] = {
        "asset_id": f"asset:{segment_id}",
        "segment_id": segment_id,
        "source_file": f"{segment_id.split('#')[0]}.mp4",
        "media_type": MediaType.still_video.value,
        "upload_index": idx,
        "evidence_frame_ids": [f"{segment_id}#f01"],
        "subjects": ["person"],
        "scene": SceneKind.outdoor.value,
        "shot_type": ShotType.medium.value,
        "action_progression": "smiles at camera",
        "mood": Mood.calm.value,
        "technical_quality": 0.7,
        "aesthetic_value": 0.65,
        "emotional_value": 0.55,
        "narrative_value": 0.6,
        "hook_potential": 0.4,
        "motion_quality": 0.1,
        "novelty": 0.5,
        "redundancy_group": None,
        "focus_x": 0.5,
        "focus_y": 0.45,
        "focus_width": 0.5,
        "focus_height": 0.55,
        "crop_confidence": 0.7,
        "visually_grounded_facts": ["person outdoors"],
        "uncertainty": 0.2,
        "analysis_provider": "anthropic",
        "analysis_model": "claude-haiku-4-5-20251001",
    }
    base.update(over)
    return AssetAnalysis.model_validate(base)


def _cand(segment_id: str, start: float = 0.0, end: float = 8.0) -> CandidateScene:
    return CandidateScene(
        segment_id=segment_id,
        source_file=f"{segment_id.split('#')[0]}.mp4",
        start_sec=start,
        end_sec=end,
        duration=end - start,
        frame_path=f"frames/{segment_id}.jpg",
        quality=Quality(
            blur_score=200.0,
            brightness=0.5,
            phash="abc",
            verdict="pass",
        ),
    )


def _story(ids: list[str], **over: Any) -> StoryPlan:
    base = {
        "user_intent": "감성적인 유튜브 브이로그로 만들어줘",
        "concept": "quiet walk",
        "tone": Mood.calm,
        "target_duration_sec": 30.0,
        "style_preset": StylePreset.clean_vlog,
        "hook_segment_id": ids[0],
        "ending_segment_id": ids[-1],
        "selected_segment_ids": ids,
        "narrative_arc": ["hook", "fact: person outdoors", "mood: calm", "close"],
        "caption_mode": CaptionMode.sparse,
        "allow_asset_reuse": False,
        "provider": "anthropic",
        "model": "claude-sonnet-4-5-20250929",
    }
    base.update(over)
    return StoryPlan.model_validate(base)


def _project(
    tmp_path: Path,
    *,
    name: str,
    story: StoryPlan,
    analyses: list[AssetAnalysis],
    candidates: list[CandidateScene],
) -> Path:
    project_dir = tmp_path / name
    project_dir.mkdir(parents=True)
    (project_dir / "story_plan.json").write_text(
        json.dumps({"project": name, "plan": story.model_dump(mode="json")}, ensure_ascii=False),
        encoding="utf-8",
    )
    (project_dir / "asset_analyses.json").write_text(
        json.dumps(
            {"project": name, "analyses": [a.model_dump(mode="json") for a in analyses]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (project_dir / "candidates.json").write_text(
        CandidatesFile(project=name, candidates=candidates, rejected=[]).model_dump_json(indent=2),
        encoding="utf-8",
    )
    return tmp_path


def test_no_modulo_repeat_when_reuse_false(tmp_path: Path):
    ids = [f"still_{i:02d}#s001" for i in range(1, 4)]
    analyses = [_analysis(s) for s in ids]
    cands = [_cand(s) for s in ids]
    story = _story(ids, target_duration_sec=45.0, allow_asset_reuse=False)
    work = _project(tmp_path, name="noreuse", story=story, analyses=analyses, candidates=cands)
    plan_timeline(work, "noreuse", BGM, force=True)
    edl = load_edl(work / "noreuse" / "edl_ai.json")
    seg_ids = [c.segment_id for c in edl.timeline]
    assert len(seg_ids) == len(set(seg_ids))
    assert len(seg_ids) <= 3
    total = sum(c.out_sec - c.in_sec for c in edl.timeline)
    assert total <= adjust_target_duration(45.0, 3, allow_reuse=False) + 1.5


def test_adjacent_redundancy_group_prevented():
    analyses = {
        "a#s001": _analysis("a#s001", redundancy_group="g1", narrative_value=0.4, upload_index=0),
        "b#s001": _analysis("b#s001", redundancy_group="g1", narrative_value=0.9, upload_index=1),
        "c#s001": _analysis("c#s001", redundancy_group="g2", narrative_value=0.5, upload_index=2),
    }
    out = drop_adjacent_redundancy(["a#s001", "b#s001", "c#s001"], analyses)
    assert out == ["b#s001", "c#s001"] or out == ["a#s001", "c#s001"]
    assert not (
        len(out) >= 2
        and analyses[out[0]].redundancy_group == analyses[out[1]].redundancy_group
    )


def test_chronology_after_cold_open():
    ids = ["still_01#s001", "still_02#s001", "still_03#s001", "still_04#s001"]
    analyses = [
        _analysis(ids[0], upload_index=0, hook_potential=0.2),
        _analysis(ids[1], upload_index=1, hook_potential=0.3),
        _analysis(ids[2], upload_index=2, hook_potential=0.95),  # late strong hook
        _analysis(ids[3], upload_index=3, hook_potential=0.4),
    ]
    by_cand = {s: _cand(s) for s in ids}
    story = _story(
        ids,
        hook_segment_id="still_03#s001",
        ending_segment_id="still_04#s001",
    )
    ordered = order_for_playback(story, analyses, by_cand)
    assert ordered[0] == "still_03#s001"  # cold open
    assert ordered[-1] == "still_04#s001"
    body = ordered[1:-1]
    # remaining chrono by upload_index
    assert body == ["still_01#s001", "still_02#s001"]


def test_timestamps_within_candidate_range(tmp_path: Path):
    ids = ["still_01#s001", "still_02#s001", "still_03#s001"]
    analyses = [_analysis(s) for s in ids]
    cands = [_cand(s, start=1.0, end=5.0) for s in ids]
    story = _story(ids, target_duration_sec=9.0)
    work = _project(tmp_path, name="range", story=story, analyses=analyses, candidates=cands)
    plan_timeline(work, "range", BGM, force=True)
    edl = load_edl(work / "range" / "edl_ai.json")
    by = {c.segment_id: c for c in cands}
    for clip in edl.timeline:
        cand = by[clip.segment_id]
        assert cand.start_sec <= clip.in_sec <= cand.end_sec
        assert cand.start_sec <= clip.out_sec <= cand.end_sec
        assert clip.out_sec > clip.in_sec


def test_beat_snap(tmp_path: Path):
    ids = ["still_01#s001", "still_02#s001", "still_03#s001"]
    analyses = [_analysis(s) for s in ids]
    cands = [_cand(s) for s in ids]
    story = _story(ids, target_duration_sec=9.0)
    work = _project(tmp_path, name="beats", story=story, analyses=analyses, candidates=cands)
    plan_timeline(work, "beats", BGM, force=True)
    edl = load_edl(work / "beats" / "edl_ai.json")
    beats = json.loads(BGM.with_suffix(".beats.json").read_text(encoding="utf-8"))
    beat_times = extend_beat_grid(beats["beat_times"], beats["tempo_bpm"], 30.0)
    for clip in edl.timeline:
        for t in (clip.in_sec, clip.out_sec):
            nearest = min(beat_times, key=lambda b: abs(b - t))
            assert abs(nearest - t) <= 0.05 + 1e-6


def test_dynamic_length_from_material(tmp_path: Path):
    few = ["still_01#s001", "still_02#s001"]
    many = [f"still_{i:02d}#s001" for i in range(1, 7)]

    def run(name: str, ids: list[str], want: float) -> float:
        analyses = [_analysis(s, upload_index=i) for i, s in enumerate(ids)]
        cands = [_cand(s) for s in ids]
        story = _story(ids, target_duration_sec=want, allow_asset_reuse=False)
        work = _project(tmp_path, name=name, story=story, analyses=analyses, candidates=cands)
        plan_timeline(work, name, BGM, force=True)
        payload = json.loads((work / name / "timeline_plan.json").read_text(encoding="utf-8"))
        return float(payload["adjusted_target_duration_sec"])

    short_t = run("few", few, 40.0)
    long_t = run("many", many, 40.0)
    assert short_t < long_t
    assert long_t >= 15.0
    assert short_t <= adjust_target_duration(40.0, 2, allow_reuse=False) + 0.01


def test_edl_has_new_fields(tmp_path: Path):
    ids = ["still_01#s001", "still_02#s001", "still_03#s001"]
    analyses = [_analysis(s) for s in ids]
    cands = [_cand(s) for s in ids]
    story = _story(ids)
    work = _project(tmp_path, name="fields", story=story, analyses=analyses, candidates=cands)
    plan_timeline(work, "fields", BGM, force=True)
    edl = load_edl(work / "fields" / "edl_ai.json")
    assert edl.style_preset == "clean_vlog"
    clip = edl.timeline[0]
    assert clip.role is not None
    assert clip.evidence_frame_ids
    assert clip.fit_mode
    assert 0.0 <= clip.focus_x <= 1.0
    assert clip.motion
    assert clip.reuse_reason is None
    score = json.loads((work / "fields" / "timeline_score_breakdown.json").read_text(encoding="utf-8"))
    assert "total" in score and "positives" in score


def test_validate_edl_passes(tmp_path: Path):
    ids = ["still_01#s001", "still_02#s001", "still_03#s001"]
    analyses = [_analysis(s) for s in ids]
    cands = [_cand(s) for s in ids]
    story = _story(ids, target_duration_sec=9.0)
    work = _project(tmp_path, name="valid", story=story, analyses=analyses, candidates=cands)
    plan_timeline(work, "valid", BGM, force=True)
    edl = load_edl(work / "valid" / "edl_ai.json")
    cands_file = CandidatesFile.model_validate_json(
        (work / "valid" / "candidates.json").read_text(encoding="utf-8")
    )
    beats = json.loads(BGM.with_suffix(".beats.json").read_text(encoding="utf-8"))
    beat_times = extend_beat_grid(beats["beat_times"], beats["tempo_bpm"], 30.0)
    actual = sum(c.out_sec - c.in_sec for c in edl.timeline)
    problems = validate_edl(edl, cands_file, beat_times, target_duration_s=actual)
    assert problems == []


def test_opening_callback_is_explicit_and_used_once(tmp_path: Path):
    ids = [f"still_{i:02d}#s001" for i in range(1, 6)]
    analyses = [
        _analysis(
            sid,
            upload_index=i,
            hook_potential=0.9 if i == 0 else 0.4,
            emotional_value=0.75 if i == 0 else 0.45,
        )
        for i, sid in enumerate(ids)
    ]
    story = _story(ids, target_duration_sec=12.0)
    work = _project(
        tmp_path,
        name="callback",
        story=story,
        analyses=analyses,
        candidates=[_cand(sid) for sid in ids],
    )
    plan_timeline(work, "callback", BGM, force=True)
    timeline = json.loads((work / "callback" / "timeline_plan.json").read_text(encoding="utf-8"))["plan"]["clips"]
    creative = json.loads((work / "callback" / "creative_execution_plan.json").read_text(encoding="utf-8"))
    assert [clip["segment_id"] for clip in timeline].count(ids[0]) == 2
    assert set(ids).issubset({clip["segment_id"] for clip in timeline})
    assert timeline[-2]["segment_id"] == ids[-1]
    assert timeline[-1]["reuse_reason"] == "opening_callback"
    assert creative["callback"]["enabled"] is True
    assert creative["callback"]["alternate_crop"] is True


def test_opening_caption_survives_callback(tmp_path: Path):
    ids = [f"still_{i:02d}#s001" for i in range(1, 6)]
    analyses = [
        _analysis(
            sid,
            upload_index=i,
            hook_potential=0.9 if i == 0 else 0.4,
            emotional_value=0.75 if i == 0 else 0.45,
            visually_grounded_facts=["person outdoors"],
        )
        for i, sid in enumerate(ids)
    ]
    story = _story(
        ids,
        target_duration_sec=12.0,
        caption_mode=CaptionMode.sparse,
        narrative_arc=["fact: person outdoors"],
    )
    work = _project(
        tmp_path,
        name="caption_callback",
        story=story,
        analyses=analyses,
        candidates=[_cand(sid) for sid in ids],
    )
    plan_timeline(work, "caption_callback", BGM, force=True)
    edl = load_edl(work / "caption_callback" / "edl_ai.json")
    assert edl.captions and edl.captions[0].segment_id == ids[0]
    assert edl.captions[0].style == "title"
    assert edl.captions[0].position == "top"
    assert edl.captions[0].end_offset_sec <= 1.9


@pytest.mark.skipif(not BGM.exists(), reason="demo_track.wav missing")
def test_bgm_fixture_present():
    assert BGM.with_suffix(".beats.json").exists()
