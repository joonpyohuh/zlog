"""Offline CLI for turning edited references into validated learning artifacts."""

from __future__ import annotations

import hashlib
import json
import statistics
import subprocess
from dataclasses import dataclass
from pathlib import Path

import click
import cv2
import numpy as np

from pipeline.edit_presets import PRESET_REGISTRY, PresetCategory
from pipeline.reference_models import (
    DatasetProvenance,
    DatasetSplit,
    EvidencePoint,
    FeatureObservation,
    PacingAnalysis,
    PlannerRecommendation,
    ReferenceAnnotation,
    ReferenceEditAnalysis,
    ReferenceMedia,
    ReferenceTrainingExample,
    StyleProfile,
    StyleRule,
    VisualStatistics,
)
from pipeline.split import detect_shot_frames

ANALYZER_VERSION = "reference-learning/1.0"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}


@dataclass(frozen=True)
class ReferenceWorkspace:
    root: Path

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def annotations(self) -> Path:
        return self.root / "annotations"

    @property
    def library(self) -> Path:
        return self.root / "library"

    @property
    def schemas(self) -> Path:
        return self.root / "schemas"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def init_workspace(root: Path) -> ReferenceWorkspace:
    workspace = ReferenceWorkspace(root)
    for directory in (
        workspace.inbox,
        workspace.annotations,
        workspace.library,
        workspace.schemas,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    _write_json(
        workspace.schemas / "reference_annotation.schema.json",
        ReferenceAnnotation.model_json_schema(),
    )
    _write_json(
        workspace.schemas / "reference_training_example.schema.json",
        ReferenceTrainingExample.model_json_schema(),
    )
    _write_json(
        workspace.schemas / "preset_registry.json",
        {
            preset_id: preset.model_dump(mode="json")
            for preset_id, preset in PRESET_REGISTRY.items()
        },
    )
    return workspace


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _video_metadata(path: Path) -> tuple[float, int, int, int, str | None]:
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError(f"Could not open reference video: {path}")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        codec_number = int(cap.get(cv2.CAP_PROP_FOURCC))
        codec = (
            "".join(chr((codec_number >> 8 * i) & 0xFF) for i in range(4)).strip("\x00")
            or None
        )
    finally:
        cap.release()
    if fps <= 0 or frame_count <= 0:
        raise ValueError(f"Reference has invalid timing metadata: {path}")
    return fps, frame_count, width, height, codec


def _audio_stream_status(path: Path) -> tuple[bool | None, str]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            return None, "ffprobe could not inspect the audio stream"
        present = bool(result.stdout.strip())
        return present, f"Audio stream present: {present}"
    except FileNotFoundError:
        return None, "ffprobe is unavailable; audio stream status is unknown"
    except subprocess.TimeoutExpired:
        return None, "ffprobe timed out; audio stream status is unknown"


def _visual_statistics(
    path: Path, frame_count: int, sample_count: int = 24
) -> VisualStatistics:
    cap = cv2.VideoCapture(str(path))
    luminance: list[float] = []
    saturation: list[float] = []
    warmth: list[float] = []
    frame_changes: list[float] = []
    previous_gray: np.ndarray | None = None
    try:
        for frame_index in np.linspace(
            0, max(frame_count - 1, 0), min(sample_count, frame_count), dtype=int
        ):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_index))
            ok, frame = cap.read()
            if not ok:
                continue
            small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            luminance.append(float(np.mean(hsv[:, :, 2]) / 255))
            saturation.append(float(np.mean(hsv[:, :, 1]) / 255))
            blue, _, red = cv2.mean(small)[:3]
            warmth.append(float(np.clip(0.5 + (red - blue) / 510, 0, 1)))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if previous_gray is not None:
                frame_changes.append(
                    float(np.mean(cv2.absdiff(previous_gray, gray)) / 255)
                )
            previous_gray = gray
    finally:
        cap.release()
    if not luminance:
        raise ValueError(f"Could not sample frames from reference: {path}")
    return VisualStatistics(
        sampled_frame_count=len(luminance),
        average_luminance=statistics.fmean(luminance),
        average_saturation=statistics.fmean(saturation),
        estimated_warmth=statistics.fmean(warmth),
        average_frame_change=statistics.fmean(frame_changes) if frame_changes else 0,
    )


def _load_annotation(
    workspace: ReferenceWorkspace, video_path: Path
) -> tuple[ReferenceAnnotation, bool]:
    candidates = [
        workspace.annotations / f"{video_path.name}.json",
        workspace.annotations / f"{video_path.stem}.json",
    ]
    for path in candidates:
        if path.exists():
            return ReferenceAnnotation.model_validate_json(
                path.read_text(encoding="utf-8")
            ), True
    return ReferenceAnnotation(), False


def _observation(
    category: str,
    preset_ids: list[str],
    *,
    evidence: list[EvidencePoint] | None = None,
) -> FeatureObservation:
    if preset_ids:
        return FeatureObservation(
            status="observed",
            summary=f"Human-annotated {category} presets: {', '.join(preset_ids)}.",
            preset_ids=preset_ids,
            confidence=1,
            evidence=[
                EvidencePoint(
                    source="manual", description=f"{category} sidecar annotation"
                )
            ],
        )
    return FeatureObservation(
        status="requires_manual_annotation",
        summary=f"{category.capitalize()} intent cannot be identified reliably from pixels alone.",
        evidence=evidence or [],
    )


def _annotated_presets(
    annotation: ReferenceAnnotation,
    category: PresetCategory,
    explicit_presets: list[str],
) -> list[str]:
    return list(
        dict.fromkeys(
            [
                *explicit_presets,
                *(
                    marker.preset_id
                    for marker in annotation.markers
                    if marker.category == category
                ),
            ]
        )
    )


def _dataset_split(sha256: str) -> DatasetSplit:
    bucket = int(sha256[:8], 16) % 100
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "validation"
    return "test"


def analyze_reference(
    video_path: Path, workspace: ReferenceWorkspace
) -> ReferenceTrainingExample:
    sha256 = _sha256(video_path)
    reference_id = f"ref-{sha256[:16]}"
    fps, frame_count, width, height, codec = _video_metadata(video_path)
    duration_ms = round(frame_count / fps * 1000)
    has_audio, audio_status = _audio_stream_status(video_path)
    annotation, has_annotation = _load_annotation(workspace, video_path)
    invalid_markers = [
        marker.timestamp_ms
        for marker in annotation.markers
        if marker.timestamp_ms > duration_ms
    ]
    if invalid_markers:
        raise ValueError(
            f"Annotation markers exceed {duration_ms}ms video duration: {invalid_markers}"
        )
    shots, detected_fps = detect_shot_frames(video_path)
    timing_fps = detected_fps or fps
    shot_durations = [round((end - start) / timing_fps * 1000) for start, end in shots]
    cut_times = [round(end / timing_fps * 1000) for _, end in shots[:-1]]
    average_duration = (
        statistics.fmean(shot_durations) if shot_durations else duration_ms
    )
    median_duration = (
        statistics.median(shot_durations) if shot_durations else duration_ms
    )
    pace_label = (
        "fast"
        if average_duration <= 1500
        else "slow"
        if average_duration >= 4000
        else "balanced"
    )
    visual_stats = _visual_statistics(video_path, frame_count)
    media = ReferenceMedia(
        reference_id=reference_id,
        path=str(video_path.resolve()),
        sha256=sha256,
        file_size_bytes=video_path.stat().st_size,
        duration_ms=duration_ms,
        fps=fps,
        width=width,
        height=height,
        codec=codec,
        has_audio=has_audio,
        license=annotation.license,
        source_url=annotation.source_url,
        rights_holder=annotation.rights_holder,
    )
    pacing = PacingAnalysis(
        status="observed",
        cut_times_ms=cut_times,
        shot_durations_ms=shot_durations,
        cut_count=len(cut_times),
        cuts_per_minute=len(cut_times) / max(duration_ms / 60_000, 1 / 60),
        average_shot_duration_ms=average_duration,
        median_shot_duration_ms=median_duration,
        short_shot_ms=min(shot_durations, default=duration_ms),
        long_shot_ms=max(shot_durations, default=duration_ms),
        pace_label=pace_label,
        confidence=0.9,
        evidence=[
            EvidencePoint(
                source="scenedetect", description="ContentDetector scene boundaries"
            )
        ],
    )
    audio_evidence = [EvidencePoint(source="ffprobe", description=audio_status)]
    caption_presets = _annotated_presets(
        annotation, "caption", annotation.caption_presets
    )
    motion_presets = _annotated_presets(annotation, "motion", annotation.motion_presets)
    color_presets = _annotated_presets(annotation, "color", annotation.color_presets)
    transition_presets = _annotated_presets(
        annotation, "transition", annotation.transition_presets
    )
    overlay_presets = _annotated_presets(
        annotation, "overlay", annotation.overlay_presets
    )
    audio_presets = _annotated_presets(annotation, "audio", annotation.audio_presets)
    analysis = ReferenceEditAnalysis(
        reference_id=reference_id,
        pacing=pacing,
        captions=_observation("caption", caption_presets),
        editorial_motion=_observation(
            "motion",
            motion_presets,
            evidence=[
                EvidencePoint(
                    source="opencv",
                    description="Frame change measured; camera and subject motion remain ambiguous",
                )
            ],
        ),
        color=_observation(
            "color",
            color_presets,
            evidence=[
                EvidencePoint(
                    source="opencv",
                    description="Luminance, saturation, and warmth measured",
                )
            ],
        ),
        transitions=_observation(
            "transition",
            transition_presets,
            evidence=[
                EvidencePoint(
                    source="scenedetect",
                    description="Boundaries detected; transition type requires review",
                )
            ],
        ),
        overlays=_observation("overlay", overlay_presets),
        audio=_observation("audio", audio_presets, evidence=audio_evidence),
        visual_statistics=visual_stats,
        markers=annotation.markers,
    )
    observed_behavior = [
        f"{pacing.cut_count} detected cuts over {duration_ms / 1000:.2f}s.",
        f"Median shot duration is {median_duration / 1000:.2f}s ({pace_label} pace).",
        f"Mean luminance {visual_stats.average_luminance:.2f}, saturation {visual_stats.average_saturation:.2f}, warmth {visual_stats.estimated_warmth:.2f}.",
    ]
    inferred_rules = [
        StyleRule(
            dimension="pacing",
            rule=f"Use a {pace_label} baseline with shots centered near {median_duration / 1000:.2f}s.",
            confidence=0.82,
            supporting_evidence=[
                "pacing.shot_durations_ms",
                "pacing.median_shot_duration_ms",
            ],
        )
    ]
    recommendation_lists: dict[PresetCategory, list[str]] = {
        "caption": caption_presets,
        "motion": motion_presets,
        "color": color_presets,
        "transition": transition_presets,
        "overlay": overlay_presets,
        "audio": audio_presets,
    }
    recommendations = [
        PlannerRecommendation(
            preset_id=preset_id,
            category=category,
            use_when="Reference style is requested",
            priority=80,
        )
        for category, preset_ids in recommendation_lists.items()
        for preset_id in preset_ids
    ]
    profile = StyleProfile(
        profile_id=f"style-{sha256[:16]}",
        source_reference_ids=[reference_id],
        style_tags=annotation.style_tags,
        observed_behavior=observed_behavior,
        inferred_rules=inferred_rules,
        planner_recommendations=recommendations,
        hard_constraints=[
            "Do not infer semantic edit intent from frame-difference metrics alone."
        ],
        confidence=0.9 if has_annotation else 0.65,
    )
    provenance = DatasetProvenance(
        analysis_origin="hybrid" if has_annotation else "automatic_analysis",
        analyzer_version=ANALYZER_VERSION,
        annotator=annotation.annotator,
        license=annotation.license,
        source_url=annotation.source_url,
    )
    return ReferenceTrainingExample(
        example_id=f"example-{sha256[:16]}",
        split=_dataset_split(sha256),
        reference_media=media,
        reference_edit_analysis=analysis,
        style_profile=profile,
        quality_labels=annotation.quality_labels,
        provenance=provenance,
    )


def analyze_workspace(
    root: Path, force: bool = False
) -> list[ReferenceTrainingExample]:
    workspace = init_workspace(root)
    videos = sorted(
        path
        for path in workspace.inbox.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not videos:
        raise FileNotFoundError(f"No reference videos found in {workspace.inbox}")
    examples: list[ReferenceTrainingExample] = []
    for video_path in videos:
        sha256 = _sha256(video_path)
        artifact_dir = workspace.library / f"{video_path.stem}-{sha256[:10]}"
        training_path = artifact_dir / "training_example.json"
        if training_path.exists() and not force:
            example = ReferenceTrainingExample.model_validate_json(
                training_path.read_text(encoding="utf-8")
            )
        else:
            example = analyze_reference(video_path, workspace)
            _write_json(
                artifact_dir / "reference_analysis.json",
                example.reference_edit_analysis.model_dump(mode="json"),
            )
            _write_json(
                artifact_dir / "style_profile.json",
                example.style_profile.model_dump(mode="json"),
            )
            _write_json(training_path, example.model_dump(mode="json"))
            _write_json(
                artifact_dir / "debug.json",
                {
                    "analyzer_version": ANALYZER_VERSION,
                    "source": str(video_path.resolve()),
                    "manual_annotation_loaded": example.provenance.analysis_origin
                    == "hybrid",
                    "manual_review_required": [
                        name
                        for name in (
                            "captions",
                            "editorial_motion",
                            "color",
                            "transitions",
                            "overlays",
                            "audio",
                        )
                        if getattr(example.reference_edit_analysis, name).status
                        == "requires_manual_annotation"
                    ],
                },
            )
        examples.append(example)
    unique_examples = {example.reference_media.sha256: example for example in examples}
    catalog = [
        {
            "example_id": example.example_id,
            "reference_id": example.reference_media.reference_id,
            "sha256": example.reference_media.sha256,
            "split": example.split,
            "license": example.provenance.license,
            "source_path": example.reference_media.path,
        }
        for example in unique_examples.values()
    ]
    _write_json(
        workspace.library / "catalog.json",
        {"analyzer_version": ANALYZER_VERSION, "examples": catalog},
    )
    return list(unique_examples.values())


@click.group()
def cli() -> None:
    """Manage Zlog's local reference-learning workspace."""


@cli.command("init")
@click.option(
    "--root",
    type=click.Path(path_type=Path),
    default=Path("references"),
    show_default=True,
)
def init_command(root: Path) -> None:
    workspace = init_workspace(root)
    click.echo(f"Reference workspace ready: {workspace.root.resolve()}")


@cli.command("analyze")
@click.option(
    "--root",
    type=click.Path(path_type=Path),
    default=Path("references"),
    show_default=True,
)
@click.option("--force", is_flag=True, help="Rebuild existing artifacts.")
def analyze_command(root: Path, force: bool) -> None:
    examples = analyze_workspace(root, force=force)
    click.echo(
        f"Analyzed {len(examples)} unique reference(s): {(root / 'library').resolve()}"
    )


if __name__ == "__main__":
    cli()
