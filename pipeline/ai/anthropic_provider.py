"""Anthropic Messages API provider — forced tool use + Pydantic validation.

Reuses the encode_image / tool_choice patterns from pipeline/tag.py and
pipeline/select_ai.py without changing those modules.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import anthropic

from pipeline.ai.json_schema import (
    SUBMIT_ASSET_ANALYSES_TOOL,
    SUBMIT_PLAN_EVALUATION_TOOL,
    SUBMIT_STORY_PLAN_TOOL,
)
from pipeline.ai.media import encode_image_block
from pipeline.ai.schemas import (
    AssetAnalysis,
    PlanEvaluation,
    StoryPlan,
    TimelinePlan,
    story_plan_against_candidates,
)
from pipeline.ai.usage import CallUsage, UsageTimer, record_usage


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        client: anthropic.Anthropic | None = None,
        api_key: str | None = None,
        default_model: str = "claude-sonnet-4-5-20250929",
        max_tokens: int = 4096,
    ) -> None:
        self.default_model = default_model
        self.max_tokens = max_tokens
        if client is not None:
            self._client = client
        else:
            key = api_key or os.getenv("ANTHROPIC_API_KEY")
            if not key:
                raise RuntimeError("ANTHROPIC_API_KEY is not configured")
            self._client = anthropic.Anthropic(api_key=key)

    def analyze_assets(
        self,
        *,
        image_paths: list[Path],
        segment_ids: list[str],
        source_files: list[str],
        allowed_segment_ids: set[str],
        model: str | None = None,
    ) -> tuple[list[AssetAnalysis], CallUsage]:
        if not (len(image_paths) == len(segment_ids) == len(source_files)):
            raise ValueError("image_paths/segment_ids/source_files length mismatch")
        unknown = sorted(set(segment_ids) - allowed_segment_ids)
        if unknown:
            raise ValueError(f"analyze_assets: unknown segment_id(s): {unknown}")

        model_id = model or self.default_model
        catalog = "\n".join(
            f"- segment_id={sid} source_file={src} upload_index={i}"
            for i, (sid, src) in enumerate(zip(segment_ids, source_files))
        )
        content: list[dict[str, Any]] = []
        for path in image_paths:
            content.append(encode_image_block(path))
        content.append(
            {
                "type": "text",
                "text": (
                    "Analyze each evidence frame. Use ONLY these segment_ids.\n"
                    f"{catalog}\n"
                    "Never invent source timestamps. Call submit_asset_analyses."
                ),
            }
        )
        raw, usage = self._tool_call(
            model=model_id,
            tools=[SUBMIT_ASSET_ANALYSES_TOOL],
            tool_name="submit_asset_analyses",
            messages=[{"role": "user", "content": content}],
            operation="analyze_assets",
        )
        items = raw.get("analyses") or []
        analyses = [AssetAnalysis.model_validate(item) for item in items]
        for a in analyses:
            if a.segment_id not in allowed_segment_ids:
                raise ValueError(f"analyze_assets: model returned unknown segment_id {a.segment_id!r}")
        return analyses, usage

    def create_story_plan(
        self,
        *,
        user_intent: str,
        analyses: list[AssetAnalysis],
        allowed_segment_ids: set[str],
        target_duration_sec: float,
        model: str | None = None,
    ) -> tuple[StoryPlan, CallUsage]:
        model_id = model or self.default_model
        payload = [a.model_dump(mode="json") for a in analyses]
        content = [
            {
                "type": "text",
                "text": (
                    f"User intent:\n{user_intent}\n\n"
                    f"Target duration seconds: {target_duration_sec}\n"
                    f"Allowed segment_ids: {sorted(allowed_segment_ids)}\n"
                    f"Asset analyses JSON:\n{json.dumps(payload, ensure_ascii=False)}\n"
                    "Create a story plan using ONLY allowed segment_ids. "
                    "Call submit_story_plan. Never invent timestamps."
                ),
            }
        ]
        raw, usage = self._tool_call(
            model=model_id,
            tools=[SUBMIT_STORY_PLAN_TOOL],
            tool_name="submit_story_plan",
            messages=[{"role": "user", "content": content}],
            operation="create_story_plan",
        )
        # Tool schema is the StoryPlan object itself.
        plan = StoryPlan.model_validate(raw)
        story_plan_against_candidates(plan, allowed_segment_ids)
        return plan, usage

    def evaluate_plan(
        self,
        *,
        user_intent: str,
        plan: StoryPlan,
        analyses: list[AssetAnalysis],
        allowed_segment_ids: set[str],
        model: str | None = None,
        timeline: TimelinePlan | None = None,
        image_paths: list[Path] | None = None,
    ) -> tuple[PlanEvaluation, CallUsage]:
        model_id = model or self.default_model
        timeline_blob = timeline.model_dump_json() if timeline is not None else "{}"
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "You are an independent plan EVALUATOR, not a new director.\n"
                    "Cite exact segment_ids. Do not invent timestamps or IDs.\n\n"
                    f"User intent:\n{user_intent}\n\n"
                    f"Story plan:\n{plan.model_dump_json()}\n\n"
                    f"TimelinePlan:\n{timeline_blob}\n\n"
                    f"Analyses:\n{json.dumps([a.model_dump(mode='json') for a in analyses], ensure_ascii=False)}\n"
                    f"Allowed segment_ids: {sorted(allowed_segment_ids)}\n"
                    "Independently evaluate. Flag prompt leakage if intent text "
                    "appears as an on-screen caption. Call submit_plan_evaluation."
                ),
            }
        ]
        for path in image_paths or []:
            content.append(encode_image_block(path))
        raw, usage = self._tool_call(
            model=model_id,
            tools=[SUBMIT_PLAN_EVALUATION_TOOL],
            tool_name="submit_plan_evaluation",
            messages=[{"role": "user", "content": content}],
            operation="evaluate_plan",
        )
        evaluation = PlanEvaluation.model_validate(raw)
        return evaluation, usage

    def repair_story_plan(
        self,
        *,
        user_intent: str,
        plan: StoryPlan,
        evaluation: PlanEvaluation,
        analyses: list[AssetAnalysis],
        allowed_segment_ids: set[str],
        model: str | None = None,
    ) -> tuple[StoryPlan, CallUsage]:
        model_id = model or self.default_model
        content = [
            {
                "type": "text",
                "text": (
                    f"User intent:\n{user_intent}\n\n"
                    f"Current plan:\n{plan.model_dump_json()}\n\n"
                    f"Evaluation:\n{evaluation.model_dump_json()}\n\n"
                    f"Allowed segment_ids: {sorted(allowed_segment_ids)}\n"
                    "Repair the plan. Call submit_story_plan with a fixed plan."
                ),
            }
        ]
        raw, usage = self._tool_call(
            model=model_id,
            tools=[SUBMIT_STORY_PLAN_TOOL],
            tool_name="submit_story_plan",
            messages=[{"role": "user", "content": content}],
            operation="repair_story_plan",
        )
        repaired = StoryPlan.model_validate(raw)
        story_plan_against_candidates(repaired, allowed_segment_ids)
        return repaired, usage

    def _tool_call(
        self,
        *,
        model: str,
        tools: list[dict[str, Any]],
        tool_name: str,
        messages: list[dict[str, Any]],
        operation: str,
        retry_count: int = 0,
    ) -> tuple[dict[str, Any], CallUsage]:
        timer = UsageTimer()
        usage = CallUsage(
            provider="anthropic",
            model=model,
            operation=operation,
            retry_count=retry_count,
        )
        try:
            response = self._client.messages.create(
                model=model,
                max_tokens=self.max_tokens,
                tools=tools,
                tool_choice={"type": "tool", "name": tool_name},
                messages=messages,
            )
        except Exception as exc:
            usage.ok = False
            usage.error = str(exc)[:300]
            usage.latency_ms = timer.ms()
            record_usage(usage)
            raise

        usage.latency_ms = timer.ms()
        if response.usage:
            usage.input_tokens = int(getattr(response.usage, "input_tokens", 0) or 0)
            usage.output_tokens = int(getattr(response.usage, "output_tokens", 0) or 0)
            cache_create = int(getattr(response.usage, "cache_creation_input_tokens", 0) or 0)
            cache_read = int(getattr(response.usage, "cache_read_input_tokens", 0) or 0)
            usage.cache_tokens = cache_create + cache_read

        tool_input: dict[str, Any] | None = None
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
                tool_input = dict(block.input)
                break
        if tool_input is None:
            usage.ok = False
            usage.error = f"missing tool_use {tool_name}"
            record_usage(usage)
            raise RuntimeError(f"Anthropic response missing tool_use {tool_name}")

        record_usage(usage)
        return tool_input, usage
