"""Scene split + frame extraction.

Reads:  footage/<project>/*.mp4|*.mov  (raw source video files)
Writes: work/<project>/segments.json          (SegmentsFile, see edl.py)
        work/<project>/frames/<segment_id>.jpg

All timecodes come from PySceneDetect (ContentDetector) snapped to actual
frame boundaries — never hand-computed or LLM-generated. This stage does
not look at pixel content beyond what scene detection and one mid-point
thumbnail per segment need.

Rules applied on top of raw scene detection:
- shots shorter than MIN_SHOT_SEC are dropped
- shots longer than MAX_SHOT_SEC are chopped into CHUNK_SEC-sized pieces
"""

from __future__ import annotations

from pathlib import Path

import click
import cv2
from PIL import Image
from scenedetect import ContentDetector, detect
from tqdm import tqdm

from pipeline.edl import Scene, SegmentsFile

CONTENT_DETECTOR_THRESHOLD = 27
MIN_SHOT_SEC = 0.8
MAX_SHOT_SEC = 8.0
CHUNK_SEC = 4.0
THUMB_LONG_EDGE = 1024
VIDEO_EXTENSIONS = {".mp4", ".mov"}


def _video_files(footage_dir: Path) -> list[Path]:
    return sorted(
        p for p in footage_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )


def _detect_shot_frames(video_path: Path) -> tuple[list[tuple[int, int]], float]:
    """Run ContentDetector and return [(start_frame, end_frame), ...] plus fps.

    Falls back to a single shot spanning the whole video if PySceneDetect
    finds no cuts (e.g. a static, single-take clip).
    """
    scene_list = detect(str(video_path), ContentDetector(threshold=CONTENT_DETECTOR_THRESHOLD))

    if scene_list:
        fps = scene_list[0][0].framerate
        return [(start.frame_num, end.frame_num) for start, end in scene_list], fps

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return [(0, total_frames)], fps


def _apply_length_rules(shots: list[tuple[int, int]], fps: float) -> list[tuple[int, int]]:
    """Drop shots < MIN_SHOT_SEC, split shots > MAX_SHOT_SEC into ~CHUNK_SEC pieces."""
    chunk_frames = round(CHUNK_SEC * fps)
    out: list[tuple[int, int]] = []

    for start_frame, end_frame in shots:
        duration_sec = (end_frame - start_frame) / fps
        if duration_sec < MIN_SHOT_SEC:
            continue
        if duration_sec <= MAX_SHOT_SEC:
            out.append((start_frame, end_frame))
            continue

        cur = start_frame
        while cur < end_frame:
            piece_end = min(cur + chunk_frames, end_frame)
            if (piece_end - cur) / fps >= MIN_SHOT_SEC:
                out.append((cur, piece_end))
            cur = piece_end

    return out


def _save_mid_frame(cap: cv2.VideoCapture, start_frame: int, end_frame: int, out_path: Path) -> None:
    mid_frame = (start_frame + end_frame) // 2
    cap.set(cv2.CAP_PROP_POS_FRAMES, mid_frame)
    ok, frame_bgr = cap.read()
    if not ok:
        raise RuntimeError(f"could not read frame {mid_frame} from video for {out_path.name}")

    img = Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
    scale = THUMB_LONG_EDGE / max(img.size)
    if scale < 1:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, quality=90)


def run_split(footage_dir: Path, work_root: Path, force: bool = False) -> Path:
    """Split every video in footage_dir into segments and write segments.json.

    footage_dir's basename is used as the project name, e.g. footage/trip_01
    -> project "trip_01" -> work/trip_01/segments.json.
    """
    project = footage_dir.name
    project_dir = work_root / project
    segments_path = project_dir / "segments.json"

    if segments_path.exists() and not force:
        click.echo(f"{segments_path} already exists, skipping (use --force to overwrite)")
        return segments_path

    videos = _video_files(footage_dir)
    if not videos:
        raise FileNotFoundError(f"no .mp4/.mov files found in {footage_dir}")

    frames_dir = project_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    # Pass 1: detect + apply length rules for every video up front so the
    # progress bar reflects the real amount of frame-extraction work.
    per_video_shots: dict[Path, tuple[list[tuple[int, int]], float]] = {}
    for video_path in tqdm(videos, desc="detecting scenes"):
        shots, fps = _detect_shot_frames(video_path)
        per_video_shots[video_path] = (_apply_length_rules(shots, fps), fps)

    total_segments = sum(len(shots) for shots, _ in per_video_shots.values())

    segments: list[Scene] = []
    with tqdm(total=total_segments, desc="extracting frames") as pbar:
        for video_path, (shots, fps) in per_video_shots.items():
            cap = cv2.VideoCapture(str(video_path))
            try:
                for n, (start_frame, end_frame) in enumerate(shots, start=1):
                    segment_id = f"{video_path.stem}#s{n:03d}"
                    frame_rel_path = f"frames/{segment_id}.jpg"
                    _save_mid_frame(cap, start_frame, end_frame, project_dir / frame_rel_path)

                    start_sec = round(start_frame / fps, 3)
                    end_sec = round(end_frame / fps, 3)
                    segments.append(
                        Scene(
                            segment_id=segment_id,
                            source_file=video_path.name,
                            start_sec=start_sec,
                            end_sec=end_sec,
                            duration=round(end_sec - start_sec, 3),
                            frame_path=frame_rel_path,
                        )
                    )
                    pbar.update(1)
            finally:
                cap.release()

    segments_file = SegmentsFile(project=project, segments=segments)
    project_dir.mkdir(parents=True, exist_ok=True)
    segments_path.write_text(segments_file.model_dump_json(indent=2), encoding="utf-8")
    return segments_path


def print_summary(segments_path: Path) -> None:
    segments_file = SegmentsFile.model_validate_json(segments_path.read_text(encoding="utf-8"))
    segs = segments_file.segments
    if not segs:
        click.echo("no segments produced")
        return

    durations = [s.duration for s in segs]
    longest = max(segs, key=lambda s: s.duration)
    shortest = min(segs, key=lambda s: s.duration)

    click.echo(f"project: {segments_file.project}")
    click.echo(f"total segments: {len(segs)}")
    click.echo(f"average duration: {sum(durations) / len(durations):.3f}s")
    click.echo(f"longest shot: {longest.segment_id} ({longest.duration:.3f}s)")
    click.echo(f"shortest shot: {shortest.segment_id} ({shortest.duration:.3f}s)")


@click.command()
@click.argument("footage_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--work-root", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--force", is_flag=True, default=False, help="overwrite an existing segments.json")
def main(footage_dir: Path, work_root: Path, force: bool) -> None:
    out = run_split(footage_dir, work_root, force=force)
    click.echo(f"wrote {out}")
    print_summary(out)


if __name__ == "__main__":
    main()
