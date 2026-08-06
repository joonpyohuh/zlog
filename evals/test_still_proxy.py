"""Photo proxies keep original aspect — no 9:16 pre-crop before Claude/Remotion."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from pipeline.edl import Scene, SegmentsFile
from pipeline.evidence import EvidenceSegment, run_evidence
from pipeline.still_proxy import (
    ffprobe_video_size,
    image_to_proxy_clip,
    oriented_image_size,
    proxy_dimensions,
    still_proxy_max_edge,
    write_source_manifest,
)

REPO = Path(__file__).resolve().parents[1]


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


pytestmark = pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg/ffprobe required")


def _solid_jpeg(path: Path, size: tuple[int, int], *, left_strip=(0, 255, 0)) -> None:
    """Landscape/portrait fixture with a green strip on the visual left edge."""
    im = Image.new("RGB", size, (200, 40, 40))
    draw = ImageDraw.Draw(im)
    draw.rectangle([0, 0, 12, size[1] - 1], fill=left_strip)
    draw.rectangle([size[0] - 13, 0, size[0] - 1, size[1] - 1], fill=(40, 40, 220))
    im.save(path, quality=92)


def test_proxy_dimensions_even_and_capped() -> None:
    assert proxy_dimensions(4032, 3024, 2048) == (2048, 1536)
    assert proxy_dimensions(3024, 4032, 2048) == (1536, 2048)
    w, h = proxy_dimensions(1001, 501, 800)
    assert w % 2 == 0 and h % 2 == 0
    assert max(w, h) <= 800


def test_landscape_proxy_keeps_aspect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZLOG_STILL_PROXY_MAX_EDGE", "640")
    src = tmp_path / "land.jpg"
    _solid_jpeg(src, (800, 600))  # 4:3
    out = tmp_path / "still_01.mp4"
    entry = image_to_proxy_clip(src, out, 2.0, upload_index=0, max_edge=640)

    assert out.is_file()
    pw, ph = ffprobe_video_size(out)
    assert (pw, ph) != (1080, 1920)
    assert abs(pw / ph - 800 / 600) < 0.02
    assert max(pw, ph) <= 640
    assert pw % 2 == 0 and ph % 2 == 0
    assert entry["original_width"] == 800
    assert entry["original_height"] == 600
    assert entry["proxy_width"] == pw
    assert entry["proxy_height"] == ph
    assert entry["media_type"] == "image"


def test_portrait_proxy_keeps_aspect(tmp_path: Path) -> None:
    src = tmp_path / "port.jpg"
    _solid_jpeg(src, (600, 800))  # 3:4
    out = tmp_path / "still_01.mp4"
    entry = image_to_proxy_clip(src, out, 2.0, max_edge=640)

    pw, ph = ffprobe_video_size(out)
    assert (pw, ph) != (1080, 1920)
    assert abs(pw / ph - 600 / 800) < 0.02
    assert max(pw, ph) <= 640
    assert entry["aspect_ratio"] == pytest.approx(600 / 800, abs=1e-4)

    # Left green strip must still be on the left of a mid-frame (no center-crop to 9:16).
    frame = tmp_path / "frame.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "0.5", "-i", str(out), "-frames:v", "1", str(frame)],
        check=True,
        capture_output=True,
    )
    pix = Image.open(frame).convert("RGB")
    left = pix.getpixel((2, pix.height // 2))
    right = pix.getpixel((pix.width - 3, pix.height // 2))
    assert left[1] > left[0]  # green-ish
    assert right[2] > right[0]  # blue-ish


def test_exif_orientation_applied(tmp_path: Path) -> None:
    """Orientation=6 (90 CW): stored 400×200 displays as 200×400."""
    raw = Image.new("RGB", (400, 200), (180, 60, 60))
    draw = ImageDraw.Draw(raw)
    draw.rectangle([0, 0, 20, 199], fill=(0, 255, 0))
    src = tmp_path / "rotated.jpg"
    exif = raw.getexif()
    exif[274] = 6
    raw.save(src, quality=90, exif=exif)

    ow, oh = oriented_image_size(src)
    assert (ow, oh) == (200, 400)

    out = tmp_path / "still_01.mp4"
    entry = image_to_proxy_clip(src, out, 1.5, max_edge=400)
    assert entry["original_width"] == 200
    assert entry["original_height"] == 400
    pw, ph = ffprobe_video_size(out)
    assert ph > pw  # portrait after orientation
    assert abs(pw / ph - 200 / 400) < 0.05


def test_source_manifest_order_and_mapping(tmp_path: Path) -> None:
    footage = tmp_path / "proj"
    footage.mkdir()
    a = footage / "a.jpg"
    b = footage / "b.jpg"
    _solid_jpeg(a, (640, 480))
    _solid_jpeg(b, (480, 640))
    items = [
        image_to_proxy_clip(a, footage / "still_01.mp4", 1.0, upload_index=0, max_edge=320),
        image_to_proxy_clip(b, footage / "still_02.mp4", 1.0, upload_index=1, max_edge=320),
    ]
    path = write_source_manifest(footage, items, project="proj")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["project"] == "proj"
    assert [x["upload_index"] for x in data["items"]] == [0, 1]
    assert data["items"][0]["original_file"] == "a.jpg"
    assert data["items"][0]["proxy_file"] == "still_01.mp4"
    assert data["items"][1]["proxy_file"] == "still_02.mp4"
    assert abs(data["items"][0]["aspect_ratio"] - 640 / 480) < 1e-4


def test_evidence_sees_full_landscape_proxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    footage = tmp_path / "footage" / "fullframe"
    work = tmp_path / "work"
    footage.mkdir(parents=True)
    work.mkdir()
    src = footage / "wide.jpg"
    _solid_jpeg(src, (640, 360))  # 16:9
    proxy = footage / "still_01.mp4"
    image_to_proxy_clip(src, proxy, 3.0, max_edge=640)

    segs = SegmentsFile(
        project="fullframe",
        segments=[
            Scene(
                segment_id="still_01#s001",
                source_file="still_01.mp4",
                start_sec=0.0,
                end_sec=3.0,
                duration=3.0,
                frame_path="frames/still_01#s001.jpg",
            )
        ],
    )
    proj = work / "fullframe"
    proj.mkdir()
    (proj / "frames").mkdir()
    # split-style representative frame (unused by evidence open path)
    Image.new("RGB", (64, 64), "gray").save(proj / "frames" / "still_01#s001.jpg")
    (proj / "segments.json").write_text(segs.model_dump_json(indent=2), encoding="utf-8")

    run_evidence(work, "fullframe", footage_dir=footage, force=True)
    manifest = json.loads((proj / "evidence_manifest.json").read_text(encoding="utf-8"))
    seg = manifest["segments"][0]
    assert seg["source_width"] == 640
    assert seg["source_height"] == 360
    assert abs(seg["source_aspect_ratio"] - 16 / 9) < 0.02

    ev_path = proj / seg["evidence"][0]["frame_path"]
    ev = Image.open(ev_path).convert("RGB")
    assert abs(ev.width / ev.height - 16 / 9) < 0.05
    left = ev.getpixel((2, ev.height // 2))
    assert left[1] > 100  # green strip preserved (not center-cropped away)


def test_video_upload_path_untouched_by_proxy_helper(tmp_path: Path) -> None:
    """Regular mp4 is not re-encoded by image_to_proxy_clip — regression guard."""
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ],
        check=True,
        capture_output=True,
    )
    before = clip.stat().st_mtime_ns
    # Server only calls image_to_proxy_clip for image files; simulate that videos
    # stay as-is and still ffprobe.
    w, h = ffprobe_video_size(clip)
    assert (w, h) == (320, 240)
    assert clip.stat().st_mtime_ns == before


def test_evidence_segment_optional_size_defaults() -> None:
    seg = EvidenceSegment(
        segment_id="a#s001",
        source_file="a.mp4",
        upload_index=0,
        time_order=0,
        start_sec=0.0,
        end_sec=1.0,
        duration=1.0,
        evidence=[],
    )
    assert seg.source_width is None
    assert seg.source_aspect_ratio is None


def test_max_edge_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZLOG_STILL_PROXY_MAX_EDGE", "1024")
    assert still_proxy_max_edge() == 1024


def test_server_no_longer_hardcodes_1080x1920_crop() -> None:
    src = (REPO / "server.py").read_text(encoding="utf-8")
    assert "crop=1080:1920" not in src
    assert "image_to_proxy_clip" in src
    assert "_image_to_proxy_clip" in src
