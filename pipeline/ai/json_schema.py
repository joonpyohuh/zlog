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
        "Submit structured analyses for each provided evidence frame. "
        "Use only the given segment_id values — never invent timestamps."
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
        "Submit a story plan that references only allowed segment_ids. "
        "Do not invent source timestamps."
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
