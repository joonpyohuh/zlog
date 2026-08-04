"""Fast contract checks for bounded, dependency-aware pipeline execution."""

from __future__ import annotations

import asyncio

from pipeline.execution import (
    DagExecutor,
    ExecutionLimits,
    PipelineTask,
    TaskState,
    cache_key,
)


def test_independent_tasks_are_bounded_and_dependencies_wait() -> None:
    active = 0
    maximum = 0
    finished: list[str] = []

    async def work(name: str) -> str:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.07)
        active -= 1
        finished.append(name)
        return name

    runs = asyncio.run(
        DagExecutor(ExecutionLimits(cpu=2)).run(
            [
                PipelineTask("a", "evidence", lambda: work("a")),
                PipelineTask("b", "features", lambda: work("b")),
                PipelineTask("c", "filter", lambda: work("c"), dependencies=("a", "b")),
            ]
        )
    )

    assert maximum == 2
    assert finished[-1] == "c"
    assert runs["c"].state == TaskState.completed
    assert runs["a"].started_at and runs["a"].ended_at and runs["a"].wall_ms > 0


def test_failure_blocks_dependents_but_not_other_branches_or_cache() -> None:
    completed: list[str] = []

    def fail() -> None:
        raise RuntimeError("boom")

    def independent() -> None:
        completed.append("independent")

    runs = asyncio.run(
        DagExecutor(strict=False).run(
            [
                PipelineTask("bad", "analyze", fail, resource_class="anthropic"),
                PipelineTask("blocked", "director", lambda: completed.append("blocked"), dependencies=("bad",)),
                PipelineTask("other", "beats", independent),
                PipelineTask("cached", "evidence", lambda: completed.append("cached"), is_cached=lambda: True),
            ]
        )
    )

    assert completed == ["independent"]
    assert runs["bad"].state == TaskState.failed
    assert runs["blocked"].state == TaskState.cancelled
    assert runs["other"].state == TaskState.completed
    assert runs["cached"].state == TaskState.skipped_cached


def test_cache_key_includes_all_reproducibility_inputs() -> None:
    common = {
        "source_hash": "source",
        "config": {"quality": "balanced"},
        "model": "claude-haiku",
        "prompt_version": "v1",
        "schema_version": "v1",
        "stage_version": "v1",
    }
    assert cache_key(**common) == cache_key(**common)
    assert cache_key(**common) != cache_key(**(common | {"prompt_version": "v2"}))


class FakeExternalMedia:
    async def submit(self, *, prompt: str, source_paths: list[str]) -> str:
        return f"fake:{len(source_paths)}:{prompt}"

    async def wait(self, job_id: str) -> str:
        return f"preview:{job_id}"

    async def cancel(self, job_id: str) -> None:
        return None


def test_external_media_contract_can_be_faked_without_a_vendor() -> None:
    provider = FakeExternalMedia()

    async def preview() -> str:
        job_id = await provider.submit(prompt="soft flow", source_paths=["a.jpg"])
        return await provider.wait(job_id)

    assert asyncio.run(preview()) == "preview:fake:1:soft flow"
