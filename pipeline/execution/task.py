"""Task contracts kept provider-neutral so expensive video vendors stay optional."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

ResourceClass = Literal["cpu", "io", "ffmpeg", "anthropic", "openai", "external_video"]
TaskWork = Callable[[], Any | Awaitable[Any]]


def cache_key(*, source_hash: str, config: dict[str, Any], model: str, prompt_version: str, schema_version: str, stage_version: str) -> str:
    """Stable content key; callers decide where their completed artifact lives."""
    payload = json.dumps(
        {
            "source_hash": source_hash,
            "config": config,
            "model": model,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "stage_version": stage_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class PipelineTask:
    task_id: str
    stage: str
    work: TaskWork
    dependencies: tuple[str, ...] = ()
    resource_class: ResourceClass = "cpu"
    cache_key: str | None = None
    is_cached: Callable[[], bool] | None = None


class ExternalMediaTask(Protocol):
    """Optional external media contract. Production does not select a vendor here."""

    async def submit(self, *, prompt: str, source_paths: list[str]) -> str: ...

    async def wait(self, job_id: str) -> str: ...

    async def cancel(self, job_id: str) -> None: ...
