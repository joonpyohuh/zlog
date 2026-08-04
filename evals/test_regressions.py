"""Regression tests for previously observed Zlog failures."""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

from pipeline.ai.schemas import StylePreset
from pipeline.captions import build_captions, build_captions_from_ai
from pipeline.edl import CandidateScene, Quality, Tags, TimelineClip
from pipeline.inspect_edl import format_report, inspect_edl
from pipeline.plan_timeline import canvas_for_style
from pipeline.select_ai import _build_user_content
from pipeline.select_baseline import (
    DEFAULT_CANVAS,
    DEFAULT_FRAME,
    expand_timeline_to_target,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PHOTO_FIXTURE = FIXTURES / "photos_vlog_same_subject"
USER_INTENT = "감성적인 유튜브 브이로그로 만들어줘"


def _cand(segment_id: str, source: str = "still_01.mp4", tags: Tags | None = None) -> CandidateScene:
    return CandidateScene(
        segment_id=segment_id,
        source_file=source,
        start_sec=0.0,
        end_sec=8.0,
        duration=8.0,
        frame_path=f"frames/{segment_id}.jpg",
        quality=Quality(
            blur_score=200.0,
            brightness=0.5,
            phash="0",
            verdict="pass",
            duplicate_of=None,
        ),
        tags=tags,
    )


def _clip(order: int, segment_id: str, source: str, in_sec: float, out_sec: float) -> TimelineClip:
    return TimelineClip(
        order=order,
        segment_id=segment_id,
        source_file=source,
        in_sec=in_sec,
        out_sec=out_sec,
        transition="cut",
    )


def _six_photo_pool() -> list[CandidateScene]:
    return [
        _cand(f"still_{i:02d}#s001", f"still_{i:02d}.mp4")
        for i in range(1, 7)
    ]


# --- fixture integrity -----------------------------------------------------


def test_six_photo_same_subject_fixture_exists():
    manifest = json.loads((PHOTO_FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["user_intent"] == USER_INTENT
    assert len(manifest["photos"]) == 6
    for name in manifest["photos"]:
        path = PHOTO_FIXTURE / name
        assert path.exists() and path.stat().st_size > 1000


# --- 1. raw user intent as caption (current) -------------------------------


def test_opening_caption_does_not_use_raw_intent():
    timeline = [_clip(1, "a#1", "still_01.mp4", 0.0, 2.0)]
    caps = build_captions(timeline, {"a#1": _cand("a#1")}, note=USER_INTENT)
    assert caps[0].style == "title"
    assert caps[0].text != USER_INTENT


def test_current_fixture_edl_exposes_intent_as_title_caption():
    report = inspect_edl(FIXTURES / "edl_intent_caption_and_repeats.json")
    titles = [c["text"] for c in report["captions"] if c["style"] == "title"]
    assert USER_INTENT in titles


def test_desired_opening_caption_is_not_raw_user_intent():
    timeline = [_clip(1, "a#1", "still_01.mp4", 0.0, 2.0)]
    caps = build_captions(timeline, {"a#1": _cand("a#1")}, note=USER_INTENT)
    assert caps[0].text != USER_INTENT


def test_desired_ai_opening_caption_not_overridden_by_note():
    timeline = [_clip(1, "a#1", "still_01.mp4", 0.0, 2.0)]
    selected = [
        {
            "segment_id": "a#1",
            "order": 1,
            "role": "opening",
            "reason": "wide establish",
            "caption": "느린 오후",
        }
    ]
    caps = build_captions_from_ai(timeline, selected, {"a#1": _cand("a#1")}, note=USER_INTENT)
    assert caps[0].text == "느린 오후"


# --- 2. fixed 4:3 letterbox ------------------------------------------------


def test_current_default_frame_is_fixed_4x3_letterbox():
    assert DEFAULT_CANVAS.width == 1080 and DEFAULT_CANVAS.height == 1920
    assert DEFAULT_FRAME.aspect == "4:3"
    assert DEFAULT_FRAME.width == 1080 and DEFAULT_FRAME.height == 810


def test_desired_style_can_escape_fixed_4x3_letterbox():
    _, frame, _ = canvas_for_style(StylePreset.clean_vlog)
    assert frame.aspect == "9:16" and frame.height == 1920


# --- 3. still modulo repetition --------------------------------------------


def test_current_expand_reuses_six_photo_pool_with_modulo():
    pool = _six_photo_pool()
    seed = [
        _clip(i, f"still_{i:02d}#s001", f"still_{i:02d}.mp4", 0.0, 1.0)
        for i in range(1, 3)
    ]
    beats = [i * 0.5 for i in range(48)]
    expanded = expand_timeline_to_target(seed, pool, beats, target_duration_s=12.0, tempo_bpm=120.0)
    sources = [c.source_file for c in expanded]
    assert len(expanded) >= 6
    assert max(sources.count(s) for s in set(sources)) >= 2


def test_current_intent_fixture_reports_repeats():
    report = inspect_edl(FIXTURES / "edl_intent_caption_and_repeats.json")
    assert report["unique_source_count"] == 6
    assert report["repeated_source_count"] >= 4
    assert report["repeated_segment_count"] >= 4
    assert report["total_duration_s"] > 10


def test_desired_no_source_repetition_when_six_stills_available():
    source = (REPO / "pipeline" / "plan_timeline.py").read_text(encoding="utf-8")
    assert "expand_timeline_to_target" not in source


# --- 4. content-independent pan/zoom ---------------------------------------


def test_clip_tsx_motion_is_content_driven_not_order_cycled():
    src = (REPO / "render" / "src" / "Clip.tsx").read_text(encoding="utf-8")
    assert "clip.order % 2" not in src
    assert "PAN_DIRS[clip.order % PAN_DIRS.length]" not in src
    assert "clip.motion" in src


# --- 5. intent not reaching Claude -----------------------------------------


def test_build_user_content_accepts_note_parameter():
    params = inspect.signature(_build_user_content).parameters
    assert "note" in params


def test_select_ai_passes_note_to_claude_call():
    src = (REPO / "pipeline" / "select_ai.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    seen = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "_call_claude":
            continue
        seen = True
        assert len(node.args) >= 11 or "note" in {kw.arg for kw in node.keywords if kw.arg}
    assert seen


def test_desired_build_user_content_accepts_note_or_intent():
    params = inspect.signature(_build_user_content).parameters
    assert "note" in params or "intent" in params or "user_intent" in params


# --- 6. semantic evaluation on product path --------------------------------


def test_evaluator_wired_on_server_product_pipeline():
    server = (REPO / "server.py").read_text(encoding="utf-8")
    assert "run_product_pipeline" in server
    assert (REPO / "pipeline" / "evaluate_plan.py").exists()
    assert "from pipeline import tag" not in server


# --- 7. docs match server / web --------------------------------------------


def test_docs_acknowledge_server_web_pipeline():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    claude = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    assert "웹앱은 아직 없다" not in readme
    assert "server.py" in readme
    assert "server.py" in claude
    assert "product_pipeline" in claude or "hybrid" in claude.lower()


# --- inspect_edl -----------------------------------------------------------


def test_inspect_edl_reports_required_fields_for_intent_fixture():
    report = inspect_edl(FIXTURES / "edl_intent_caption_and_repeats.json")
    required = {
        "unique_source_count",
        "repeated_source_count",
        "repeated_segment_count",
        "clip_durations",
        "total_duration_s",
        "captions",
        "style_preset",
        "frame_dimensions",
        "generator_and_model",
    }
    assert required.issubset(report.keys())
    assert report["generator_and_model"]["generator"] == "ai"
    assert "4x3" in report["style_preset"] or "4:3" in report["style_preset"]
    text = format_report(report)
    assert "style_preset" in text
    assert "total_duration_s" in text
    assert USER_INTENT in text


def test_production_path_doc_lists_intermediate_artifacts():
    body = (REPO / "evals" / "production_path.md").read_text(encoding="utf-8")
    for token in (
        "server.py",
        "segments.json",
        "candidates.json",
        "edl_ai.json",
        "render.mp4",
        "final.mp4",
        "Remotion",
    ):
        assert token in body
