"""Artifact references + content hashing (PROMPT 1.5)."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from pipeline.ai.agents.permissions import AgentName
from pipeline.ai.agents.schema import (
    ArtifactReference,
    ArtifactType,
    ValidationStatus,
)


def content_hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_hash_path(path: Path) -> str:
    return content_hash_bytes(path.read_bytes())


def content_hash_json(obj: Any) -> str:
    blob = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return content_hash_bytes(blob.encode("utf-8"))


def make_artifact_ref(
    *,
    path: Path | str,
    artifact_type: ArtifactType,
    created_by_agent: AgentName | str,
    schema_version: str = "1",
    source_artifact_ids: list[str] | None = None,
    artifact_id: str | None = None,
    content_hash: str | None = None,
    validation_status: ValidationStatus = ValidationStatus.valid,
) -> ArtifactReference:
    p = Path(path)
    h = content_hash or (content_hash_path(p) if p.is_file() else content_hash_json({"path": str(p)}))
    agent = created_by_agent.value if isinstance(created_by_agent, AgentName) else str(created_by_agent)
    return ArtifactReference(
        artifact_id=artifact_id or f"art_{uuid.uuid4().hex[:12]}",
        artifact_type=artifact_type,
        path=str(p).replace("\\", "/"),
        schema_version=schema_version,
        content_hash=h,
        created_by_agent=agent,
        source_artifact_ids=list(source_artifact_ids or []),
        validation_status=validation_status,
    )


def verify_artifact_fresh(
    ref: ArtifactReference,
    *,
    project_dir: Path | None = None,
) -> ValidationStatus:
    """Re-hash on disk; stale if mismatch."""
    path = Path(ref.path)
    if project_dir is not None and not path.is_file():
        cand = project_dir / path.name
        if cand.is_file():
            path = cand
        else:
            # try relative from project
            alt = project_dir / Path(ref.path).name
            path = alt if alt.is_file() else path
    if not path.is_file():
        # Also accept path relative to cwd / absolute as stored
        if not Path(ref.path).is_file():
            return ValidationStatus.invalid
        path = Path(ref.path)
    current = content_hash_path(path)
    if current != ref.content_hash:
        return ValidationStatus.stale
    return ValidationStatus.valid
