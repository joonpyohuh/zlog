"""Small asyncio scheduler: dependency gates + per-resource concurrency caps."""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from .limits import ExecutionLimits
from .task import PipelineTask


class TaskState(StrEnum):
    pending = "pending"
    ready = "ready"
    running = "running"
    completed = "completed"
    failed = "failed"
    skipped_cached = "skipped_cached"
    cancelled = "cancelled"


@dataclass
class TaskRun:
    task_id: str
    stage: str
    state: TaskState = TaskState.pending
    started_at: str | None = None
    ended_at: str | None = None
    wall_ms: float = 0.0
    error: str | None = None
    result: Any = field(default=None, repr=False)

    def public_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "stage": self.stage,
            "state": self.state,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "wall_ms": round(self.wall_ms, 2),
            "error": self.error,
        }


class DagExecutor:
    def __init__(self, limits: ExecutionLimits | None = None, *, strict: bool | None = None) -> None:
        self.limits = limits or ExecutionLimits.from_env()
        self.strict = strict if strict is not None else os.getenv("ZLOG_STRICT_PIPELINE") == "1"
        self._semaphores = {
            name: asyncio.Semaphore(getattr(self.limits, name))
            for name in ("cpu", "io", "ffmpeg", "anthropic", "openai", "external_video")
        }

    async def run(self, tasks: list[PipelineTask]) -> dict[str, TaskRun]:
        by_id = {task.task_id: task for task in tasks}
        if len(by_id) != len(tasks):
            raise ValueError("pipeline task ids must be unique")
        unknown = {dep for task in tasks for dep in task.dependencies if dep not in by_id}
        if unknown:
            raise ValueError(f"unknown pipeline task dependency: {sorted(unknown)}")

        runs = {task.task_id: TaskRun(task.task_id, task.stage) for task in tasks}
        active: dict[asyncio.Task[None], str] = {}
        while len(active) or any(run.state == TaskState.pending for run in runs.values()):
            for task in tasks:
                run = runs[task.task_id]
                if run.state != TaskState.pending:
                    continue
                deps = [runs[dep].state for dep in task.dependencies]
                if any(state in (TaskState.failed, TaskState.cancelled) for state in deps):
                    run.state = TaskState.cancelled
                elif all(state in (TaskState.completed, TaskState.skipped_cached) for state in deps):
                    if task.is_cached and task.is_cached():
                        run.state = TaskState.skipped_cached
                    else:
                        run.state = TaskState.ready
                        active[asyncio.create_task(self._run_task(task, run))] = task.task_id
            if not active:
                if any(run.state == TaskState.pending for run in runs.values()):
                    raise ValueError("pipeline task dependencies contain a cycle")
                break
            done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
            for future in done:
                active.pop(future)
                await future

        failures = [run for run in runs.values() if run.state == TaskState.failed]
        if failures and self.strict:
            raise RuntimeError("pipeline tasks failed: " + ", ".join(run.task_id for run in failures))
        return runs

    async def _run_task(self, task: PipelineTask, run: TaskRun) -> None:
        run.state = TaskState.running
        run.started_at = datetime.now(UTC).isoformat()
        started = time.perf_counter()
        try:
            async with self._semaphores[task.resource_class]:
                if inspect.iscoroutinefunction(task.work):
                    run.result = await task.work()
                else:
                    value = await asyncio.to_thread(task.work)
                    run.result = await value if inspect.isawaitable(value) else value
            run.state = TaskState.completed
        except asyncio.CancelledError:
            run.state = TaskState.cancelled
            raise
        except Exception as exc:  # noqa: BLE001 - captured for independent branches
            run.state = TaskState.failed
            run.error = str(exc)[:300]
        finally:
            run.wall_ms = (time.perf_counter() - started) * 1000
            run.ended_at = datetime.now(UTC).isoformat()
