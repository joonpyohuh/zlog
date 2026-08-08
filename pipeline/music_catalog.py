"""Simple searchable music catalog with context-aware recommendations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pipeline.editorial_models import EditingIntent, TripGraph


class MusicTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    music_id: str
    title: str
    artist: str
    file: str
    mood_tags: list[str] = Field(default_factory=list)
    energy: Literal["low", "medium", "high"] = "medium"


def load_catalog(path: Path = Path("assets/music/catalog.json")) -> list[MusicTrack]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [MusicTrack.model_validate(item) for item in data.get("tracks", [])]


def search_music(query: str, tracks: list[MusicTrack]) -> list[MusicTrack]:
    terms = query.lower().split()
    if not terms:
        return tracks
    return [
        track
        for track in tracks
        if all(
            term
            in " ".join(
                [track.title, track.artist, *track.mood_tags, track.energy]
            ).lower()
            for term in terms
        )
    ]


def recommend_music(
    graph: TripGraph,
    intent: EditingIntent,
    tracks: list[MusicTrack],
    *,
    day_id: str | None = None,
    event_id: str | None = None,
) -> list[MusicTrack]:
    """Return an ordered list only; recommendation scores are intentionally private."""

    scenes = [
        scene
        for scene in graph.scenes
        if (not day_id or scene.day_id == day_id)
        and (not event_id or scene.event_id == event_id)
    ]
    signals = {
        value.lower()
        for scene in scenes
        for value in [
            scene.label,
            scene.emotional_progression,
            *scene.roles,
            *scene.subjects,
        ]
    }
    if intent.desired_mood:
        signals.add(intent.desired_mood.lower())
    if intent.pacing:
        signals.add(
            {"calm": "low", "balanced": "medium", "energetic": "high"}[intent.pacing]
        )
    ranked = sorted(
        tracks,
        key=lambda track: (
            -sum(
                any(
                    tag.lower() in signal or signal in tag.lower() for signal in signals
                )
                for tag in [*track.mood_tags, track.energy]
            ),
            track.title.lower(),
        ),
    )
    return ranked[:8]


def resolve_music_file(track: MusicTrack, repo_root: Path) -> Path:
    path = (repo_root / track.file).resolve()
    if repo_root.resolve() not in path.parents or not path.is_file():
        raise FileNotFoundError(f"Music file is unavailable: {track.music_id}")
    return path
