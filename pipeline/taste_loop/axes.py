"""Preset registry — the axes we are allowed to shake, and their bounds.

One axis = one editing decision that can be expressed as a single number on
an existing EDL field. A variant round shakes exactly one of these and holds
everything else fixed, so a pick is attributable.

`ALLOWED` is the hard registry bound: a value outside it is a programming
error and raises AxisRangeError. We never silently clamp — a quietly clamped
value would make the logged `axis_value` disagree with what was rendered, and
the whole point of the log is that those two agree.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class TasteAxis(str, Enum):
    """The editing dimensions the comparison loop can vary."""

    avg_cut_duration = "avg_cut_duration"
    caption_frequency = "caption_frequency"
    push_in_strength = "push_in_strength"
    non_hard_cut_ratio = "non_hard_cut_ratio"


class AxisRangeError(ValueError):
    """Raised when a value falls outside an axis's registry-allowed range."""


class AxisRange(BaseModel):
    """A closed interval [low, high] on one axis."""

    low: float
    high: float

    @model_validator(mode="after")
    def _ordered(self) -> AxisRange:
        if self.high < self.low:
            raise ValueError(f"AxisRange high ({self.high}) is below low ({self.low})")
        return self

    @property
    def span(self) -> float:
        return self.high - self.low

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2.0

    def contains(self, value: float, *, tol: float = 1e-9) -> bool:
        return self.low - tol <= value <= self.high + tol

    def sample(self, n: int) -> list[float]:
        """n evenly spaced points across the range, endpoints included.

        A degenerate (zero-span) range yields the same value n times — that is
        a legitimate state once an axis has been narrowed hard, and the caller
        decides whether it is still worth rendering.
        """
        if n < 1:
            raise ValueError("n must be >= 1")
        if n == 1:
            return [round(self.mid, 4)]
        step = self.span / (n - 1)
        return [round(self.low + step * i, 4) for i in range(n)]


class AxisSpec(BaseModel):
    """Registry entry for one axis."""

    axis: TasteAxis
    allowed: AxisRange
    unit: str
    description: str
    # Narrowing never shrinks an axis below this, so the loop can keep
    # producing four visibly different variants instead of collapsing to one.
    min_span: float = Field(gt=0)

    def validate_value(self, value: float, *, context: str = "") -> float:
        """Return `value` if it is inside `allowed`, else raise AxisRangeError."""
        if not self.allowed.contains(value):
            where = f" ({context})" if context else ""
            raise AxisRangeError(
                f"{self.axis.value}={value} is outside the allowed range "
                f"[{self.allowed.low}, {self.allowed.high}]{where}"
            )
        return value

    def validate_range(self, rng: AxisRange, *, context: str = "") -> AxisRange:
        self.validate_value(rng.low, context=context or "range low")
        self.validate_value(rng.high, context=context or "range high")
        return rng


REGISTRY: dict[TasteAxis, AxisSpec] = {
    TasteAxis.avg_cut_duration: AxisSpec(
        axis=TasteAxis.avg_cut_duration,
        allowed=AxisRange(low=0.4, high=4.0),
        unit="seconds",
        description="Average played duration of one cut before beat snapping.",
        min_span=0.3,
    ),
    TasteAxis.caption_frequency: AxisSpec(
        axis=TasteAxis.caption_frequency,
        allowed=AxisRange(low=0.0, high=1.0),
        unit="fraction of clips",
        description="Fraction of timeline clips that carry an on-screen caption.",
        min_span=0.15,
    ),
    TasteAxis.push_in_strength: AxisSpec(
        axis=TasteAxis.push_in_strength,
        allowed=AxisRange(low=0.0, high=0.8),
        unit="motion_strength (0-1)",
        description="Ken Burns push-in strength applied to each clip.",
        min_span=0.12,
    ),
    TasteAxis.non_hard_cut_ratio: AxisSpec(
        axis=TasteAxis.non_hard_cut_ratio,
        allowed=AxisRange(low=0.0, high=0.6),
        unit="fraction of transitions",
        description="Fraction of clip entries that use a flash instead of a hard cut.",
        min_span=0.12,
    ),
}

VARIANTS_PER_ROUND = 4


def spec_for(axis: TasteAxis | str) -> AxisSpec:
    key = TasteAxis(axis) if not isinstance(axis, TasteAxis) else axis
    try:
        return REGISTRY[key]
    except KeyError as exc:  # pragma: no cover — TasteAxis() already guards
        raise AxisRangeError(f"no registry entry for axis {key!r}") from exc


def all_axes() -> list[TasteAxis]:
    return list(REGISTRY)
