"""Adaptive multimodal evidence extraction (PROMPT 2).

Reads:  work/<project>/segments.json   (PySceneDetect timestamps — never rewritten)
        footage/<project>/*             (source media + optional upload_order.json)
Writes: work/<project>/evidence/<segment_id>#fNN.jpg
        work/<project>/evidence_manifest.json
        work/<project>/deterministic_features.json
        work/<project>/evidence_sheets/sheet_*.jpg
        work/<project>/evidence_sheet_manifest.json

No AI API calls. Timecodes stay owned by split.py; this stage only samples
frames *inside* each segment's existing [start_sec, end_sec] window.
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import click
import cv2
import imagehash
import librosa
import numpy as np
from PIL import ExifTags, Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from pipeline.edl import Scene, SegmentsFile

THUMB_LONG_EDGE = 1024
DEFAULT_RELS = (0.2, 0.5, 0.8)
PROBE_COUNT = 9
MAX_EXTRA_FRAMES = 2
# Mean Farneback magnitude (px/frame) + phash distance (0..1) thresholds.
STATIC_MOTION = 0.35
STATIC_PERCEPTUAL = 0.08
HIGH_MOTION = 1.8
HIGH_PERCEPTUAL = 0.22
CELLS_PER_SHEET = 12
COLS = 3
CELL = 280
LABEL_H = 72
MARGIN = 12
BG = (12, 12, 12)
FG = (240, 240, 240)
MUTED = (160, 160, 160)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov"}
STILL_RE = re.compile(r"^still_(\d+)", re.IGNORECASE)


class EvidenceFrame(BaseModel):
    evidence_id: str
    frame_path: str
    offset_sec: float  # absolute seconds in source file
    rel_pos: float = Field(ge=0.0, le=1.0)
    role: Literal["default", "extra", "representative"]


class EvidenceSegment(BaseModel):
    segment_id: str
    source_file: str
    upload_index: int = Field(ge=0)
    capture_time: str | None = None
    time_order: int = Field(ge=0)
    start_sec: float
    end_sec: float
    duration: float
    evidence: list[EvidenceFrame]
    # Optional proxy frame size (full-bleed stills / video) — defaults keep old fixtures valid
    source_width: int | None = None
    source_height: int | None = None
    source_aspect_ratio: float | None = None


class SourceMeta(BaseModel):
    source_file: str
    upload_index: int = Field(ge=0)
    capture_time: str | None = None
    time_order: int = Field(ge=0)
    capture_time_trusted: bool = False


class EvidenceManifest(BaseModel):
    project: str
    sources: list[SourceMeta] = Field(default_factory=list)
    segments: list[EvidenceSegment] = Field(default_factory=list)


class FrameFeatures(BaseModel):
    evidence_id: str
    blur: float
    brightness: float = Field(ge=0.0, le=1.0)
    contrast: float = Field(ge=0.0, le=1.0)
    motion_magnitude: float = Field(ge=0.0)
    perceptual_distance_prev: float = Field(ge=0.0, le=1.0)


class SegmentFeatures(BaseModel):
    segment_id: str
    duration_sec: float
    audio_rms: float = Field(ge=0.0)
    onset_density: float = Field(ge=0.0)
    mean_motion: float = Field(ge=0.0)
    mean_perceptual_distance: float = Field(ge=0.0, le=1.0)
    is_static: bool
    is_high_motion: bool
    frames: list[FrameFeatures] = Field(default_factory=list)


class DeterministicFeaturesFile(BaseModel):
    project: str
    segments: list[SegmentFeatures] = Field(default_factory=list)


def evidence_id_for(segment_id: str, index: int) -> str:
    """Stable evidence id: <segment_id>#f01 … (1-based, temporal order)."""
    if index < 1:
        raise ValueError("evidence index must be >= 1")
    return f"{segment_id}#f{index:02d}"


def write_upload_order(footage_dir: Path, filenames: list[str]) -> Path:
    """Persist upload order explicitly — never infer chronology from alpha sort."""
    footage_dir.mkdir(parents=True, exist_ok=True)
    out = footage_dir / "upload_order.json"
    out.write_text(json.dumps({"files": list(filenames)}, indent=2), encoding="utf-8")
    return out


def load_upload_order(footage_dir: Path | None, project_dir: Path | None = None) -> list[str] | None:
    for base in (footage_dir, project_dir):
        if base is None:
            continue
        path = base / "upload_order.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            files = data.get("files")
            if isinstance(files, list) and all(isinstance(x, str) for x in files):
                return list(files)
    return None


def _parse_exif_datetime(raw: str) -> datetime | None:
    raw = raw.strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            # EXIF timestamps are naive by format; compare as naive wall time.
            return datetime.strptime(raw, fmt)  # noqa: DTZ007
        except ValueError:
            continue
    return None


def _exif_trusted(dt: datetime, *, now: datetime | None = None) -> bool:
    """Reject obviously bogus EXIF clocks; otherwise trust DateTimeOriginal."""
    now = now or datetime.now(UTC).replace(tzinfo=None)
    if dt.year < 1995 or dt > now.replace(hour=23, minute=59, second=59):
        return False
    return not (dt.year == 1970 and dt.month == 1 and dt.day == 1)


def read_capture_time(path: Path) -> tuple[str | None, bool]:
    """Return (ISO8601 local naive string, trusted) from image EXIF if usable."""
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        return None, False
    try:
        img = Image.open(path)
    except OSError:
        return None, False

    try:
        exif = img.getexif()
    except Exception:
        return None, False
    if not exif:
        return None, False

    # Prefer DateTimeOriginal (36867) then DateTimeDigitized / DateTime.
    candidates: list[str] = []
    tag_map = {ExifTags.Base.DateTimeOriginal: None, ExifTags.Base.DateTimeDigitized: None, ExifTags.Base.DateTime: None}
    for tag_id in tag_map:
        val = exif.get(tag_id)
        if isinstance(val, str) and val.strip():
            candidates.append(val)
    try:
        ifd = exif.get_ifd(0x8769)  # Exif IFD
        for tag_id in (36867, 36868, 306):
            val = ifd.get(tag_id)
            if isinstance(val, str) and val.strip():
                candidates.append(val)
    except Exception:
        pass

    for raw in candidates:
        dt = _parse_exif_datetime(raw)
        if dt is None:
            continue
        if _exif_trusted(dt):
            return dt.isoformat(sep="T", timespec="seconds"), True
        return None, False
    return None, False


def _image_for_source(
    source_file: str,
    upload_index: int,
    footage_dir: Path | None,
    upload_order: list[str] | None,
) -> Path | None:
    """Best-effort path to an image that may carry EXIF for this source."""
    if footage_dir is None or not footage_dir.exists():
        return None
    if upload_order and 0 <= upload_index < len(upload_order):
        cand = footage_dir / upload_order[upload_index]
        if cand.is_file() and cand.suffix.lower() in IMAGE_EXTENSIONS:
            return cand
    direct = footage_dir / source_file
    if direct.is_file() and direct.suffix.lower() in IMAGE_EXTENSIONS:
        return direct
    sibling = footage_dir / f"{Path(source_file).stem}.jpg"
    if sibling.is_file():
        return sibling
    return None


def resolve_source_meta(
    source_files: list[str],
    footage_dir: Path | None,
    upload_order: list[str] | None,
) -> dict[str, SourceMeta]:
    """Map each source_file → upload_index / capture_time / time_order.

    Chronology: trusted EXIF only when *every* source has trusted EXIF;
    otherwise upload_index. Never sorts by filename alphabetically.
    """
    index_by_name: dict[str, int] = {}
    if upload_order:
        for i, name in enumerate(upload_order):
            index_by_name[name] = i
            # server.py writes still_01.mp4 for upload_order[0], still_02 for [1], …
            index_by_name[f"still_{i + 1:02d}.mp4"] = i
            index_by_name[f"still_{i + 1:02d}.mov"] = i

    def _fallback_index(name: str, discovery_i: int) -> int:
        if name in index_by_name:
            return index_by_name[name]
        m = STILL_RE.match(Path(name).stem)
        if m:
            # still_01 → upload_index 0 when no explicit order file
            return max(0, int(m.group(1)) - 1)
        return discovery_i

    metas: dict[str, SourceMeta] = {}
    for discovery_i, source_file in enumerate(source_files):
        upload_index = _fallback_index(source_file, discovery_i)
        capture_time: str | None = None
        trusted = False
        img = _image_for_source(source_file, upload_index, footage_dir, upload_order)
        if img is not None:
            capture_time, trusted = read_capture_time(img)
        metas[source_file] = SourceMeta(
            source_file=source_file,
            upload_index=upload_index,
            capture_time=capture_time if trusted else None,
            time_order=upload_index,
            capture_time_trusted=bool(trusted and capture_time),
        )

    all_trusted = bool(metas) and all(m.capture_time_trusted for m in metas.values())
    if all_trusted:
        ordered = sorted(metas.values(), key=lambda m: (m.capture_time or "", m.upload_index))
        for i, m in enumerate(ordered):
            metas[m.source_file] = m.model_copy(update={"time_order": i})
    else:
        for m in metas.values():
            metas[m.source_file] = m.model_copy(update={"time_order": m.upload_index})
    return metas

def _unique_frame_indices(start_f: int, end_f: int, rels: tuple[float, ...]) -> list[tuple[int, float]]:
    span = max(0, end_f - start_f)
    if span <= 1:
        return [(start_f, 0.5)]
    out: list[tuple[int, float]] = []
    seen: set[int] = set()
    for rel in rels:
        f = start_f + int(round(span * rel))
        f = min(max(f, start_f), end_f - 1)
        if f not in seen:
            seen.add(f)
            out.append((f, float(rel)))
    return out


def _probe_indices(start_f: int, end_f: int, count: int = PROBE_COUNT) -> list[int]:
    span = max(0, end_f - start_f)
    if span <= 1:
        return [start_f]
    n = min(count, span)
    if n == 1:
        return [start_f]
    return [start_f + int(round(i * (span - 1) / (n - 1))) for i in range(n)]


def _read_frame(cap: cv2.VideoCapture, frame_idx: int) -> np.ndarray:
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, bgr = cap.read()
    if not ok or bgr is None:
        raise RuntimeError(f"could not read frame {frame_idx}")
    return bgr


def _save_thumb(bgr: np.ndarray, out_path: Path) -> None:
    img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    scale = THUMB_LONG_EDGE / max(img.size)
    if scale < 1:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, quality=90)


def _frame_stats(bgr: np.ndarray) -> tuple[float, float, float, imagehash.ImageHash]:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean() / 255.0)
    contrast = float(gray.std() / 255.0)
    phash = imagehash.phash(Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)))
    return blur, brightness, contrast, phash


def _flow_mag(prev_gray: np.ndarray, gray: np.ndarray) -> float:
    flow = cv2.calcOpticalFlowFarneback(prev_gray, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    mag = np.linalg.norm(flow, axis=2)
    return float(mag.mean())


def _phash_distance(a: imagehash.ImageHash, b: imagehash.ImageHash) -> float:
    # 64-bit phash → normalize Hamming to [0, 1]
    return float(a - b) / 64.0


def _audio_features(video_path: Path, start_sec: float, duration: float) -> tuple[float, float]:
    if duration <= 0:
        return 0.0, 0.0
    import warnings

    try:
        # Many phone-stills→mp4 clips have no audio track; swallow decoder noise.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            y, sr = librosa.load(
                str(video_path),
                sr=22050,
                mono=True,
                offset=max(0.0, start_sec),
                duration=duration,
            )
    except Exception:
        return 0.0, 0.0
    if y.size == 0:
        return 0.0, 0.0
    rms = float(np.sqrt(np.mean(np.square(y.astype(np.float64)))))
    try:
        onset_env = librosa.onset.onset_strength(y=y, sr=sr)
        onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr, units="time")
        density = float(len(np.atleast_1d(onsets)) / duration)
    except Exception:
        density = 0.0
    return max(0.0, rms), max(0.0, density)


def _select_frame_indices(
    start_f: int,
    end_f: int,
    fps: float,
    probe_bgrs: dict[int, np.ndarray],
    probe_stats: dict[int, tuple[float, float, float, imagehash.ImageHash]],
    motions: list[float],
    perceptuals: list[float],
) -> list[tuple[int, float, Literal["default", "extra", "representative"]]]:
    """Return [(frame_idx, rel_pos, role), ...] in temporal order (ids assigned later)."""
    defaults = _unique_frame_indices(start_f, end_f, DEFAULT_RELS)
    mean_motion = float(np.mean(motions)) if motions else 0.0
    mean_perc = float(np.mean(perceptuals)) if perceptuals else 0.0
    is_static = mean_motion <= STATIC_MOTION and mean_perc <= STATIC_PERCEPTUAL
    is_high = mean_motion >= HIGH_MOTION or mean_perc >= HIGH_PERCEPTUAL

    if is_static:
        # Keep the sharpest default (prefer mid on ties).
        best_f, best_rel = max(
            defaults,
            key=lambda fr: (probe_stats[fr[0]][0], -abs(fr[1] - 0.5)),
        )
        return [(best_f, best_rel, "representative")]

    chosen: dict[int, tuple[float, Literal["default", "extra", "representative"]]] = {
        f: (rel, "default") for f, rel in defaults
    }

    if is_high:
        # Score probe frames by local change; add up to two not already chosen.
        scored: list[tuple[float, int]] = []
        probe_fs = sorted(probe_bgrs.keys())
        for i, f in enumerate(probe_fs):
            if f in chosen:
                continue
            motion = motions[i - 1] if i > 0 and i - 1 < len(motions) else 0.0
            perc = perceptuals[i - 1] if i > 0 and i - 1 < len(perceptuals) else 0.0
            # Prefer interior change peaks.
            scored.append((motion + 4.0 * perc, f))
        scored.sort(key=lambda t: t[0], reverse=True)
        span = max(1, end_f - start_f)
        for _, f in scored[:MAX_EXTRA_FRAMES]:
            rel = (f - start_f) / span
            chosen[f] = (float(rel), "extra")

    ordered = sorted(chosen.items(), key=lambda kv: kv[0])
    return [(f, rel, role) for f, (rel, role) in ordered]


def extract_segment_evidence(
    cap: cv2.VideoCapture,
    scene: Scene,
    fps: float,
    out_dir: Path,
) -> tuple[list[EvidenceFrame], SegmentFeatures]:
    start_f = int(round(scene.start_sec * fps))
    end_f = int(round(scene.end_sec * fps))
    if end_f <= start_f:
        end_f = start_f + 1

    probe_fs = _probe_indices(start_f, end_f)
    # Ensure defaults are among probes for stats.
    for f, _ in _unique_frame_indices(start_f, end_f, DEFAULT_RELS):
        if f not in probe_fs:
            probe_fs.append(f)
    probe_fs = sorted(set(probe_fs))

    probe_bgrs: dict[int, np.ndarray] = {}
    probe_stats: dict[int, tuple[float, float, float, imagehash.ImageHash]] = {}
    for f in probe_fs:
        bgr = _read_frame(cap, f)
        probe_bgrs[f] = bgr
        probe_stats[f] = _frame_stats(bgr)

    motions: list[float] = []
    perceptuals: list[float] = []
    prev_gray = None
    prev_hash = None
    for f in probe_fs:
        gray = cv2.cvtColor(probe_bgrs[f], cv2.COLOR_BGR2GRAY)
        ph = probe_stats[f][3]
        if prev_gray is not None and prev_hash is not None:
            motions.append(_flow_mag(prev_gray, gray))
            perceptuals.append(_phash_distance(prev_hash, ph))
        prev_gray = gray
        prev_hash = ph

    selected = _select_frame_indices(
        start_f, end_f, fps, probe_bgrs, probe_stats, motions, perceptuals
    )

    evidence: list[EvidenceFrame] = []
    frame_feats: list[FrameFeatures] = []
    prev_sel_hash: imagehash.ImageHash | None = None
    for i, (f, rel, role) in enumerate(selected, start=1):
        eid = evidence_id_for(scene.segment_id, i)
        rel_path = f"evidence/{eid}.jpg"
        if f not in probe_bgrs:
            probe_bgrs[f] = _read_frame(cap, f)
            probe_stats[f] = _frame_stats(probe_bgrs[f])
        _save_thumb(probe_bgrs[f], out_dir / rel_path)
        blur, bright, contrast, ph = probe_stats[f]
        perc_prev = _phash_distance(prev_sel_hash, ph) if prev_sel_hash is not None else 0.0
        prev_sel_hash = ph
        # Motion for selected frame: nearest probe-gap magnitude
        motion = 0.0
        if f in probe_fs:
            idx = probe_fs.index(f)
            if idx > 0 and idx - 1 < len(motions):
                motion = motions[idx - 1]
        offset_sec = round(f / fps, 3)
        evidence.append(
            EvidenceFrame(
                evidence_id=eid,
                frame_path=rel_path,
                offset_sec=offset_sec,
                rel_pos=round(rel, 4),
                role=role,
            )
        )
        frame_feats.append(
            FrameFeatures(
                evidence_id=eid,
                blur=round(blur, 3),
                brightness=round(min(1.0, max(0.0, bright)), 4),
                contrast=round(min(1.0, max(0.0, contrast)), 4),
                motion_magnitude=round(max(0.0, motion), 4),
                perceptual_distance_prev=round(min(1.0, max(0.0, perc_prev)), 4),
            )
        )

    mean_motion = float(np.mean(motions)) if motions else 0.0
    mean_perc = float(np.mean(perceptuals)) if perceptuals else 0.0
    feats = SegmentFeatures(
        segment_id=scene.segment_id,
        duration_sec=scene.duration,
        audio_rms=0.0,  # filled by caller with path
        onset_density=0.0,
        mean_motion=round(mean_motion, 4),
        mean_perceptual_distance=round(min(1.0, max(0.0, mean_perc)), 4),
        is_static=mean_motion <= STATIC_MOTION and mean_perc <= STATIC_PERCEPTUAL,
        is_high_motion=mean_motion >= HIGH_MOTION or mean_perc >= HIGH_PERCEPTUAL,
        frames=frame_feats,
    )
    return evidence, feats


def build_evidence_contact_sheets(
    project_dir: Path,
    manifest: EvidenceManifest,
) -> Path:
    """Grid of evidence frames labeled with evidence_id / segment / source / order."""
    pool: list[tuple[EvidenceSegment, EvidenceFrame]] = []
    # Sort by time_order then segment start then evidence offset — temporal + upload.
    segs = sorted(
        manifest.segments,
        key=lambda s: (s.time_order, s.start_sec, s.segment_id),
    )
    for seg in segs:
        for ev in seg.evidence:
            pool.append((seg, ev))

    sheets_dir = project_dir / "evidence_sheets"
    if sheets_dir.exists():
        for old in sheets_dir.glob("sheet_*.jpg"):
            old.unlink()
    sheets_dir.mkdir(parents=True, exist_ok=True)

    try:
        font_big = ImageFont.truetype("arial.ttf", 22)
        font_small = ImageFont.truetype("arial.ttf", 13)
    except OSError:
        font_big = ImageFont.load_default()
        font_small = font_big

    sheet_manifest: dict[str, dict[str, Any]] = {}
    n_sheets = max(1, math.ceil(len(pool) / CELLS_PER_SHEET)) if pool else 1

    if not pool:
        out = project_dir / "evidence_sheet_manifest.json"
        out.write_text(
            json.dumps({"project": manifest.project, "manifest": {}}, indent=2),
            encoding="utf-8",
        )
        return out

    for sheet_i in range(n_sheets):
        chunk = pool[sheet_i * CELLS_PER_SHEET : (sheet_i + 1) * CELLS_PER_SHEET]
        rows = math.ceil(len(chunk) / COLS)
        sheet_name = f"sheet_{sheet_i + 1:03d}.jpg"
        cell_w = CELL + MARGIN
        cell_h = CELL + LABEL_H + MARGIN
        w = COLS * cell_w + MARGIN
        h = rows * cell_h + MARGIN
        canvas = Image.new("RGB", (w, h), BG)
        draw = ImageDraw.Draw(canvas)

        for local_i, (seg, ev) in enumerate(chunk):
            index = local_i + 1
            col, row = local_i % COLS, local_i // COLS
            x = MARGIN + col * cell_w
            y = MARGIN + row * cell_h

            thumb = Image.open(project_dir / ev.frame_path).convert("RGB")
            thumb.thumbnail((CELL, CELL), Image.LANCZOS)
            px = x + (CELL - thumb.width) // 2
            py = y + (CELL - thumb.height) // 2
            canvas.paste(thumb, (px, py))

            draw.rectangle((x, y + CELL, x + CELL, y + CELL + LABEL_H), fill=(20, 20, 20))
            order_label = f"t{seg.time_order}/u{seg.upload_index}"
            draw.text((x + 6, y + CELL + 4), ev.evidence_id, fill=FG, font=font_big)
            draw.text((x + 6, y + CELL + 28), seg.segment_id, fill=MUTED, font=font_small)
            draw.text(
                (x + 6, y + CELL + 44),
                f"{seg.source_file}  {order_label}",
                fill=MUTED,
                font=font_small,
            )

            sheet_manifest[ev.evidence_id] = {
                "sheet": sheet_name,
                "index": index,
                "row": row,
                "col": col,
                "segment_id": seg.segment_id,
                "source_file": seg.source_file,
                "upload_index": seg.upload_index,
                "time_order": seg.time_order,
            }

        canvas.save(sheets_dir / sheet_name, quality=90)

    out = project_dir / "evidence_sheet_manifest.json"
    out.write_text(
        json.dumps({"project": manifest.project, "manifest": sheet_manifest}, indent=2),
        encoding="utf-8",
    )
    return out


def run_evidence(
    work_dir: Path,
    project: str,
    footage_dir: Path | None = None,
    force: bool = False,
) -> Path:
    """Extract adaptive evidence frames + deterministic features for a project."""
    project_dir = work_dir / project
    segments_path = project_dir / "segments.json"
    if not segments_path.exists():
        raise FileNotFoundError(segments_path)

    manifest_path = project_dir / "evidence_manifest.json"
    features_path = project_dir / "deterministic_features.json"
    if manifest_path.exists() and features_path.exists() and not force:
        click.echo(f"{manifest_path} exists, skipping (use --force to overwrite)")
        return manifest_path

    segments_file = SegmentsFile.model_validate_json(segments_path.read_text(encoding="utf-8"))
    if footage_dir is None:
        # Conventional layout: footage/<project>
        guess = Path("footage") / project
        footage_dir = guess if guess.exists() else None

    upload_order = load_upload_order(footage_dir, project_dir)
    # Discovery order = first appearance in segments.json (split order), NOT re-sorted alpha here.
    source_files: list[str] = []
    for s in segments_file.segments:
        if s.source_file not in source_files:
            source_files.append(s.source_file)

    source_meta = resolve_source_meta(source_files, footage_dir, upload_order)

    evidence_dir = project_dir / "evidence"
    if force and evidence_dir.exists():
        for old in evidence_dir.glob("*.jpg"):
            old.unlink()
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # Open each source video once.
    by_source: dict[str, list[Scene]] = {}
    for scene in segments_file.segments:
        by_source.setdefault(scene.source_file, []).append(scene)

    out_segments: list[EvidenceSegment] = []
    out_features: list[SegmentFeatures] = []

    for source_file, scenes in by_source.items():
        video_path = None
        if footage_dir is not None:
            candidate = footage_dir / source_file
            if candidate.exists():
                video_path = candidate
        if video_path is None:
            raise FileNotFoundError(
                f"source video for {source_file} not found under {footage_dir}"
            )

        cap = cv2.VideoCapture(str(video_path))
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
            src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0) or None
            src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0) or None
            src_ar = round(src_w / src_h, 6) if src_w and src_h else None
            meta = source_meta[source_file]
            for scene in scenes:
                frames, feats = extract_segment_evidence(cap, scene, fps, project_dir)
                rms, onset = _audio_features(video_path, scene.start_sec, scene.duration)
                feats = feats.model_copy(update={"audio_rms": round(rms, 6), "onset_density": round(onset, 4)})
                out_segments.append(
                    EvidenceSegment(
                        segment_id=scene.segment_id,
                        source_file=source_file,
                        upload_index=meta.upload_index,
                        capture_time=meta.capture_time,
                        time_order=meta.time_order,
                        start_sec=scene.start_sec,
                        end_sec=scene.end_sec,
                        duration=scene.duration,
                        evidence=frames,
                        source_width=src_w,
                        source_height=src_h,
                        source_aspect_ratio=src_ar,
                    )
                )
                out_features.append(feats)
        finally:
            cap.release()

    manifest = EvidenceManifest(
        project=project,
        sources=sorted(source_meta.values(), key=lambda m: m.upload_index),
        segments=out_segments,
    )
    features_file = DeterministicFeaturesFile(project=project, segments=out_features)

    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    features_path.write_text(features_file.model_dump_json(indent=2), encoding="utf-8")
    sheet_path = build_evidence_contact_sheets(project_dir, manifest)
    click.echo(
        f"wrote {manifest_path.name}, {features_path.name}, "
        f"{len(out_segments)} segments → {sheet_path.name}"
    )
    return manifest_path


@click.command()
@click.option("--work-dir", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--project", required=True)
@click.option("--footage-dir", type=click.Path(path_type=Path), default=None)
@click.option("--force", is_flag=True, default=False)
def main(work_dir: Path, project: str, footage_dir: Path | None, force: bool) -> None:
    out = run_evidence(work_dir, project, footage_dir=footage_dir, force=force)
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
