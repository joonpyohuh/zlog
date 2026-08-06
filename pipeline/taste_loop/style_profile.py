"""StyleProfile — the per-axis value ranges the loop is narrowing toward.

Versioned and append-only: `save_new_version` writes v2, v3, ... and never
touches an earlier file. `load_latest` reads the highest version present.
Every write records what it narrowed and why, so a profile can be traced back
to the rounds that produced it.

Stored under taste/style_profiles/ next to the existing taste/ artifacts.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from pipeline.taste_loop.axes import REGISTRY, AxisRange, TasteAxis, spec_for

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROFILE_DIR = REPO_ROOT / "taste" / "style_profiles"
_VERSION_RE = re.compile(r"^v(\d+)\.json$")


class AxisNarrowing(BaseModel):
    """Audit record for one axis's change between two profile versions."""

    axis: TasteAxis
    before: AxisRange
    after: AxisRange
    sample_size: int
    winner_mean: float
    winner_stdev: float


class StyleProfile(BaseModel):
    """Current allowed range per axis, plus how it got here."""

    version: int = Field(ge=1)
    created_at: str
    base_project: str | None = None
    ranges: dict[TasteAxis, AxisRange]
    narrowed_from_version: int | None = None
    narrowings: list[AxisNarrowing] = Field(default_factory=list)
    note: str = ""

    def range_for(self, axis: TasteAxis | str) -> AxisRange:
        key = TasteAxis(axis) if not isinstance(axis, TasteAxis) else axis
        if key not in self.ranges:
            raise KeyError(f"StyleProfile v{self.version} has no range for axis {key.value!r}")
        return self.ranges[key]

    def validated(self) -> StyleProfile:
        """Assert every range still sits inside the registry's allowed bounds."""
        for axis, rng in self.ranges.items():
            spec_for(axis).validate_range(rng, context=f"StyleProfile v{self.version}")
        return self


def default_profile(*, base_project: str | None = None) -> StyleProfile:
    """v1 — every axis wide open at its registry-allowed range."""
    return StyleProfile(
        version=1,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        base_project=base_project,
        ranges={axis: spec.allowed.model_copy() for axis, spec in REGISTRY.items()},
        note="initial profile — full registry range on every axis",
    )


def _profile_path(version: int, profile_dir: Path | None = None) -> Path:
    return (profile_dir or PROFILE_DIR) / f"v{version}.json"


def existing_versions(profile_dir: Path | None = None) -> list[int]:
    directory = profile_dir or PROFILE_DIR
    if not directory.exists():
        return []
    out: list[int] = []
    for path in directory.iterdir():
        match = _VERSION_RE.match(path.name)
        if match:
            out.append(int(match.group(1)))
    return sorted(out)


def load_version(version: int, profile_dir: Path | None = None) -> StyleProfile:
    path = _profile_path(version, profile_dir)
    if not path.exists():
        raise FileNotFoundError(f"no StyleProfile at {path}")
    return StyleProfile.model_validate_json(path.read_text(encoding="utf-8")).validated()


def load_latest(profile_dir: Path | None = None, *, create: bool = True) -> StyleProfile:
    """Read the highest-numbered profile, creating v1 if none exists."""
    versions = existing_versions(profile_dir)
    if versions:
        return load_version(versions[-1], profile_dir)
    profile = default_profile()
    if create:
        write_profile(profile, profile_dir)
    return profile


def write_profile(profile: StyleProfile, profile_dir: Path | None = None) -> Path:
    """Write a profile at its own version number. Refuses to overwrite."""
    profile.validated()
    path = _profile_path(profile.version, profile_dir)
    if path.exists():
        raise FileExistsError(
            f"{path} already exists — profiles are append-only, bump the version"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    return path


def save_new_version(
    previous: StyleProfile,
    ranges: dict[TasteAxis, AxisRange],
    narrowings: list[AxisNarrowing],
    *,
    profile_dir: Path | None = None,
    note: str = "",
) -> tuple[StyleProfile, Path]:
    """Persist `ranges` as the next version, leaving `previous` on disk."""
    versions = existing_versions(profile_dir)
    next_version = (max(versions) if versions else previous.version) + 1
    profile = StyleProfile(
        version=next_version,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        base_project=previous.base_project,
        ranges=ranges,
        narrowed_from_version=previous.version,
        narrowings=narrowings,
        note=note,
    )
    return profile, write_profile(profile, profile_dir)
