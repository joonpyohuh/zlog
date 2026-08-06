"""Photo → aspect-preserving H.264 proxy (no 9:16 pre-crop).

Web uploads used to scale+crop to 1080×1920 before Claude/Remotion, which
destroyed edge subjects and made focus_x/focus_y meaningless. Proxies now keep
the original aspect; Remotion applies subject-aware 9:16 cover at render time.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

DEFAULT_MAX_EDGE = 2048
ENV_MAX_EDGE = "ZLOG_STILL_PROXY_MAX_EDGE"


def still_proxy_max_edge() -> int:
    raw = os.getenv(ENV_MAX_EDGE, str(DEFAULT_MAX_EDGE)).strip()
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_MAX_EDGE
    return max(64, n)


def oriented_image_size(image: Path) -> tuple[int, int]:
    """Pixel size after EXIF orientation (display size)."""
    with Image.open(image) as im:
        im = ImageOps.exif_transpose(im)
        return int(im.width), int(im.height)


def proxy_dimensions(width: int, height: int, max_edge: int | None = None) -> tuple[int, int]:
    """Scale so the long edge ≤ max_edge; both sides even for yuv420p."""
    max_edge = still_proxy_max_edge() if max_edge is None else max(64, int(max_edge))
    w = max(1, int(width))
    h = max(1, int(height))
    long = max(w, h)
    scale = min(1.0, max_edge / long) if long else 1.0
    pw = round(w * scale)
    ph = round(h * scale)
    pw = max(2, pw - (pw % 2))
    ph = max(2, ph - (ph % 2))
    return pw, ph


def ffprobe_video_size(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {result.stderr}")
    line = (result.stdout or "").strip().splitlines()[0]
    w_s, h_s = line.split("x", 1)
    return int(w_s), int(h_s)


def _run_ffmpeg(cmd: list[str]) -> None:
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(cmd)}\n{result.stdout}\n{result.stderr}"
        )


def image_to_proxy_clip(
    image: Path,
    out: Path,
    seconds: float,
    *,
    max_edge: int | None = None,
    upload_index: int = 0,
) -> dict[str, Any]:
    """Encode a looped still proxy that preserves aspect ratio (no center crop).

    Uses a Pillow EXIF-normalized temporary PNG so orientation is applied once
    and manifest dimensions match the encoded file.
    """
    max_edge = still_proxy_max_edge() if max_edge is None else max(64, int(max_edge))
    seconds = max(0.5, float(seconds))
    out.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(image) as im:
        im = ImageOps.exif_transpose(im.convert("RGB"))
        ow, oh = int(im.width), int(im.height)
        pw, ph = proxy_dimensions(ow, oh, max_edge)
        tmp = out.with_suffix(".proxy_src.png")
        try:
            if (ow, oh) != (pw, ph):
                im = im.resize((pw, ph), Image.Resampling.LANCZOS)
            im.save(tmp, format="PNG")
            _run_ffmpeg(
                [
                    "ffmpeg",
                    "-y",
                    "-loop",
                    "1",
                    "-t",
                    f"{seconds:.2f}",
                    "-i",
                    str(tmp),
                    "-vf",
                    f"scale={pw}:{ph}",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-r",
                    "30",
                    "-an",
                    str(out),
                ]
            )
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)

    # Trust encoded stream (guards against odd encoder rounding).
    enc_w, enc_h = ffprobe_video_size(out)
    aspect = round(ow / oh, 6) if oh else 0.0
    return {
        "proxy_file": out.name,
        "original_file": image.name,
        "media_type": "image",
        "upload_index": int(upload_index),
        "original_width": ow,
        "original_height": oh,
        "proxy_width": enc_w,
        "proxy_height": enc_h,
        "aspect_ratio": aspect,
    }


def write_source_manifest(
    footage_dir: Path,
    items: list[dict[str, Any]],
    *,
    project: str | None = None,
) -> Path:
    """Write footage/<project>/source_manifest.json (provenance only)."""
    path = footage_dir / "source_manifest.json"
    payload = {
        "project": project or footage_dir.name,
        "still_proxy_max_edge": still_proxy_max_edge(),
        "items": items,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
