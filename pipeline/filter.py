"""Quality filter — drop blurry / over- or under-exposed / duplicate segments.

Reads:  work/<project>/segments.json
        work/<project>/frames/<segment_id>.jpg
Writes: work/<project>/candidates.json         (CandidatesFile, see edl.py)
        work/<project>/rejected_contact.jpg    (debug grid of rejected frames)

This stage only scores each segment's representative frame and attaches a
verdict. It never changes a segment's timecodes and never adds new
candidate segments — those still come solely from split.py.

Checks, in order (a segment is judged by the first one it fails):
1. blur       — cv2 Laplacian variance below BLUR_THRESHOLD
2. exposure   — grayscale mean brightness outside [BRIGHTNESS_LOW, BRIGHTNESS_HIGH]
3. duplicate  — pHash Hamming distance to an earlier *already-passing* frame
                within DUPLICATE_HAMMING_THRESHOLD. The earlier frame is kept
                (assumed sharper / more representative of the shot); the
                later one is marked duplicate.
"""

from __future__ import annotations

import math
from pathlib import Path

import click
import cv2
import imagehash
from PIL import Image, ImageDraw, ImageFont

from pipeline.edl import CandidateScene, CandidatesFile, Quality, SegmentsFile

BLUR_THRESHOLD = 100.0
BRIGHTNESS_LOW = 0.15
BRIGHTNESS_HIGH = 0.85
DUPLICATE_HAMMING_THRESHOLD = 5

CONTACT_CELL_SIZE = 220
CONTACT_LABEL_HEIGHT = 48
CONTACT_MARGIN = 8


def _score_frame(frame_path: Path) -> tuple[float, float, imagehash.ImageHash]:
    """Return (blur_score, brightness in [0, 1], phash) for one frame."""
    bgr = cv2.imread(str(frame_path))
    if bgr is None:
        raise FileNotFoundError(f"could not read frame: {frame_path}")

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur_score = cv2.Laplacian(gray, cv2.CV_64F).var()
    brightness = gray.mean() / 255.0
    phash = imagehash.phash(Image.open(frame_path))
    return blur_score, brightness, phash


def filter_scenes(
    work_dir: Path,
    project: str,
    blur_threshold: float = BLUR_THRESHOLD,
    brightness_low: float = BRIGHTNESS_LOW,
    brightness_high: float = BRIGHTNESS_HIGH,
    duplicate_threshold: int = DUPLICATE_HAMMING_THRESHOLD,
) -> Path:
    """Score segments.json entries (sharpness via OpenCV, exposure, near-
    duplicate detection via imagehash) and write candidates.json.
    """
    project_dir = work_dir / project
    segments_file = SegmentsFile.model_validate_json(
        (project_dir / "segments.json").read_text(encoding="utf-8")
    )

    candidates: list[CandidateScene] = []
    rejected: list[CandidateScene] = []
    accepted_hashes: list[tuple[str, imagehash.ImageHash]] = []  # (segment_id, phash) of verdict=="pass"

    for scene in segments_file.segments:
        frame_path = project_dir / scene.frame_path
        blur_score, brightness, phash = _score_frame(frame_path)

        verdict: str
        duplicate_of: str | None = None

        # Synthetic still_* clips (photo→mp4) often score soft on Laplacian
        # after encode; don't reject them as blurry at the photo threshold.
        is_still = str(scene.source_file).startswith("still_")
        effective_blur = blur_threshold * 0.25 if is_still else blur_threshold
        if blur_score < effective_blur:
            verdict = "blurry"
        elif brightness < brightness_low:
            verdict = "underexposed"
        elif brightness > brightness_high:
            verdict = "overexposed"
        else:
            verdict = "pass"
            for other_id, other_hash in accepted_hashes:
                if phash - other_hash <= duplicate_threshold:
                    verdict = "duplicate"
                    duplicate_of = other_id
                    break

        quality = Quality(
            blur_score=round(float(blur_score), 3),
            brightness=round(float(brightness), 3),
            phash=str(phash),
            verdict=verdict,
            duplicate_of=duplicate_of,
        )
        candidate = CandidateScene(**scene.model_dump(), quality=quality)

        if verdict == "pass":
            candidates.append(candidate)
            accepted_hashes.append((scene.segment_id, phash))
        else:
            rejected.append(candidate)

    candidates_file = CandidatesFile(project=project, candidates=candidates, rejected=rejected)
    out_path = project_dir / "candidates.json"
    out_path.write_text(candidates_file.model_dump_json(indent=2), encoding="utf-8")

    _print_stats(candidates, rejected)
    _write_rejected_contact(project_dir, rejected)

    return out_path


def _print_stats(candidates: list[CandidateScene], rejected: list[CandidateScene]) -> None:
    total = len(candidates) + len(rejected)
    click.echo(f"total segments: {total}")
    click.echo(f"pass: {len(candidates)}")

    counts: dict[str, int] = {}
    for c in rejected:
        counts[c.quality.verdict] = counts.get(c.quality.verdict, 0) + 1
    for reason in sorted(counts):
        click.echo(f"{reason}: {counts[reason]}")


def _write_rejected_contact(project_dir: Path, rejected: list[CandidateScene]) -> None:
    """Build work/<project>/rejected_contact.jpg — a grid of every rejected
    frame labeled with its verdict, so thresholds can be eyeballed.
    """
    out_path = project_dir / "rejected_contact.jpg"
    if not rejected:
        click.echo("no rejected frames, skipping rejected_contact.jpg")
        return

    cols = max(1, math.ceil(math.sqrt(len(rejected))))
    rows = math.ceil(len(rejected) / cols)
    cell_w = CONTACT_CELL_SIZE + CONTACT_MARGIN
    cell_h = CONTACT_CELL_SIZE + CONTACT_LABEL_HEIGHT + CONTACT_MARGIN

    sheet = Image.new("RGB", (cols * cell_w + CONTACT_MARGIN, rows * cell_h + CONTACT_MARGIN), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()

    for i, candidate in enumerate(rejected):
        col, row = i % cols, i // cols
        x = CONTACT_MARGIN + col * cell_w
        y = CONTACT_MARGIN + row * cell_h

        thumb = Image.open(project_dir / candidate.frame_path).convert("RGB")
        thumb.thumbnail((CONTACT_CELL_SIZE, CONTACT_CELL_SIZE), Image.LANCZOS)
        paste_x = x + (CONTACT_CELL_SIZE - thumb.width) // 2
        paste_y = y + (CONTACT_CELL_SIZE - thumb.height) // 2
        sheet.paste(thumb, (paste_x, paste_y))

        q = candidate.quality
        label = f"{candidate.segment_id}\n{q.verdict}"
        if q.verdict == "duplicate":
            label += f" of {q.duplicate_of}"
        label += f"\nblur={q.blur_score:.0f} b={q.brightness:.2f}"
        draw.multiline_text(
            (x + 4, y + CONTACT_CELL_SIZE + 2), label, fill="black", font=font, spacing=2
        )

    project_dir.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=90)
    click.echo(f"wrote {out_path}")


@click.command()
@click.option("--work-dir", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--project", required=True)
@click.option("--blur-threshold", type=float, default=BLUR_THRESHOLD)
@click.option("--brightness-low", type=float, default=BRIGHTNESS_LOW)
@click.option("--brightness-high", type=float, default=BRIGHTNESS_HIGH)
@click.option("--duplicate-threshold", type=int, default=DUPLICATE_HAMMING_THRESHOLD)
def main(
    work_dir: Path,
    project: str,
    blur_threshold: float,
    brightness_low: float,
    brightness_high: float,
    duplicate_threshold: int,
) -> None:
    out = filter_scenes(
        work_dir,
        project,
        blur_threshold=blur_threshold,
        brightness_low=brightness_low,
        brightness_high=brightness_high,
        duplicate_threshold=duplicate_threshold,
    )
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
