"""Media set registry — the footage bundles rounds can be run against.

Several sets must be registrable and rounds must be able to alternate between
them: repeating one bundle over and over teaches settings that fit *that clip*,
not the founder's taste.

A media set points at an existing pipeline project (footage/<project>/ plus
work/<project>/). Registering does not re-run the pipeline; it records that a
base EDL exists (or is expected) for that project.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REGISTRY_PATH = REPO_ROOT / "taste" / "media_sets.json"


class MediaSet(BaseModel):
    """One registered footage bundle."""

    media_set_id: str
    project: str
    footage_dir: str
    work_dir: str
    bgm_track: str
    label: str = ""
    registered_at: str = ""

    def base_edl_path(self, repo_root: Path | None = None) -> Path:
        """The EDL the normal pipeline already produced for this project.

        Prefers the hybrid output, falls back to the baseline selector — the
        same precedence run.py's render stage uses.
        """
        root = repo_root or REPO_ROOT
        work = root / self.work_dir if not Path(self.work_dir).is_absolute() else Path(self.work_dir)
        for name in ("edl_ai.json", "edl_baseline.json"):
            candidate = work / name
            if candidate.exists():
                return candidate
        raise FileNotFoundError(
            f"media set {self.media_set_id!r}: no edl_ai.json or edl_baseline.json under {work} — "
            f"run the normal pipeline for project {self.project!r} first"
        )

    def candidates_path(self, repo_root: Path | None = None) -> Path:
        root = repo_root or REPO_ROOT
        work = root / self.work_dir if not Path(self.work_dir).is_absolute() else Path(self.work_dir)
        return work / "candidates.json"

    def beats_path(self, repo_root: Path | None = None) -> Path:
        root = repo_root or REPO_ROOT
        return root / "assets" / "bgm" / f"{self.bgm_track}.beats.json"


class MediaSetRegistry(BaseModel):
    sets: list[MediaSet] = Field(default_factory=list)

    def get(self, media_set_id: str) -> MediaSet:
        for item in self.sets:
            if item.media_set_id == media_set_id:
                return item
        known = ", ".join(s.media_set_id for s in self.sets) or "(none registered)"
        raise KeyError(f"unknown media_set_id {media_set_id!r}; registered: {known}")

    def ids(self) -> list[str]:
        return [s.media_set_id for s in self.sets]


def load_registry(path: Path | None = None) -> MediaSetRegistry:
    target = path or REGISTRY_PATH
    if not target.exists():
        return MediaSetRegistry()
    return MediaSetRegistry.model_validate_json(target.read_text(encoding="utf-8"))


def save_registry(registry: MediaSetRegistry, path: Path | None = None) -> Path:
    target = path or REGISTRY_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(registry.model_dump_json(indent=2), encoding="utf-8")
    return target


def register_media_set(
    *,
    media_set_id: str,
    project: str,
    bgm_track: str,
    label: str = "",
    footage_dir: str | None = None,
    work_dir: str | None = None,
    path: Path | None = None,
    replace: bool = False,
) -> MediaSet:
    """Add (or replace) a media set in the registry."""
    registry = load_registry(path)
    if any(s.media_set_id == media_set_id for s in registry.sets):
        if not replace:
            raise ValueError(
                f"media_set_id {media_set_id!r} already registered — pass replace=True to overwrite"
            )
        registry.sets = [s for s in registry.sets if s.media_set_id != media_set_id]

    item = MediaSet(
        media_set_id=media_set_id,
        project=project,
        footage_dir=footage_dir or f"footage/{project}",
        work_dir=work_dir or f"work/{project}",
        bgm_track=bgm_track,
        label=label or project,
        registered_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    registry.sets.append(item)
    save_registry(registry, path)
    return item


def next_media_set_for_round(
    registry: MediaSetRegistry, previous_media_set_ids: list[str]
) -> MediaSet:
    """Pick the registered set used least recently, so rounds rotate.

    Rotation is what keeps the collected preference about editing rather than
    about one particular bundle of footage.
    """
    if not registry.sets:
        raise ValueError("no media sets registered")
    usage: dict[str, int] = {s.media_set_id: 0 for s in registry.sets}
    last_seen: dict[str, int] = {s.media_set_id: -1 for s in registry.sets}
    for index, media_set_id in enumerate(previous_media_set_ids):
        if media_set_id in usage:
            usage[media_set_id] += 1
            last_seen[media_set_id] = index
    return min(
        registry.sets,
        key=lambda s: (usage[s.media_set_id], last_seen[s.media_set_id]),
    )
