"""Cacheable local perception passes feeding Zlog's existing planning path.

The module deliberately does not call a model. Ambiguous boundaries become
bounded escalation requests for the existing provider layer to satisfy later.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import click
import cv2
from pydantic import BaseModel, Field

from pipeline.edl import CandidatesFile, SegmentsFile
from pipeline.evidence import DeterministicFeaturesFile, EvidenceManifest
from pipeline.execution import cache_key
from pipeline.perception_models import (
    AnalysisBudget,
    AudioFeatures,
    BoundaryCandidate,
    BoundaryCandidatesArtifact,
    BoundaryDecision,
    BoundaryDecisionsArtifact,
    CompositionFeatures,
    ConfidenceRecord,
    EditingGoal,
    EscalationRequest,
    FactorizedFeatures,
    HierarchicalTimeline,
    HierarchyLevel,
    LightingFeatures,
    MotionFeatures,
    PerceptionObservation,
    ProcessingCostReport,
    ProvenanceRecord,
    QualityFeatures,
    RoutingArtifact,
    ScanArtifact,
    SegmentRoute,
    SignalScores,
    SourceAssetMetadata,
    SourceRange,
    StageCost,
    TimelineNode,
)

LOGGER = logging.getLogger("zlog.perception")
STAGE_VERSION = "adaptive-perception-v1"
T = TypeVar("T", bound=BaseModel)


class PerceptionConfig(BaseModel):
    proposer_threshold: float = Field(default=0.20, ge=0.0, le=1.0)
    split_threshold: float = Field(default=0.56, ge=0.0, le=1.0)
    uncertainty_band: float = Field(default=0.07, ge=0.0, le=0.5)
    shot_change_weight: float = Field(default=0.24, ge=0.0)
    visual_change_weight: float = Field(default=0.18, ge=0.0)
    motion_change_weight: float = Field(default=0.12, ge=0.0)
    lighting_change_weight: float = Field(default=0.10, ge=0.0)
    audio_change_weight: float = Field(default=0.12, ge=0.0)
    prediction_error_weight: float = Field(default=0.24, ge=0.0)


STAGE_FILES = {
    "scan": "perception_scan.json",
    "boundary_proposal": "boundary_candidates.json",
    "routing": "perception_routes.json",
    "boundary_judgment": "boundary_decisions.json",
    "hierarchy": "hierarchical_timeline.json",
}


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(raw.encode()).hexdigest()[:14]}"


def _hash_paths(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.name):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_model(path: Path, model: BaseModel) -> None:
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")


def _log_stage(project: str, stage: StageCost) -> None:
    LOGGER.info(
        json.dumps(
            {
                "project": project,
                "stage": stage.stage,
                "duration_ms": stage.latency_ms,
                "cache_status": stage.cache_status,
                "sampled_frames": stage.sampled_frames,
                "estimated_cost_usd": stage.estimated_cost_usd,
            },
            separators=(",", ":"),
        )
    )


def _run_cached_stage(
    *,
    project: str,
    project_dir: Path,
    stage: str,
    input_paths: list[Path],
    config: dict[str, Any],
    model_type: type[T],
    build: Callable[[], T],
    force: bool,
) -> tuple[T, StageCost]:
    output = project_dir / STAGE_FILES[stage]
    cache_path = project_dir / "perception_cache.json"
    cache_data = (
        _read_json(cache_path)
        if cache_path.exists()
        else {"schema_version": "1.0", "stages": {}}
    )
    key = cache_key(
        source_hash=_hash_paths(input_paths),
        config=config,
        model="local",
        prompt_version="none",
        schema_version="1.0",
        stage_version=f"{STAGE_VERSION}:{stage}",
    )
    started = time.perf_counter()
    cached = (
        not force
        and output.exists()
        and cache_data.get("stages", {}).get(stage, {}).get("key") == key
    )
    if cached:
        value = model_type.model_validate_json(output.read_text(encoding="utf-8"))
    else:
        value = build()
        _write_model(output, value)
        cache_data.setdefault("stages", {})[stage] = {
            "key": key,
            "artifact": output.name,
        }
        cache_path.write_text(json.dumps(cache_data, indent=2), encoding="utf-8")
    cost = StageCost(
        stage=stage,
        latency_ms=round((time.perf_counter() - started) * 1000.0, 2),
        cache_status="hit" if cached else "miss",
    )
    _log_stage(project, cost)
    return value, cost


def _mean(values: list[float], default: float = 0.0) -> float:
    return sum(values) / len(values) if values else default


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _codec(cap: cv2.VideoCapture) -> str | None:
    value = int(cap.get(cv2.CAP_PROP_FOURCC))
    if not value:
        return None
    return (
        "".join(chr((value >> (8 * index)) & 0xFF) for index in range(4)).strip()
        or None
    )


def _source_metadata(
    project: str,
    segments: SegmentsFile,
    footage_dir: Path | None,
) -> list[SourceAssetMetadata]:
    by_source: dict[str, int] = {}
    for segment in segments.segments:
        by_source[segment.source_file] = max(
            by_source.get(segment.source_file, 0), round(segment.end_sec * 1000)
        )
    out: list[SourceAssetMetadata] = []
    for source_file, fallback_duration in by_source.items():
        path = footage_dir / source_file if footage_dir else None
        fps = 0.0
        width = 0
        height = 0
        duration_ms = fallback_duration
        codec = None
        if path and path.exists():
            cap = cv2.VideoCapture(str(path))
            try:
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                frames = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
                if fps > 0 and frames > 0:
                    duration_ms = round(frames / fps * 1000)
                codec = _codec(cap)
            finally:
                cap.release()
        out.append(
            SourceAssetMetadata(
                asset_id=_stable_id("asset", project, source_file),
                source_file=source_file,
                duration_ms=duration_ms,
                fps=round(fps, 3),
                width=width,
                height=height,
                codec=codec,
                provenance=[
                    ProvenanceRecord(source="opencv", model_or_version=cv2.__version__)
                ],
            )
        )
    return out


def _build_scan(
    project: str,
    project_dir: Path,
    footage_dir: Path | None,
    budget: AnalysisBudget,
) -> tuple[ScanArtifact, list[str]]:
    segments = SegmentsFile.model_validate_json(
        (project_dir / "segments.json").read_text(encoding="utf-8")
    )
    manifest = EvidenceManifest.model_validate_json(
        (project_dir / "evidence_manifest.json").read_text(encoding="utf-8")
    )
    features = DeterministicFeaturesFile.model_validate_json(
        (project_dir / "deterministic_features.json").read_text(encoding="utf-8")
    )
    candidates_path = project_dir / "candidates.json"
    candidates = (
        CandidatesFile.model_validate_json(candidates_path.read_text(encoding="utf-8"))
        if candidates_path.exists()
        else None
    )
    feature_by_id = {item.segment_id: item for item in features.segments}
    evidence_by_id = {item.segment_id: item for item in manifest.segments}
    verdict_by_id = {}
    duplicate_by_id = {}
    if candidates:
        for item in candidates.candidates + candidates.rejected:
            verdict_by_id[item.segment_id] = item.quality.verdict
            duplicate_by_id[item.segment_id] = (
                1.0 if item.quality.verdict == "duplicate" else 0.0
            )

    frame_priority: list[tuple[float, str]] = []
    for segment in segments.segments:
        feat = feature_by_id.get(segment.segment_id)
        evidence = evidence_by_id.get(segment.segment_id)
        if not evidence:
            continue
        for frame in evidence.evidence:
            priority = (2.0 if frame.role == "representative" else 1.0) + (
                feat.mean_motion if feat else 0.0
            )
            frame_priority.append((priority, frame.evidence_id))
    frame_priority.sort(key=lambda item: (-item[0], item[1]))
    selected_frames = {
        item[1] for item in frame_priority[: budget.max_representative_frames]
    }
    degraded = []
    if len(frame_priority) > len(selected_frames):
        degraded.append("representative_frame_budget_applied")

    observations: list[PerceptionObservation] = []
    for segment in segments.segments:
        feat = feature_by_id.get(segment.segment_id)
        evidence = evidence_by_id.get(segment.segment_id)
        frame_features = feat.frames if feat else []
        brightness = _mean([item.brightness for item in frame_features], 0.5)
        contrast = _mean([item.contrast for item in frame_features], 0.0)
        blur = _mean([item.blur for item in frame_features], 0.0)
        motion = feat.mean_motion if feat else 0.0
        perceptual = feat.mean_perceptual_distance if feat else 0.0
        rms = feat.audio_rms if feat else 0.0
        onset = feat.onset_density if feat else 0.0
        refs = [
            frame.evidence_id
            for frame in (evidence.evidence if evidence else [])
            if frame.evidence_id in selected_frames
        ]
        frame_timestamps = {
            frame.evidence_id: round(frame.offset_sec * 1000)
            for frame in (evidence.evidence if evidence else [])
            if frame.evidence_id in selected_frames
        }
        warmth = None
        if evidence and refs:
            frame_record = next(
                (item for item in evidence.evidence if item.evidence_id == refs[0]),
                None,
            )
            image_path = project_dir / frame_record.frame_path if frame_record else None
            image = (
                cv2.imread(str(image_path))
                if image_path and image_path.exists()
                else None
            )
            if image is not None:
                blue, _, red = (float(value) for value in image.mean(axis=(0, 1)))
                warmth = _clamp(red / max(1.0, red + blue))
        exposure_usable = 0.15 <= brightness <= 0.85
        sharpness = _clamp(math.log1p(max(0.0, blur)) / math.log1p(500.0))
        quality = _clamp(0.55 * sharpness + 0.45 * (1.0 - abs(brightness - 0.5) * 2.0))
        observations.append(
            PerceptionObservation(
                observation_id=_stable_id("obs", project, segment.segment_id),
                segment_id=segment.segment_id,
                source_range=SourceRange(
                    source_file=segment.source_file,
                    start_ms=round(segment.start_sec * 1000),
                    end_ms=round(segment.end_sec * 1000),
                ),
                representative_frame_ids=refs,
                representative_frame_timestamps_ms=frame_timestamps,
                features=FactorizedFeatures(
                    lighting=LightingFeatures(
                        luminance=round(brightness, 4),
                        contrast=round(contrast, 4),
                        estimated_warmth=round(warmth, 4)
                        if warmth is not None
                        else None,
                        exposure_usable=exposure_usable,
                    ),
                    composition=CompositionFeatures(),
                    motion=MotionFeatures(
                        optical_flow_magnitude=round(motion, 4),
                        perceptual_change=round(perceptual, 4),
                        is_static=feat.is_static if feat else True,
                        is_high_motion=feat.is_high_motion if feat else False,
                    ),
                    audio=AudioFeatures(
                        rms=round(rms, 6),
                        onset_density=round(onset, 4),
                        silence_probability=round(_clamp(1.0 - rms * 20.0), 4),
                        speech_presence=None,
                        availability="measured" if rms > 0 else "unavailable",
                        unavailable_reason=None
                        if rms > 0
                        else "no_decodable_audio_or_silence",
                    ),
                    quality=QualityFeatures(
                        blur_score=round(blur, 3),
                        quality_score=round(quality, 4),
                        duplicate_likelihood=duplicate_by_id.get(
                            segment.segment_id, 0.0
                        ),
                        filter_verdict=verdict_by_id.get(segment.segment_id),
                    ),
                ),
                provenance=[
                    ProvenanceRecord(
                        source="opencv",
                        model_or_version=STAGE_VERSION,
                        input_evidence_ids=refs,
                    )
                ],
            )
        )
    sources = _source_metadata(project, segments, footage_dir)
    duration_ms = sum(item.duration_ms for item in sources)
    return (
        ScanArtifact(
            project=project,
            duration_ms=duration_ms,
            sources=sources,
            observations=observations,
        ),
        degraded,
    )


def prediction_error(
    previous: PerceptionObservation, observed: PerceptionObservation
) -> float:
    """Replaceable adjacent-state approximation; no learned predictor in the MVP."""
    prev = previous.features
    cur = observed.features
    deltas = [
        abs(prev.lighting.luminance - cur.lighting.luminance),
        abs(prev.lighting.contrast - cur.lighting.contrast),
        _clamp(
            abs(prev.motion.optical_flow_magnitude - cur.motion.optical_flow_magnitude)
            / 3.0
        ),
        abs(prev.motion.perceptual_change - cur.motion.perceptual_change),
        _clamp(abs(prev.audio.rms - cur.audio.rms) * 20.0),
    ]
    return round(_clamp(_mean(deltas)), 4)


def _build_boundary_candidates(
    project: str,
    scan: ScanArtifact,
    config: PerceptionConfig,
) -> BoundaryCandidatesArtifact:
    weights = {
        "shot": config.shot_change_weight,
        "visual": config.visual_change_weight,
        "motion": config.motion_change_weight,
        "lighting": config.lighting_change_weight,
        "audio": config.audio_change_weight,
        "prediction": config.prediction_error_weight,
    }
    weight_total = sum(weights.values()) or 1.0
    candidates: list[BoundaryCandidate] = []
    for before, after in zip(scan.observations, scan.observations[1:], strict=False):
        same_source = before.source_range.source_file == after.source_range.source_file
        shot = 1.0
        visual = _clamp(
            0.5
            * abs(before.features.lighting.contrast - after.features.lighting.contrast)
            + 0.5
            * abs(
                before.features.motion.perceptual_change
                - after.features.motion.perceptual_change
            )
        )
        motion = _clamp(
            abs(
                before.features.motion.optical_flow_magnitude
                - after.features.motion.optical_flow_magnitude
            )
            / 3.0
        )
        lighting = abs(
            before.features.lighting.luminance - after.features.lighting.luminance
        )
        audio = _clamp(abs(before.features.audio.rms - after.features.audio.rms) * 20.0)
        error = prediction_error(before, after)
        if not same_source:
            visual = max(visual, 0.8)
            error = max(error, 0.75)
        combined = (
            shot * weights["shot"]
            + visual * weights["visual"]
            + motion * weights["motion"]
            + lighting * weights["lighting"]
            + audio * weights["audio"]
            + error * weights["prediction"]
        ) / weight_total
        if combined < config.proposer_threshold and same_source:
            continue
        timestamp = after.source_range.start_ms
        reasons = ["SHOT_CHANGE"]
        if error >= 0.35:
            reasons.append("PREDICTION_ERROR")
        if not same_source:
            reasons.append("SOURCE_CHANGE")
        candidates.append(
            BoundaryCandidate(
                candidate_id=_stable_id(
                    "boundary", project, before.segment_id, after.segment_id, timestamp
                ),
                timestamp_ms=timestamp,
                before_segment_id=before.segment_id,
                after_segment_id=after.segment_id,
                signal_scores=SignalScores(
                    shot_change=shot,
                    visual_change=round(visual, 4),
                    motion_change=round(motion, 4),
                    lighting_change=round(lighting, 4),
                    audio_change=round(audio, 4),
                    prediction_error=error,
                ),
                combined_score=round(_clamp(combined), 4),
                confidence=ConfidenceRecord(
                    score=round(
                        _clamp(0.55 + abs(combined - config.proposer_threshold)), 4
                    ),
                    reason_codes=reasons,
                ),
                provenance=[
                    ProvenanceRecord(
                        source="heuristic",
                        model_or_version=f"{STAGE_VERSION}:weighted-boundary-v1",
                        input_evidence_ids=(
                            before.representative_frame_ids
                            + after.representative_frame_ids
                        ),
                    )
                ],
            )
        )
    return BoundaryCandidatesArtifact(project=project, candidates=candidates)


def _normalize_categories(values: dict[str, float]) -> dict[str, float]:
    total = sum(values.values()) or 1.0
    return {key: round(value / total, 4) for key, value in sorted(values.items())}


def _build_routes(project: str, scan: ScanArtifact) -> RoutingArtifact:
    routes: list[SegmentRoute] = []
    for observation in scan.observations:
        feature = observation.features
        categories = {
            "daily_vlog": 0.35,
            "conversation": 0.08 + min(0.35, feature.audio.rms * 8.0),
            "travel_landscape": 0.12 + 0.12 * feature.lighting.contrast,
            "activity": 0.08 + min(0.45, feature.motion.optical_flow_magnitude / 4.0),
            "food_cafe": 0.08 + (0.08 if feature.motion.is_static else 0.0),
            "product_focused": 0.05 + (0.08 if feature.motion.is_static else 0.0),
        }
        normalized = _normalize_categories(categories)
        top = sorted(normalized, key=lambda key: normalized[key], reverse=True)[:2]
        groups = {"quality", "lighting", "motion"}
        if "conversation" in top:
            groups.update({"audio", "people", "emotion"})
        if "travel_landscape" in top:
            groups.add("composition")
        if "food_cafe" in top or "product_focused" in top:
            groups.update({"composition", "semantic"})
        ordered = sorted(normalized.values(), reverse=True)
        margin = ordered[0] - ordered[1] if len(ordered) > 1 else ordered[0]
        routes.append(
            SegmentRoute(
                segment_id=observation.segment_id,
                categories=normalized,
                selected_feature_groups=sorted(groups),
                confidence=ConfidenceRecord(
                    score=round(_clamp(0.45 + margin), 4),
                    reason_codes=[
                        "LOCAL_SOFT_ROUTING",
                        "CATEGORY_ADAPTIVE_FEATURE_BUNDLE",
                    ],
                ),
                provenance=[
                    ProvenanceRecord(
                        source="heuristic",
                        model_or_version=f"{STAGE_VERSION}:router-v1",
                        input_evidence_ids=observation.representative_frame_ids,
                    )
                ],
            )
        )
    return RoutingArtifact(project=project, routes=routes)


def _goal_split_threshold(config: PerceptionConfig, goal: EditingGoal) -> float:
    pacing_adjustment = {"slow": 0.12, "balanced": 0.0, "fast": -0.10}[goal.pacing]
    duration_adjustment = (
        -0.04
        if goal.target_duration_seconds <= 20
        else 0.04
        if goal.target_duration_seconds >= 60
        else 0.0
    )
    return _clamp(config.split_threshold + pacing_adjustment + duration_adjustment)


def _build_decisions(
    project: str,
    scan: ScanArtifact,
    candidates: BoundaryCandidatesArtifact,
    goal: EditingGoal,
    budget: AnalysisBudget,
    config: PerceptionConfig,
) -> BoundaryDecisionsArtifact:
    threshold = _goal_split_threshold(config, goal)
    by_segment = {item.segment_id: item for item in scan.observations}
    decisions: list[BoundaryDecision] = []
    escalations: list[EscalationRequest] = []
    for candidate in candidates.candidates:
        delta = candidate.combined_score - threshold
        ambiguous = abs(delta) <= config.uncertainty_band
        if ambiguous:
            decision = "uncertain"
            reasons = ["CONFLICTING_LOCAL_SIGNALS", "SELECTIVE_REINSPECTION_REQUIRED"]
        elif delta > 0:
            decision = "split"
            reasons = ["COMBINED_CHANGE_ABOVE_GOAL_THRESHOLD"]
        else:
            decision = "merge"
            reasons = ["CONTINUITY_ABOVE_GOAL_THRESHOLD"]
        if candidate.signal_scores.prediction_error >= 0.35:
            reasons.append("PREDICTION_ERROR")
        before = by_segment[candidate.before_segment_id]
        after = by_segment[candidate.after_segment_id]
        inspected = before.representative_frame_ids + after.representative_frame_ids
        confidence = _clamp(0.5 + abs(delta) / max(0.01, 1.0 - threshold))
        decisions.append(
            BoundaryDecision(
                candidate_id=candidate.candidate_id,
                decision=decision,
                reason_codes=reasons,
                concise_reason=(
                    f"score {candidate.combined_score:.2f} vs goal threshold {threshold:.2f}"
                ),
                confidence=round(
                    confidence if not ambiguous else min(confidence, 0.55), 4
                ),
                inspected_evidence_ids=inspected,
            )
        )
        if ambiguous:
            escalations.append(
                EscalationRequest(
                    request_id=_stable_id("inspect", candidate.candidate_id),
                    candidate_id=candidate.candidate_id,
                    task="boundary_judgment",
                    reason_codes=reasons,
                    status=(
                        "budget_blocked"
                        if budget.max_high_cost_inspections == 0
                        else "provider_not_configured"
                    ),
                    context_segment_ids=[
                        candidate.before_segment_id,
                        candidate.after_segment_id,
                    ],
                )
            )
    return BoundaryDecisionsArtifact(
        project=project,
        decisions=decisions,
        escalation_requests=escalations,
    )


def _combined_categories(
    segment_ids: list[str], routes: dict[str, SegmentRoute]
) -> dict[str, float]:
    keys = {key for segment_id in segment_ids for key in routes[segment_id].categories}
    return _normalize_categories(
        {
            key: _mean(
                [
                    routes[segment_id].categories.get(key, 0.0)
                    for segment_id in segment_ids
                ]
            )
            for key in keys
        }
    )


def _build_hierarchy(
    project: str,
    scan: ScanArtifact,
    routes_artifact: RoutingArtifact,
    candidates: BoundaryCandidatesArtifact,
    decisions_artifact: BoundaryDecisionsArtifact,
    goal: EditingGoal,
    degraded_reasons: list[str],
) -> HierarchicalTimeline:
    routes = {item.segment_id: item for item in routes_artifact.routes}
    observations = {item.observation_id: item for item in scan.observations}
    decisions = {item.candidate_id: item for item in decisions_artifact.decisions}
    candidate_after = {item.after_segment_id: item for item in candidates.candidates}
    nodes: list[TimelineNode] = []
    node_by_id: dict[str, TimelineNode] = {}

    def add(node: TimelineNode) -> TimelineNode:
        nodes.append(node)
        node_by_id[node.node_id] = node
        return node

    atomic_nodes: list[TimelineNode] = []
    for observation in scan.observations:
        frame_ids: list[str] = []
        for frame_id in observation.representative_frame_ids:
            timestamp_ms = observation.representative_frame_timestamps_ms.get(
                frame_id, observation.source_range.start_ms
            )
            frame = add(
                TimelineNode(
                    node_id=_stable_id("frame", frame_id),
                    level=HierarchyLevel.frame,
                    source_ranges=[
                        SourceRange(
                            source_file=observation.source_range.source_file,
                            start_ms=timestamp_ms,
                            end_ms=timestamp_ms,
                        )
                    ],
                    representative_frame_ids=[frame_id],
                    confidence=ConfidenceRecord(
                        score=0.98, reason_codes=["DETERMINISTIC_FRAME_SAMPLE"]
                    ),
                    provenance=observation.provenance,
                )
            )
            frame_ids.append(frame.node_id)
        shot = add(
            TimelineNode(
                node_id=_stable_id("shot", observation.segment_id),
                level=HierarchyLevel.shot,
                source_ranges=[observation.source_range],
                child_ids=frame_ids,
                categories=routes[observation.segment_id].categories,
                representative_frame_ids=observation.representative_frame_ids,
                feature_observation_ids=[observation.observation_id],
                confidence=ConfidenceRecord(
                    score=0.96, reason_codes=["PYSCENEDETECT_RANGE"]
                ),
                provenance=observation.provenance,
            )
        )
        for child_id in frame_ids:
            node_by_id[child_id].parent_id = shot.node_id
        atomic = add(
            TimelineNode(
                node_id=_stable_id("event", observation.segment_id),
                level=HierarchyLevel.atomic_event,
                source_ranges=[observation.source_range],
                child_ids=[shot.node_id],
                categories=routes[observation.segment_id].categories,
                representative_frame_ids=observation.representative_frame_ids,
                feature_observation_ids=[observation.observation_id],
                confidence=ConfidenceRecord(
                    score=0.62, reason_codes=["SHOT_AS_ATOMIC_EVENT_CANDIDATE"]
                ),
                provenance=routes[observation.segment_id].provenance,
            )
        )
        shot.parent_id = atomic.node_id
        atomic_nodes.append(atomic)

    semantic_groups: list[list[TimelineNode]] = []
    for atomic in atomic_nodes:
        observation_id = node_by_id[atomic.child_ids[0]].feature_observation_ids[0]
        observation = observations[observation_id]
        boundary = candidate_after.get(observation.segment_id)
        should_merge = bool(
            semantic_groups
            and boundary
            and decisions.get(boundary.candidate_id)
            and decisions[boundary.candidate_id].decision == "merge"
        )
        if should_merge:
            semantic_groups[-1].append(atomic)
        else:
            semantic_groups.append([atomic])

    semantic_nodes: list[TimelineNode] = []
    for group in semantic_groups:
        segment_ids = [
            observations[node.feature_observation_ids[0]].segment_id for node in group
        ]
        scene = add(
            TimelineNode(
                node_id=_stable_id("scene", *[node.node_id for node in group]),
                level=HierarchyLevel.semantic_scene,
                source_ranges=[
                    source_range
                    for node in group
                    for source_range in node.source_ranges
                ],
                child_ids=[node.node_id for node in group],
                categories=_combined_categories(segment_ids, routes),
                representative_frame_ids=[
                    frame_id
                    for node in group
                    for frame_id in node.representative_frame_ids
                ],
                feature_observation_ids=[
                    observation_id
                    for node in group
                    for observation_id in node.feature_observation_ids
                ],
                confidence=ConfidenceRecord(
                    score=round(_mean([node.confidence.score for node in group]), 4),
                    reason_codes=[
                        "ADJACENT_EVENT_MERGE" if len(group) > 1 else "EVENT_SCENE"
                    ],
                ),
                provenance=[
                    ProvenanceRecord(
                        source="heuristic",
                        model_or_version=f"{STAGE_VERSION}:semantic-merge-v1",
                    )
                ],
            )
        )
        for child in group:
            child.parent_id = scene.node_id
        semantic_nodes.append(scene)

    roles = [
        "hook",
        "orientation",
        "development",
        "zlog_moment",
        "release",
        "resonance",
    ]
    beat_nodes: list[TimelineNode] = []
    for index, scene in enumerate(semantic_nodes):
        role_index = round(index * (len(roles) - 1) / max(1, len(semantic_nodes) - 1))
        beat = add(
            TimelineNode(
                node_id=_stable_id("beat", scene.node_id, roles[role_index]),
                level=HierarchyLevel.narrative_beat,
                source_ranges=scene.source_ranges,
                child_ids=[scene.node_id],
                categories=scene.categories,
                representative_frame_ids=scene.representative_frame_ids,
                feature_observation_ids=scene.feature_observation_ids,
                narrative_role=roles[role_index],
                confidence=ConfidenceRecord(
                    score=0.55, reason_codes=["GOAL_CONDITIONED_POSITIONAL_ROLE"]
                ),
                provenance=[
                    ProvenanceRecord(
                        source="heuristic",
                        model_or_version=f"{STAGE_VERSION}:soft-flow-v1",
                    )
                ],
            )
        )
        scene.parent_id = beat.node_id
        beat_nodes.append(beat)

    if beat_nodes:
        chapter = add(
            TimelineNode(
                node_id=_stable_id(
                    "chapter", project, *[node.node_id for node in beat_nodes]
                ),
                level=HierarchyLevel.chapter,
                source_ranges=[
                    source_range
                    for node in beat_nodes
                    for source_range in node.source_ranges
                ],
                child_ids=[node.node_id for node in beat_nodes],
                categories=_normalize_categories(
                    {
                        key: _mean(
                            [node.categories.get(key, 0.0) for node in beat_nodes]
                        )
                        for key in {
                            key for node in beat_nodes for key in node.categories
                        }
                    }
                ),
                representative_frame_ids=[
                    frame_id
                    for node in beat_nodes
                    for frame_id in node.representative_frame_ids
                ],
                feature_observation_ids=[
                    observation_id
                    for node in beat_nodes
                    for observation_id in node.feature_observation_ids
                ],
                confidence=ConfidenceRecord(
                    score=0.52, reason_codes=["SINGLE_EPISODE_MVP"]
                ),
                provenance=[
                    ProvenanceRecord(
                        source="heuristic",
                        model_or_version=f"{STAGE_VERSION}:chapter-v1",
                    )
                ],
            )
        )
        for beat in beat_nodes:
            beat.parent_id = chapter.node_id

    asset_id = _stable_id("project", project, *[item.asset_id for item in scan.sources])
    return HierarchicalTimeline(
        project=project,
        asset_id=asset_id,
        duration_ms=scan.duration_ms,
        editing_goal=goal,
        nodes=nodes,
        boundary_candidate_ids=[item.candidate_id for item in candidates.candidates],
        boundary_decision_ids=[
            item.candidate_id for item in decisions_artifact.decisions
        ],
        escalation_requests=decisions_artifact.escalation_requests,
        quality_status="degraded" if degraded_reasons else "full",
        degraded_reasons=degraded_reasons,
    )


def run_adaptive_perception(
    work_dir: Path,
    project: str,
    *,
    footage_dir: Path | None = None,
    editing_goal: EditingGoal | None = None,
    budget: AnalysisBudget | None = None,
    config: PerceptionConfig | None = None,
    force: bool = False,
) -> Path:
    """Run all local passes and write hierarchical_timeline.json plus cost report."""
    project_dir = work_dir / project
    goal = editing_goal or EditingGoal()
    analysis_budget = budget or AnalysisBudget()
    perception_config = config or PerceptionConfig()
    required = [
        project_dir / "segments.json",
        project_dir / "evidence_manifest.json",
        project_dir / "deterministic_features.json",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"perception inputs missing: {missing}")
    project_dir.mkdir(parents=True, exist_ok=True)
    costs: list[StageCost] = []
    scan_state: dict[str, Any] = {"degraded": []}

    def scan_build() -> ScanArtifact:
        artifact, degraded = _build_scan(
            project, project_dir, footage_dir, analysis_budget
        )
        scan_state["degraded"] = degraded
        return artifact

    scan_inputs = required + (
        [project_dir / "candidates.json"]
        if (project_dir / "candidates.json").exists()
        else []
    )
    scan, cost = _run_cached_stage(
        project=project,
        project_dir=project_dir,
        stage="scan",
        input_paths=scan_inputs,
        config=analysis_budget.model_dump(),
        model_type=ScanArtifact,
        build=scan_build,
        force=force,
    )
    sampled = sum(len(item.representative_frame_ids) for item in scan.observations)
    degraded_reasons = list(scan_state["degraded"])
    if not degraded_reasons:
        total_source_frames = sum(
            len(item.evidence)
            for item in EvidenceManifest.model_validate_json(
                (project_dir / "evidence_manifest.json").read_text(encoding="utf-8")
            ).segments
        )
        if total_source_frames > sampled:
            degraded_reasons.append("representative_frame_budget_applied")
    cost.sampled_frames = sampled
    cost.inspected_seconds = round(scan.duration_ms / 1000.0, 3)
    costs.append(cost)

    candidates, cost = _run_cached_stage(
        project=project,
        project_dir=project_dir,
        stage="boundary_proposal",
        input_paths=[project_dir / STAGE_FILES["scan"]],
        config=perception_config.model_dump(),
        model_type=BoundaryCandidatesArtifact,
        build=lambda: _build_boundary_candidates(project, scan, perception_config),
        force=force,
    )
    costs.append(cost)
    routes, cost = _run_cached_stage(
        project=project,
        project_dir=project_dir,
        stage="routing",
        input_paths=[project_dir / STAGE_FILES["scan"]],
        config={"router_version": "v1"},
        model_type=RoutingArtifact,
        build=lambda: _build_routes(project, scan),
        force=force,
    )
    costs.append(cost)
    decisions, cost = _run_cached_stage(
        project=project,
        project_dir=project_dir,
        stage="boundary_judgment",
        input_paths=[
            project_dir / STAGE_FILES["scan"],
            project_dir / STAGE_FILES["boundary_proposal"],
            project_dir / STAGE_FILES["routing"],
        ],
        config={
            "goal": goal.model_dump(),
            "budget": analysis_budget.model_dump(),
            "config": perception_config.model_dump(),
        },
        model_type=BoundaryDecisionsArtifact,
        build=lambda: _build_decisions(
            project, scan, candidates, goal, analysis_budget, perception_config
        ),
        force=force,
    )
    costs.append(cost)
    timeline, cost = _run_cached_stage(
        project=project,
        project_dir=project_dir,
        stage="hierarchy",
        input_paths=[
            project_dir / STAGE_FILES["scan"],
            project_dir / STAGE_FILES["boundary_proposal"],
            project_dir / STAGE_FILES["routing"],
            project_dir / STAGE_FILES["boundary_judgment"],
        ],
        config={"goal": goal.model_dump(), "degraded_reasons": degraded_reasons},
        model_type=HierarchicalTimeline,
        build=lambda: _build_hierarchy(
            project,
            scan,
            routes,
            candidates,
            decisions,
            goal,
            degraded_reasons,
        ),
        force=force,
    )
    costs.append(cost)
    total_latency = round(sum(item.latency_ms for item in costs), 2)
    if total_latency > analysis_budget.max_processing_time_ms:
        reason = "processing_time_budget_exceeded"
        if reason not in degraded_reasons:
            degraded_reasons.append(reason)
        timeline.quality_status = "degraded"
        timeline.degraded_reasons = degraded_reasons
        _write_model(project_dir / STAGE_FILES["hierarchy"], timeline)
    report = ProcessingCostReport(
        project=project,
        quality_status="degraded" if degraded_reasons else "full",
        degraded_reasons=degraded_reasons,
        stages=costs,
        total_latency_ms=total_latency,
        cache_hits=sum(item.cache_status == "hit" for item in costs),
        cache_misses=sum(item.cache_status == "miss" for item in costs),
        sampled_frames=sampled,
        inspected_seconds=round(scan.duration_ms / 1000.0, 3),
        escalation_count=len(decisions.escalation_requests),
        multimodal_calls=0,
        text_model_calls=0,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=0.0,
    )
    _write_model(project_dir / "perception_cost_report.json", report)
    click.echo(
        json.dumps(
            {
                "project": project,
                "timeline": STAGE_FILES["hierarchy"],
                "nodes": len(timeline.nodes),
                "boundaries": len(candidates.candidates),
                "cache_hits": report.cache_hits,
                "cost_usd": report.estimated_cost_usd,
                "quality_status": report.quality_status,
            },
            ensure_ascii=False,
        )
    )
    return project_dir / STAGE_FILES["hierarchy"]


def evaluate_perception(
    project_dir: Path,
    annotation_path: Path,
    *,
    tolerance_ms: int = 250,
) -> Path:
    """Evaluate boundary timing and structural coverage against a tiny JSON label set."""
    annotations = _read_json(annotation_path)
    expected = sorted(
        int(item["timestamp_ms"]) for item in annotations.get("boundaries", [])
    )
    candidates = BoundaryCandidatesArtifact.model_validate_json(
        (project_dir / STAGE_FILES["boundary_proposal"]).read_text(encoding="utf-8")
    )
    decisions = BoundaryDecisionsArtifact.model_validate_json(
        (project_dir / STAGE_FILES["boundary_judgment"]).read_text(encoding="utf-8")
    )
    timeline = HierarchicalTimeline.model_validate_json(
        (project_dir / STAGE_FILES["hierarchy"]).read_text(encoding="utf-8")
    )
    cost = ProcessingCostReport.model_validate_json(
        (project_dir / "perception_cost_report.json").read_text(encoding="utf-8")
    )
    predicted = sorted(item.timestamp_ms for item in candidates.candidates)
    remaining = set(range(len(expected)))
    errors: list[int] = []
    for timestamp in predicted:
        if not remaining:
            break
        match = min(remaining, key=lambda index: abs(expected[index] - timestamp))
        error = abs(expected[match] - timestamp)
        if error <= tolerance_ms:
            remaining.remove(match)
            errors.append(error)
    true_positive = len(errors)
    precision = true_positive / len(predicted) if predicted else 0.0
    recall = true_positive / len(expected) if expected else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    levels = {str(node.level) for node in timeline.nodes}
    parent_links = [
        node.parent_id for node in timeline.nodes if node.level != "chapter"
    ]
    valid_parent_links = sum(
        parent_id in {node.node_id for node in timeline.nodes}
        for parent_id in parent_links
    )
    duplicate_segments = sum(
        observation.features.quality.duplicate_likelihood >= 0.5
        for observation in ScanArtifact.model_validate_json(
            (project_dir / STAGE_FILES["scan"]).read_text(encoding="utf-8")
        ).observations
    )
    payload = {
        "schema_version": "1.0",
        "project": timeline.project,
        "tolerance_ms": tolerance_ms,
        "proposed_boundaries": len(predicted),
        "accepted_boundaries": sum(
            item.decision == "split" for item in decisions.decisions
        ),
        "merged_boundaries": sum(
            item.decision == "merge" for item in decisions.decisions
        ),
        "uncertain_boundaries": sum(
            item.decision == "uncertain" for item in decisions.decisions
        ),
        "boundary_precision": round(precision, 4),
        "boundary_recall": round(recall, 4),
        "boundary_f1": round(f1, 4),
        "mean_absolute_boundary_error_ms": round(
            _mean([float(error) for error in errors]), 2
        ),
        "hierarchy_levels_present": sorted(levels),
        "hierarchy_parent_link_rate": round(
            valid_parent_links / len(parent_links) if parent_links else 1.0, 4
        ),
        "segment_coverage": round(
            sum(
                source_range.end_ms - source_range.start_ms
                for node in timeline.nodes
                if node.level == "atomic_event"
                for source_range in node.source_ranges
            )
            / max(1, timeline.duration_ms),
            4,
        ),
        "duplicate_segment_rate": round(
            duplicate_segments
            / max(1, sum(node.level == "atomic_event" for node in timeline.nodes)),
            4,
        ),
        "cache_hit_rate": round(
            cost.cache_hits / max(1, cost.cache_hits + cost.cache_misses), 4
        ),
        "high_cost_inspections": cost.multimodal_calls,
        "tokens": {
            "input": cost.input_tokens,
            "cached_input": cost.cached_input_tokens,
            "output": cost.output_tokens,
        },
        "estimated_cost_usd": cost.estimated_cost_usd,
        "total_processing_time_ms": cost.total_latency_ms,
        "cost_per_processed_minute_usd": round(
            cost.estimated_cost_usd / max(timeline.duration_ms / 60_000.0, 1e-9), 6
        ),
    }
    out = project_dir / "perception_evaluation.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


@click.command()
@click.option("--work-dir", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--project", required=True)
@click.option("--footage-dir", type=click.Path(path_type=Path), default=None)
@click.option("--target-duration", type=float, default=30.0)
@click.option(
    "--platform",
    type=click.Choice(
        ["youtube_shorts", "instagram_reels", "tiktok", "youtube", "other"]
    ),
    default="other",
)
@click.option(
    "--pacing", type=click.Choice(["slow", "balanced", "fast"]), default="balanced"
)
@click.option("--max-frames", type=int, default=80)
@click.option("--force", is_flag=True, default=False)
def main(
    work_dir: Path,
    project: str,
    footage_dir: Path | None,
    target_duration: float,
    platform: str,
    pacing: str,
    max_frames: int,
    force: bool,
) -> None:
    goal = EditingGoal.model_validate(
        {
            "target_duration_seconds": target_duration,
            "platform": platform,
            "pacing": pacing,
        }
    )
    out = run_adaptive_perception(
        work_dir,
        project,
        footage_dir=footage_dir,
        editing_goal=goal,
        budget=AnalysisBudget(max_representative_frames=max_frames),
        force=force,
    )
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
