"""JSON Schema fragments for Anthropic tool use / OpenAI structured outputs."""

from __future__ import annotations

from typing import Any

from pipeline.ai.schemas import (
    AssetAnalysis,
    PlanEvaluation,
    StoryPlan,
)


def pydantic_tool_schema(model: type) -> dict[str, Any]:
    """OpenAPI-ish schema for Anthropic tool input_schema."""
    schema = model.model_json_schema()
    # Anthropic tools want a flat object schema; $defs are ok in modern API.
    return schema


SUBMIT_ASSET_ANALYSES_TOOL: dict[str, Any] = {
    "name": "submit_asset_analyses",
    "description": (
        "Submit one AssetAnalysis per segment temporal bundle "
        "(start/mid/end evidence frames analyzed together). "
        "Use only the given segment_id and evidence_frame_id values — "
        "never invent timestamps or decide the final timeline."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "analyses": {
                "type": "array",
                "items": AssetAnalysis.model_json_schema(),
            }
        },
        "required": ["analyses"],
    },
}

SUBMIT_STORY_PLAN_TOOL: dict[str, Any] = {
    "name": "submit_story_plan",
    "description": (
        "Submit a StoryPlan for the vlog. Schema must match StoryPlan exactly. "
        "Use only allowed segment_ids. Never invent source timestamps or beat times. "
        "Never copy the user edit brief into captions."
    ),
    "input_schema": StoryPlan.model_json_schema(),
}

SUBMIT_PLAN_EVALUATION_TOOL: dict[str, Any] = {
    "name": "submit_plan_evaluation",
    "description": "Submit an independent evaluation of the story plan.",
    "input_schema": PlanEvaluation.model_json_schema(),
}


def openai_response_format(name: str, model: type) -> dict[str, Any]:
    """Responses API Structured Outputs text.format payload."""
    schema = model.model_json_schema()
    # OpenAI requires additionalProperties: false on root for strict mode.
    schema.setdefault("additionalProperties", False)
    return {
        "type": "json_schema",
        "name": name,
        "strict": True,
        "schema": schema,
    }
