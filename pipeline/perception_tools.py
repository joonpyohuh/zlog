"""Bounded query interface over adaptive-perception artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from pipeline.perception_models import (
    BoundaryDecisionsArtifact,
    HierarchicalTimeline,
    ProcessingCostReport,
    ScanArtifact,
    TimelineNode,
)

ToolName = Literal[
    "search_events",
    "get_timeline_summary",
    "get_segment",
    "get_neighbor_segments",
    "inspect_clip",
    "inspect_lighting",
    "inspect_motion",
    "inspect_expression",
    "inspect_audio",
    "compare_segments",
    "request_boundary_judgment",
    "request_merge_judgment",
    "find_similar_segments",
    "get_cost_report",
]


class ToolRequest(BaseModel):
    schema_version: str = "1.0"
    tool: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)


class PerceptionToolbox:
    """Read-only agent tools with hard result caps and compact responses."""

    MAX_RESULTS = 20

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.timeline = HierarchicalTimeline.model_validate_json(
            (project_dir / "hierarchical_timeline.json").read_text(encoding="utf-8")
        )
        self.scan = ScanArtifact.model_validate_json(
            (project_dir / "perception_scan.json").read_text(encoding="utf-8")
        )
        self.decisions = BoundaryDecisionsArtifact.model_validate_json(
            (project_dir / "boundary_decisions.json").read_text(encoding="utf-8")
        )
        self.cost = ProcessingCostReport.model_validate_json(
            (project_dir / "perception_cost_report.json").read_text(encoding="utf-8")
        )
        self._nodes = {node.node_id: node for node in self.timeline.nodes}
        self._observations = {item.segment_id: item for item in self.scan.observations}

    def execute(self, request: ToolRequest | dict[str, Any]) -> dict[str, Any]:
        parsed = (
            request
            if isinstance(request, ToolRequest)
            else ToolRequest.model_validate(request)
        )
        handler = getattr(self, parsed.tool)
        return {
            "schema_version": "1.0",
            "tool": parsed.tool,
            "result": handler(**parsed.arguments),
        }

    @staticmethod
    def _compact(node: TimelineNode) -> dict[str, Any]:
        return {
            "node_id": node.node_id,
            "level": node.level,
            "source_ranges": [item.model_dump() for item in node.source_ranges],
            "parent_id": node.parent_id,
            "child_ids": node.child_ids,
            "categories": dict(
                sorted(node.categories.items(), key=lambda item: -item[1])[:4]
            ),
            "representative_frame_ids": node.representative_frame_ids[:6],
            "narrative_role": node.narrative_role,
            "confidence": node.confidence.model_dump(),
        }

    def _limit(self, requested: int = 5) -> int:
        return max(1, min(int(requested), self.MAX_RESULTS))

    def _node_for_segment(self, segment_id: str) -> TimelineNode | None:
        if segment_id in self._nodes:
            return self._nodes[segment_id]
        observation = self._observations.get(segment_id)
        if observation:
            return next(
                (
                    node
                    for node in self.timeline.nodes
                    if observation.observation_id in node.feature_observation_ids
                    and node.level == "atomic_event"
                ),
                None,
            )
        return None

    def search_events(self, query: str, topK: int = 5) -> list[dict[str, Any]]:
        terms = {
            term for term in query.lower().replace("_", " ").split() if len(term) > 2
        }
        scored: list[tuple[float, TimelineNode]] = []
        for node in self.timeline.nodes:
            if node.level not in ("atomic_event", "semantic_scene", "narrative_beat"):
                continue
            text = (
                " ".join([*node.categories, node.narrative_role or ""])
                .replace("_", " ")
                .lower()
            )
            lexical = sum(term in text for term in terms)
            category = max(node.categories.values(), default=0.0)
            scored.append((lexical * 2.0 + category, node))
        scored.sort(key=lambda item: (-item[0], item[1].node_id))
        return [
            self._compact(node) | {"match_score": round(score, 4)}
            for score, node in scored[: self._limit(topK)]
        ]

    def get_timeline_summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for node in self.timeline.nodes:
            counts[str(node.level)] = counts.get(str(node.level), 0) + 1
        return {
            "project": self.timeline.project,
            "duration_ms": self.timeline.duration_ms,
            "editing_goal": self.timeline.editing_goal.model_dump(),
            "level_counts": counts,
            "boundary_count": len(self.timeline.boundary_candidate_ids),
            "quality_status": self.timeline.quality_status,
            "degraded_reasons": self.timeline.degraded_reasons,
        }

    def get_segment(self, segmentId: str) -> dict[str, Any]:
        node = self._node_for_segment(segmentId)
        return (
            self._compact(node)
            if node
            else {"status": "not_found", "segment_id": segmentId}
        )

    def get_neighbor_segments(
        self, segmentId: str, count: int = 1
    ) -> list[dict[str, Any]]:
        node = self._node_for_segment(segmentId)
        if not node:
            return []
        peers = [item for item in self.timeline.nodes if item.level == node.level]
        index = next(
            index for index, item in enumerate(peers) if item.node_id == node.node_id
        )
        width = self._limit(count)
        return [
            self._compact(item)
            for item in peers[
                max(0, index - width) : min(len(peers), index + width + 1)
            ]
            if item.node_id != node.node_id
        ]

    def inspect_clip(
        self,
        segmentId: str,
        reason: str = "",
        sampleFps: float = 2.0,
        includeAudio: bool = True,
    ) -> dict[str, Any]:
        node = self._node_for_segment(segmentId)
        if not node:
            return {"status": "not_found", "segment_id": segmentId}
        return {
            "status": "local_evidence_only",
            "reason": reason[:240],
            "requested_sample_fps": min(max(sampleFps, 0.1), 8.0),
            "include_audio": includeAudio,
            "source_ranges": [item.model_dump() for item in node.source_ranges],
            "evidence_frame_ids": node.representative_frame_ids[:8],
            "note": "No paid reinspection was invoked; use returned IDs with a configured provider.",
        }

    def _feature(self, segment_id: str, group: str) -> dict[str, Any]:
        observation = self._observations.get(segment_id)
        if not observation:
            return {"status": "not_found", "segment_id": segment_id}
        return {
            "status": "available",
            "segment_id": segment_id,
            "feature": getattr(observation.features, group).model_dump(),
            "provenance": [item.model_dump() for item in observation.provenance],
        }

    def inspect_lighting(self, segmentId: str) -> dict[str, Any]:
        return self._feature(segmentId, "lighting")

    def inspect_motion(self, segmentId: str) -> dict[str, Any]:
        return self._feature(segmentId, "motion")

    def inspect_audio(self, segmentId: str) -> dict[str, Any]:
        return self._feature(segmentId, "audio")

    def inspect_expression(self, segmentId: str) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "segment_id": segmentId,
            "reason": "expression extraction is scaffolded for selective visual inspection",
        }

    def compare_segments(self, segmentIds: list[str]) -> list[dict[str, Any]]:
        return [
            self.get_segment(segment_id)
            for segment_id in segmentIds[: self.MAX_RESULTS]
        ]

    def _judgment(self, candidate_id: str, requested: str) -> dict[str, Any]:
        decision = next(
            (
                item
                for item in self.decisions.decisions
                if item.candidate_id == candidate_id
            ),
            None,
        )
        escalation = next(
            (
                item
                for item in self.decisions.escalation_requests
                if item.candidate_id == candidate_id
            ),
            None,
        )
        return {
            "requested": requested,
            "decision": decision.model_dump() if decision else None,
            "escalation": escalation.model_dump() if escalation else None,
        }

    def request_boundary_judgment(self, candidateId: str) -> dict[str, Any]:
        return self._judgment(candidateId, "boundary_judgment")

    def request_merge_judgment(self, candidateId: str) -> dict[str, Any]:
        return self._judgment(candidateId, "merge_judgment")

    def find_similar_segments(
        self, segmentId: str, topK: int = 5
    ) -> list[dict[str, Any]]:
        source = self._node_for_segment(segmentId)
        if not source:
            return []
        peers = [
            node
            for node in self.timeline.nodes
            if node.level == source.level and node.node_id != source.node_id
        ]
        scored = []
        for node in peers:
            keys = set(source.categories) | set(node.categories)
            distance = (
                sum(
                    abs(source.categories.get(key, 0.0) - node.categories.get(key, 0.0))
                    for key in keys
                )
                / 2.0
            )
            scored.append((1.0 - min(1.0, distance), node))
        scored.sort(key=lambda item: (-item[0], item[1].node_id))
        return [
            self._compact(node) | {"similarity": round(score, 4)}
            for score, node in scored[: self._limit(topK)]
        ]

    def get_cost_report(self) -> dict[str, Any]:
        return self.cost.model_dump()


def run_tool(project_dir: Path, request: dict[str, Any]) -> dict[str, Any]:
    """Convenience function for server/agent adapters."""
    return PerceptionToolbox(project_dir).execute(request)


def load_request(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
