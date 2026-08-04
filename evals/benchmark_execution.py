"""Reproducible fake-task timing report for the bounded pipeline executor."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

from pipeline.execution import DagExecutor, ExecutionLimits, PipelineTask

TASK_MS = 60
STAGES = ("split", "beats", "evidence", "features")


async def _fake_work() -> None:
    await asyncio.sleep(TASK_MS / 1000)


async def _parallel(cached: bool = False) -> tuple[float, dict[str, object]]:
    started = time.perf_counter()
    runs = await DagExecutor(ExecutionLimits(cpu=2, ffmpeg=2)).run(
        [
            PipelineTask(
                stage,
                stage,
                _fake_work,
                resource_class="ffmpeg" if stage == "split" else "cpu",
                is_cached=(lambda: True) if cached else None,
            )
            for stage in STAGES
        ]
    )
    return (time.perf_counter() - started) * 1000, {
        "states": {task_id: run.state for task_id, run in runs.items()},
        "max_configured_concurrency": 2,
    }


def main() -> None:
    started = time.perf_counter()
    for _ in STAGES:
        asyncio.run(_fake_work())
    sequential_ms = (time.perf_counter() - started) * 1000
    cold_ms, cold = asyncio.run(_parallel())
    warm_ms, warm = asyncio.run(_parallel(cached=True))
    artifact_hash = hashlib.sha256("|".join(STAGES).encode()).hexdigest()
    print(
        json.dumps(
            {
                "fixture_task_ms": TASK_MS,
                "stages": STAGES,
                "sequential_ms": round(sequential_ms, 2),
                "cold_dag_ms": round(cold_ms, 2),
                "warm_cache_ms": round(warm_ms, 2),
                "critical_path_ms": TASK_MS,
                "cold": cold,
                "warm": warm,
                "output_artifact_hash": artifact_hash,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
