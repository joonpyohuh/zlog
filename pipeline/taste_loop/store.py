"""Persistence for comparison rounds and clip feedback.

Two interchangeable backends behind one protocol:

- LocalTasteStore  — JSON files under taste/taste_loop/. Always available, and
  what the loop runs on by default.
- SupabaseTasteStore — the same rows over Supabase's REST API, used when
  SUPABASE_URL and a service-role key are configured.

The table shapes are identical (see supabase/migrations/20260806_taste_loop.sql),
so rows collected locally can be replayed into Supabase later without a
reshape. The local store is the source of truth for the acceptance run;
nothing here silently falls back between backends — the caller picks one and
gets an error if it cannot be reached.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from pipeline.taste_loop.axes import TasteAxis
from pipeline.taste_loop.variants import EditTimeline

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LOCAL_STORE_DIR = REPO_ROOT / "taste" / "taste_loop"

Verdict = Literal["good", "bad"]


class RenderedVariant(BaseModel):
    """A variant plus the outcome of trying to render it.

    A failed render is stored, not dropped: a round where one variant is
    missing is still a valid round, but only if the reader can see that a
    variant is missing and why.
    """

    timeline_id: str
    variant_index: int
    axis: TasteAxis
    axis_value: float
    video_path: str | None = None
    ok: bool = True
    error: str | None = None
    attempts: int = 1
    render_seconds: float = 0.0


class ComparisonRound(BaseModel):
    """One four-way comparison and its outcome.

    `winner_id` is None both before a pick is made and after "none of these" —
    `resolved` distinguishes the two.
    """

    id: str
    media_set_id: str
    axis: TasteAxis
    candidates: list[EditTimeline]
    renders: list[RenderedVariant] = Field(default_factory=list)
    winner_id: str | None = None
    resolved: bool = False
    rejected_all: bool = False
    style_profile_version: int | None = None
    created_at: str
    resolved_at: str | None = None

    def winner(self) -> EditTimeline | None:
        if not self.winner_id:
            return None
        return next((c for c in self.candidates if c.timeline_id == self.winner_id), None)

    def winning_value(self) -> float | None:
        won = self.winner()
        return won.axis_value if won else None

    def timeline(self, timeline_id: str) -> EditTimeline | None:
        return next((c for c in self.candidates if c.timeline_id == timeline_id), None)

    def render_for(self, timeline_id: str) -> RenderedVariant | None:
        return next((r for r in self.renders if r.timeline_id == timeline_id), None)


class ClipFeedback(BaseModel):
    """One spacebar mark on a chosen variant.

    Everything below `verdict`/`comment` is filled by code from the timeline
    JSON — the person supplies a moment and a judgement, never a number.
    """

    id: str
    timeline_id: str
    timestamp_sec: float
    clip_id: str | None
    shot_type: str | None
    active_presets: list[str]
    active_params: dict[str, Any]
    caption_active: bool
    verdict: Verdict
    comment: str | None = None
    created_at: str


class TasteStore(Protocol):
    def save_round(self, round_: ComparisonRound) -> ComparisonRound: ...
    def get_round(self, round_id: str) -> ComparisonRound | None: ...
    def list_rounds(self, *, limit: int = 200) -> list[ComparisonRound]: ...
    def record_pick(
        self, round_id: str, winner_id: str | None, *, rejected_all: bool = False
    ) -> ComparisonRound: ...
    def save_feedback(self, feedback: ClipFeedback) -> ClipFeedback: ...
    def list_feedback(self, *, timeline_id: str | None = None, limit: int = 500) -> list[ClipFeedback]: ...


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class LocalTasteStore:
    """File-backed store: one JSON per round, one JSONL for feedback."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or LOCAL_STORE_DIR
        self.rounds_dir = self.root / "rounds"
        self.feedback_path = self.root / "clip_feedback.jsonl"

    # --- rounds ---

    def _round_path(self, round_id: str) -> Path:
        return self.rounds_dir / f"{round_id}.json"

    def save_round(self, round_: ComparisonRound) -> ComparisonRound:
        self.rounds_dir.mkdir(parents=True, exist_ok=True)
        self._round_path(round_.id).write_text(
            round_.model_dump_json(indent=2), encoding="utf-8"
        )
        return round_

    def get_round(self, round_id: str) -> ComparisonRound | None:
        path = self._round_path(round_id)
        if not path.exists():
            return None
        return ComparisonRound.model_validate_json(path.read_text(encoding="utf-8"))

    def list_rounds(self, *, limit: int = 200) -> list[ComparisonRound]:
        if not self.rounds_dir.exists():
            return []
        rounds: list[ComparisonRound] = []
        for path in self.rounds_dir.glob("*.json"):
            try:
                rounds.append(ComparisonRound.model_validate_json(path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"corrupt round file {path}: {exc}") from exc
        rounds.sort(key=lambda r: r.created_at)
        return rounds[-limit:]

    def record_pick(
        self, round_id: str, winner_id: str | None, *, rejected_all: bool = False
    ) -> ComparisonRound:
        round_ = self.get_round(round_id)
        if round_ is None:
            raise KeyError(f"no round {round_id!r}")
        if winner_id and round_.timeline(winner_id) is None:
            raise ValueError(f"{winner_id!r} is not a candidate of round {round_id!r}")
        if winner_id and rejected_all:
            raise ValueError("a round cannot both have a winner and reject all four")
        round_.winner_id = winner_id
        round_.rejected_all = rejected_all
        round_.resolved = True
        round_.resolved_at = _now()
        return self.save_round(round_)

    # --- feedback ---

    def save_feedback(self, feedback: ClipFeedback) -> ClipFeedback:
        self.feedback_path.parent.mkdir(parents=True, exist_ok=True)
        with self.feedback_path.open("a", encoding="utf-8") as handle:
            handle.write(feedback.model_dump_json() + "\n")
        return feedback

    def list_feedback(
        self, *, timeline_id: str | None = None, limit: int = 500
    ) -> list[ClipFeedback]:
        if not self.feedback_path.exists():
            return []
        rows: list[ClipFeedback] = []
        for line in self.feedback_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = ClipFeedback.model_validate_json(line)
            if timeline_id and row.timeline_id != timeline_id:
                continue
            rows.append(row)
        return rows[-limit:]


class SupabaseTasteStore:
    """The same rows over Supabase's PostgREST endpoint.

    Requires a service-role key: these tables are founder-private and their
    RLS policies deny anon access outright.
    """

    def __init__(self, url: str | None = None, service_key: str | None = None) -> None:
        self.url = (url or os.getenv("NEXT_PUBLIC_SUPABASE_URL") or os.getenv("SUPABASE_URL") or "").rstrip("/")
        self.service_key = service_key or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or ""
        if not self.url or not self.service_key:
            raise RuntimeError(
                "SupabaseTasteStore needs SUPABASE_URL (or NEXT_PUBLIC_SUPABASE_URL) and "
                "SUPABASE_SERVICE_ROLE_KEY; use LocalTasteStore when those are unset"
            )

    def _request(
        self, method: str, path: str, *, body: Any = None, params: str = ""
    ) -> list[dict[str, Any]]:
        url = f"{self.url}/rest/v1/{path}{params}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("apikey", self.service_key)
        req.add_header("Authorization", f"Bearer {self.service_key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Prefer", "return=representation,resolution=merge-duplicates")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"supabase {method} {path} failed ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"supabase {method} {path} unreachable: {exc.reason}") from exc
        return json.loads(raw) if raw.strip() else []

    @staticmethod
    def _round_row(round_: ComparisonRound) -> dict[str, Any]:
        return {
            "id": round_.id,
            "media_set_id": round_.media_set_id,
            "axis": round_.axis.value,
            "candidates": json.loads(
                json.dumps([c.model_dump(mode="json") for c in round_.candidates])
            ),
            "renders": json.loads(
                json.dumps([r.model_dump(mode="json") for r in round_.renders])
            ),
            "winner_id": round_.winner_id,
            "resolved": round_.resolved,
            "rejected_all": round_.rejected_all,
            "style_profile_version": round_.style_profile_version,
            "created_at": round_.created_at,
            "resolved_at": round_.resolved_at,
        }

    @staticmethod
    def _row_to_round(row: dict[str, Any]) -> ComparisonRound:
        return ComparisonRound.model_validate(
            {
                **row,
                "candidates": row.get("candidates") or [],
                "renders": row.get("renders") or [],
            }
        )

    def save_round(self, round_: ComparisonRound) -> ComparisonRound:
        self._request("POST", "comparison_rounds", body=self._round_row(round_))
        return round_

    def get_round(self, round_id: str) -> ComparisonRound | None:
        rows = self._request("GET", "comparison_rounds", params=f"?id=eq.{round_id}&limit=1")
        return self._row_to_round(rows[0]) if rows else None

    def list_rounds(self, *, limit: int = 200) -> list[ComparisonRound]:
        rows = self._request(
            "GET", "comparison_rounds", params=f"?order=created_at.asc&limit={int(limit)}"
        )
        return [self._row_to_round(r) for r in rows]

    def record_pick(
        self, round_id: str, winner_id: str | None, *, rejected_all: bool = False
    ) -> ComparisonRound:
        existing = self.get_round(round_id)
        if existing is None:
            raise KeyError(f"no round {round_id!r}")
        if winner_id and existing.timeline(winner_id) is None:
            raise ValueError(f"{winner_id!r} is not a candidate of round {round_id!r}")
        if winner_id and rejected_all:
            raise ValueError("a round cannot both have a winner and reject all four")
        self._request(
            "PATCH",
            "comparison_rounds",
            params=f"?id=eq.{round_id}",
            body={
                "winner_id": winner_id,
                "rejected_all": rejected_all,
                "resolved": True,
                "resolved_at": _now(),
            },
        )
        existing.winner_id = winner_id
        existing.rejected_all = rejected_all
        existing.resolved = True
        existing.resolved_at = _now()
        return existing

    def save_feedback(self, feedback: ClipFeedback) -> ClipFeedback:
        self._request("POST", "clip_feedback", body=feedback.model_dump(mode="json"))
        return feedback

    def list_feedback(
        self, *, timeline_id: str | None = None, limit: int = 500
    ) -> list[ClipFeedback]:
        query = f"?order=created_at.asc&limit={int(limit)}"
        if timeline_id:
            query += f"&timeline_id=eq.{timeline_id}"
        rows = self._request("GET", "clip_feedback", params=query)
        return [ClipFeedback.model_validate(r) for r in rows]


def get_store(backend: str | None = None) -> TasteStore:
    """Return the configured store.

    ZLOG_TASTE_STORE=supabase opts into Supabase; anything else (and the
    default) uses the local files. Supabase misconfiguration raises rather
    than degrading to local, so a run never half-lands in two places.
    """
    choice = (backend or os.getenv("ZLOG_TASTE_STORE") or "local").strip().lower()
    if choice == "supabase":
        return SupabaseTasteStore()
    if choice == "local":
        return LocalTasteStore()
    raise ValueError(f"unknown taste store backend {choice!r} (expected 'local' or 'supabase')")


def new_round_id() -> str:
    return uuid.uuid4().hex
