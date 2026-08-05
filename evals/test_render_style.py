"""PROMPT 7 — content-aware vertical Remotion renderer contracts."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from pipeline.ai.schemas import (
    AssetAnalysis,
    FitMode,
    MediaType,
    Mood,
    SceneKind,
    ShotType,
    StylePreset,
)
from pipeline.director import style_from_brief
from pipeline.edl import Caption
from pipeline.plan_timeline import canvas_for_style, fit_mode_for_analysis

REPO = Path(__file__).resolve().parents[1]
CLIP_TSX = REPO / "render" / "src" / "Clip.tsx"
STYLE_TS = REPO / "render" / "src" / "style.ts"
ZLOG_TSX = REPO / "render" / "src" / "ZlogFilm.tsx"
CAPTION_TSX = REPO / "render" / "src" / "Caption.tsx"
ROOT_TSX = REPO / "render" / "src" / "Root.tsx"
FOCUS_ASSERT = REPO / "render" / "scripts" / "assert_focus_crop.mjs"


def test_clip_tsx_does_not_use_order_modulo_motion():
    src = CLIP_TSX.read_text(encoding="utf-8")
    assert "clip.order % 2" not in src
    assert "PAN_DIRS[clip.order % PAN_DIRS.length]" not in src
    assert "clip.motion" in src
    assert "focus_x" in src and "focus_y" in src


def test_style_ts_defaults_to_clean_vlog():
    src = STYLE_TS.read_text(encoding="utf-8")
    assert "clean_vlog" in src
    assert "y2k_camcorder" in src
    # Default branch when preset missing
    assert "edl.style_preset || 'clean_vlog'" in src or 'edl.style_preset || "clean_vlog"' in src


def test_root_default_edl_is_clean_vertical():
    src = ROOT_TSX.read_text(encoding="utf-8")
    assert "style_preset: 'clean_vlog'" in src
    assert "aspect: '9:16'" in src
    assert "camcorder_osd: false" in src


def test_zlogfilm_gates_y2k_kit():
    src = ZLOG_TSX.read_text(encoding="utf-8")
    assert "resolveStyle" in src
    assert "style.camcorderOsd" in src
    assert "style.allowFlash" in src
    assert "style.endingCredit" in src


def test_caption_requires_grounding_and_blocks_brief():
    src = CAPTION_TSX.read_text(encoding="utf-8")
    assert "grounding" in src
    assert "looksLikeEditBrief" in src
    assert "return null" in src


def test_python_canvas_clean_vs_y2k():
    _canvas, frame, aes = canvas_for_style(StylePreset.clean_vlog)
    assert frame.aspect == "9:16"
    assert frame.y_offset == 0
    assert aes.camcorder_osd is False
    assert aes.scanlines is False
    assert aes.allow_flash is False
    assert aes.lut == ""

    _, frame_y, aes_y = canvas_for_style(StylePreset.y2k_camcorder)
    assert frame_y.aspect == "4:3"
    assert aes_y.camcorder_osd is True
    assert aes_y.scanlines is True


def test_low_crop_confidence_uses_blurred_contain():
    a = AssetAnalysis(
        asset_id="a",
        segment_id="s#s001",
        source_file="s.mp4",
        media_type=MediaType.still_video,
        upload_index=0,
        subjects=["person"],
        scene=SceneKind.portrait,
        shot_type=ShotType.medium,
        mood=Mood.calm,
        crop_confidence=0.2,
        analysis_provider="anthropic",
        analysis_model="x",
    )
    assert fit_mode_for_analysis(a, StylePreset.clean_vlog) == FitMode.blurred_background_contain


def test_clip_uses_focus_object_position_helpers():
    clip = CLIP_TSX.read_text(encoding="utf-8")
    style = STYLE_TS.read_text(encoding="utf-8")
    assert "objectPositionCss" in style
    assert "objectFitForMode" in style
    assert "objectPositionCss" in clip
    assert "objectFitForMode" in clip
    assert "blurred_background_contain" in clip


def test_focus_x_changes_object_position_via_node_helper():
    result = subprocess.run(
        ["node", str(FOCUS_ASSERT)],
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok" in result.stdout


def test_director_y2k_maps_to_camcorder_preset():
    assert style_from_brief("y2k ccd look") == StylePreset.y2k_camcorder
    assert style_from_brief("감성적인 유튜브 브이로그") == StylePreset.clean_vlog


def test_caption_model_has_grounding_field():
    cap = Caption(segment_id="a", text="hello", grounding="visually_grounded_facts")
    assert cap.grounding == "visually_grounded_facts"
    assert Caption(segment_id="a", text="x").grounding == ""


def test_fit_mode_enum_includes_new_modes():
    assert FitMode.subject_aware_cover.value == "subject_aware_cover"
    assert FitMode.blurred_background_contain.value == "blurred_background_contain"


def test_style_snapshot_fixture_roundtrip(tmp_path: Path):
    """Frozen JSON snapshot of a clean_vlog EDL shape the renderer expects."""
    edl = {
        "project": "snap",
        "version": "1.0",
        "generator": "ai",
        "style_preset": "clean_vlog",
        "canvas": {"width": 1080, "height": 1920},
        "frame": {"aspect": "9:16", "width": 1080, "height": 1920, "y_offset": 0},
        "aesthetic": {
            "lut": "",
            "grain": 0.0,
            "bloom": 0.0,
            "scanlines": False,
            "camcorder_osd": False,
            "allow_flash": False,
        },
        "audio": {"bgm_id": "demo_track", "start_sec": 0.0, "volume": 0.8},
        "timeline": [
            {
                "order": 1,
                "segment_id": "still_01#s001",
                "source_file": "still_01.mp4",
                "in_sec": 0.0,
                "out_sec": 2.0,
                "transition": "cut",
                "role": "opening",
                "fit_mode": "subject_aware_cover",
                "focus_x": 0.48,
                "focus_y": 0.42,
                "motion": "ken_burns_in",
                "motion_strength": 0.3,
                "crop_confidence": 0.7,
                "evidence_frame_ids": ["still_01#s001#f01"],
            }
        ],
        "captions": [
            {
                "segment_id": "still_01#s001",
                "text": "person outdoors",
                "style": "subtitle",
                "font": "body",
                "position": "bottom",
                "start_offset_sec": 0.1,
                "end_offset_sec": None,
                "grounding": "visually_grounded_facts",
            }
        ],
        "signature": {"enabled": False, "text": "", "duration": 0.0},
    }
    path = tmp_path / "edl_clean_vlog_snapshot.json"
    path.write_text(json.dumps(edl, indent=2), encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["style_preset"] == "clean_vlog"
    assert loaded["frame"]["aspect"] == "9:16"
    assert loaded["timeline"][0]["motion"] == "ken_burns_in"
    assert "order %" not in json.dumps(loaded)
    assert loaded["captions"][0]["grounding"]
    # Ensure Clip source still documents content-aware contract
    assert re.search(r"clip\.motion", CLIP_TSX.read_text(encoding="utf-8"))
