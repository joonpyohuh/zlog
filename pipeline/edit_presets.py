"""Versioned, declarative editing primitives shared by analysis and planning."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PRESET_TAXONOMY_VERSION = "1.0"
PresetCategory = Literal["caption", "motion", "color", "transition", "overlay", "audio"]
RendererStatus = Literal["implemented", "partial", "planned"]


class ParameterRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum: float
    maximum: float
    unit: str = ""


class PresetDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: PresetCategory
    description: str
    supported_media_types: list[Literal["video", "image", "audio", "text"]]
    parameter_defaults: dict[str, float | str | bool] = Field(default_factory=dict)
    allowed_ranges: dict[str, ParameterRange] = Field(default_factory=dict)
    incompatible_with: list[str] = Field(default_factory=list)
    renderer_status: RendererStatus
    renderer_mapping: str | None = None
    style_tags: list[str] = Field(default_factory=list)


def _preset(
    preset_id: str,
    category: PresetCategory,
    description: str,
    media: list[Literal["video", "image", "audio", "text"]],
    status: RendererStatus,
    mapping: str | None = None,
    *,
    defaults: dict[str, float | str | bool] | None = None,
    ranges: dict[str, ParameterRange] | None = None,
    tags: list[str] | None = None,
) -> PresetDefinition:
    return PresetDefinition(
        id=preset_id,
        category=category,
        description=description,
        supported_media_types=media,
        parameter_defaults=defaults or {},
        allowed_ranges=ranges or {},
        renderer_status=status,
        renderer_mapping=mapping,
        style_tags=tags or [],
    )


PRESET_REGISTRY: dict[str, PresetDefinition] = {
    preset.id: preset
    for preset in [
        _preset(
            "minimal_bottom",
            "caption",
            "Small bottom subtitle with safe margins.",
            ["text"],
            "implemented",
            "Caption kind=subtitle",
            tags=["clean", "documentary"],
        ),
        _preset(
            "centered_statement",
            "caption",
            "Large centered narrative statement.",
            ["text"],
            "implemented",
            "Caption kind=title",
            tags=["hook", "payoff"],
        ),
        _preset(
            "keyword_pop",
            "caption",
            "Short emphasized keyword animation.",
            ["text"],
            "partial",
            "Caption kind=title animation=spring",
            tags=["energy"],
        ),
        _preset(
            "dialogue_clean",
            "caption",
            "Readable dialogue subtitle treatment.",
            ["text"],
            "implemented",
            "Caption kind=subtitle",
            tags=["dialogue"],
        ),
        _preset(
            "static",
            "motion",
            "No editorial camera movement.",
            ["video", "image"],
            "implemented",
            "motion=static",
            tags=["calm"],
        ),
        _preset(
            "slow_push_center",
            "motion",
            "Slow centered push-in.",
            ["video", "image"],
            "implemented",
            "motion=push",
            defaults={"start_scale": 1.0, "end_scale": 1.06},
            ranges={
                "end_scale": ParameterRange(minimum=1.01, maximum=1.15, unit="scale")
            },
            tags=["intimate", "payoff"],
        ),
        _preset(
            "slow_push_subject",
            "motion",
            "Slow push toward the detected subject.",
            ["video", "image"],
            "implemented",
            "motion=push + focus",
            tags=["subject", "payoff"],
        ),
        _preset(
            "pan_left_to_right",
            "motion",
            "Gentle left-to-right editorial pan.",
            ["video", "image"],
            "implemented",
            "motion=pan_right",
            tags=["reveal"],
        ),
        _preset(
            "pan_right_to_left",
            "motion",
            "Gentle right-to-left editorial pan.",
            ["video", "image"],
            "implemented",
            "motion=pan_left",
            tags=["reveal"],
        ),
        _preset(
            "punch_in",
            "motion",
            "Fast scale accent for a beat or hook.",
            ["video", "image"],
            "partial",
            "entry_effect=punchIn",
            tags=["hook", "energy"],
        ),
        _preset(
            "micro_handheld",
            "motion",
            "Subtle handheld texture.",
            ["video", "image"],
            "planned",
            tags=["organic"],
        ),
        _preset(
            "neutral_clean",
            "color",
            "Neutral baseline grade.",
            ["video", "image"],
            "implemented",
            "style LUT/default grade",
            tags=["clean"],
        ),
        _preset(
            "warm_cafe",
            "color",
            "Warm, restrained cafe grade.",
            ["video", "image"],
            "partial",
            "style warm LUT",
            tags=["warm", "lifestyle"],
        ),
        _preset(
            "cool_city",
            "color",
            "Cool urban grade.",
            ["video", "image"],
            "partial",
            "style cool LUT",
            tags=["cool", "city"],
        ),
        _preset(
            "soft_film",
            "color",
            "Soft contrast film-inspired finish.",
            ["video", "image"],
            "partial",
            "aesthetic preset",
            tags=["film", "soft"],
        ),
        _preset(
            "high_contrast_energy",
            "color",
            "High-contrast energetic finish.",
            ["video", "image"],
            "planned",
            tags=["energy"],
        ),
        _preset(
            "clean_cut",
            "transition",
            "Direct cut with no added effect.",
            ["video", "image"],
            "implemented",
            "transition=cut",
            tags=["clean"],
        ),
        _preset(
            "soft_crossfade",
            "transition",
            "Short soft dissolve.",
            ["video", "image"],
            "planned",
            tags=["soft", "reflective"],
        ),
        _preset(
            "quick_flash",
            "transition",
            "Brief flash accent.",
            ["video", "image"],
            "implemented",
            "transition=flash",
            tags=["energy"],
        ),
        _preset(
            "dip_black",
            "transition",
            "Short dip to black between chapters.",
            ["video", "image"],
            "planned",
            tags=["chapter"],
        ),
        _preset(
            "subtle_gradient_bottom",
            "overlay",
            "Bottom readability gradient.",
            ["video", "image"],
            "planned",
            tags=["caption"],
        ),
        _preset(
            "letterbox",
            "overlay",
            "Cinematic letterbox treatment.",
            ["video", "image"],
            "partial",
            "aesthetic preset",
            tags=["cinematic"],
        ),
        _preset(
            "timestamp_label",
            "overlay",
            "Compact timestamp label.",
            ["video", "image", "text"],
            "partial",
            "camcorder aesthetic",
            tags=["memory"],
        ),
        _preset(
            "location_tag",
            "overlay",
            "Compact location label.",
            ["video", "image", "text"],
            "planned",
            tags=["travel"],
        ),
        _preset(
            "music_under_dialogue",
            "audio",
            "Duck music beneath speech.",
            ["audio"],
            "implemented",
            "audio_engine dialogue ducking",
            tags=["dialogue"],
        ),
        _preset(
            "beat_emphasis",
            "audio",
            "Align an edit accent to a detected beat.",
            ["audio", "video"],
            "partial",
            "beat-aligned effect timing",
            tags=["energy"],
        ),
        _preset(
            "soft_intro_fade",
            "audio",
            "Gentle music fade at the opening.",
            ["audio"],
            "planned",
            tags=["soft"],
        ),
        _preset(
            "impact_sfx",
            "audio",
            "Short impact cue on a narrative accent.",
            ["audio", "video"],
            "planned",
            tags=["hook", "payoff"],
        ),
    ]
}


def get_preset(
    preset_id: str, category: PresetCategory | None = None
) -> PresetDefinition:
    """Resolve a known preset and optionally enforce its category."""

    try:
        preset = PRESET_REGISTRY[preset_id]
    except KeyError as exc:
        raise ValueError(f"Unknown edit preset: {preset_id}") from exc
    if category and preset.category != category:
        raise ValueError(f"Preset {preset_id!r} is {preset.category}, not {category}")
    return preset
