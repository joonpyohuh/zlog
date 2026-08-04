"""OpenAI Responses API provider — Structured Outputs + Pydantic validation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pipeline.ai.json_schema import openai_response_format
from pipeline.ai.media import encode_image_data_url
from pipeline.ai.schemas import (
    AssetAnalysis,
    PlanEvaluation,
    StoryPlan,
    story_plan_against_candidates,
)
from pipeline.ai.usage import CallUsage, UsageTimer, record_usage


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        *,
        client: Any | None = None,
        api_key: str | None = None,
        default_model: str = "gpt-5.6-luna",
    ) -> None:
        self.default_model = default_model
        if client is not None:
            self._client = client
        else:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "openai package is required for OpenAIProvider — uv add openai"
                ) from exc
            key = api_key or os.getenv("OPENAI_API_KEY")
            if not key:
                raise RuntimeError("OPENAI_API_KEY is not configured")
            self._client = OpenAI(api_key=key)

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
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": (
                    "Analyze each evidence frame. Use ONLY these segment_ids.\n"
                    f"{catalog}\n"
                    "Return JSON matching AssetAnalysisList. Never invent timestamps."
                ),
            }
        ]
        for path in image_paths:
            content.append({"type": "input_image", "image_url": encode_image_data_url(path)})

        # Wrapper object for array structured output.
        class _AssetList:
            @staticmethod
            def model_json_schema() -> dict[str, Any]:
                return {
                    "type": "object",
                    "properties": {
                        "analyses": {
                            "type": "array",
                            "items": AssetAnalysis.model_json_schema(),
                        }
                    },
                    "required": ["analyses"],
                    "additionalProperties": False,
                }

        raw, usage = self._structured_call(
            model=model_id,
            content=content,
            schema_name="asset_analysis_list",
            schema_model=_AssetList,
            operation="analyze_assets",
        )
        analyses = [AssetAnalysis.model_validate(item) for item in raw.get("analyses") or []]
        for a in analyses:
            if a.segment_id not in allowed_segment_ids:
                raise ValueError(f"analyze_assets: unknown segment_id {a.segment_id!r}")
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
        content = [
            {
                "type": "input_text",
                "text": (
                    f"User intent:\n{user_intent}\n\n"
                    f"Target duration seconds: {target_duration_sec}\n"
                    f"Allowed segment_ids: {sorted(allowed_segment_ids)}\n"
                    f"Analyses:\n{json.dumps([a.model_dump(mode='json') for a in analyses], ensure_ascii=False)}\n"
                    "Return a StoryPlan JSON. Use only allowed segment_ids."
                ),
            }
        ]
        raw, usage = self._structured_call(
            model=model_id,
            content=content,
            schema_name="story_plan",
            schema_model=StoryPlan,
            operation="create_story_plan",
        )
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
    ) -> tuple[PlanEvaluation, CallUsage]:
        model_id = model or self.default_model
        content = [
            {
                "type": "input_text",
                "text": (
                    f"User intent:\n{user_intent}\n\n"
                    f"Plan:\n{plan.model_dump_json()}\n\n"
                    f"Analyses:\n{json.dumps([a.model_dump(mode='json') for a in analyses], ensure_ascii=False)}\n"
                    f"Allowed segment_ids: {sorted(allowed_segment_ids)}\n"
                    "Independently evaluate. Flag prompt leakage if the user intent "
                    "is used as an on-screen caption. Return PlanEvaluation JSON."
                ),
            }
        ]
        raw, usage = self._structured_call(
            model=model_id,
            content=content,
            schema_name="plan_evaluation",
            schema_model=PlanEvaluation,
            operation="evaluate_plan",
        )
        return PlanEvaluation.model_validate(raw), usage

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
                "type": "input_text",
                "text": (
                    f"User intent:\n{user_intent}\n\n"
                    f"Current plan:\n{plan.model_dump_json()}\n\n"
                    f"Evaluation:\n{evaluation.model_dump_json()}\n\n"
                    f"Allowed segment_ids: {sorted(allowed_segment_ids)}\n"
                    "Repair the plan and return StoryPlan JSON."
                ),
            }
        ]
        raw, usage = self._structured_call(
            model=model_id,
            content=content,
            schema_name="story_plan",
            schema_model=StoryPlan,
            operation="repair_story_plan",
        )
        repaired = StoryPlan.model_validate(raw)
        story_plan_against_candidates(repaired, allowed_segment_ids)
        return repaired, usage

    def _structured_call(
        self,
        *,
        model: str,
        content: list[dict[str, Any]],
        schema_name: str,
        schema_model: type,
        operation: str,
        retry_count: int = 0,
    ) -> tuple[dict[str, Any], CallUsage]:
        timer = UsageTimer()
        usage = CallUsage(
            provider="openai",
            model=model,
            operation=operation,
            retry_count=retry_count,
        )
        fmt = openai_response_format(schema_name, schema_model)
        try:
            response = self._client.responses.create(
                model=model,
                input=[{"role": "user", "content": content}],
                text={"format": fmt},
            )
        except Exception as exc:
            usage.ok = False
            usage.error = str(exc)[:300]
            usage.latency_ms = timer.ms()
            record_usage(usage)
            raise

        usage.latency_ms = timer.ms()
        resp_usage = getattr(response, "usage", None)
        if resp_usage is not None:
            usage.input_tokens = int(getattr(resp_usage, "input_tokens", 0) or 0)
            usage.output_tokens = int(getattr(resp_usage, "output_tokens", 0) or 0)
            details = getattr(resp_usage, "input_tokens_details", None)
            if details is not None:
                usage.cache_tokens = int(getattr(details, "cached_tokens", 0) or 0)

        text = getattr(response, "output_text", None)
        if not text:
            # Fallback walk output items
            chunks: list[str] = []
            for item in getattr(response, "output", None) or []:
                for part in getattr(item, "content", None) or []:
                    if getattr(part, "type", None) in ("output_text", "text"):
                        chunks.append(getattr(part, "text", "") or "")
            text = "".join(chunks)
        if not text:
            usage.ok = False
            usage.error = "empty structured response"
            record_usage(usage)
            raise RuntimeError("OpenAI structured response was empty")

        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            usage.ok = False
            usage.error = "invalid JSON in structured response"
            record_usage(usage)
            raise RuntimeError("OpenAI structured response was not JSON") from exc

        record_usage(usage)
        return data, usage
