"""Shared multimodal helpers (patterns from tag.py / select_ai.py)."""

from __future__ import annotations

import base64
from pathlib import Path


def encode_image_block(path: Path) -> dict:
    """Anthropic Messages image content block (base64)."""
    media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def encode_image_data_url(path: Path) -> str:
    """OpenAI-style data URL for Responses API input_image."""
    media_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{data}"
