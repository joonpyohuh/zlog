"""Creative Direction Bible helpers — Soft Flow roles, effect budget, prompt snippet.

Full text: taste/CREATIVE_DIRECTION_BIBLE.md
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BIBLE_PATH = REPO_ROOT / "taste" / "CREATIVE_DIRECTION_BIBLE.md"

SOFT_FLOW_ROLES: tuple[str, ...] = (
    "hook",
    "orientation",
    "development",
    "zlog_moment",
    "release",
    "resonance",
)

# Compact director/evaluator reminder — not the full bible (token budget).
SOFT_FLOW_PROMPT_SNIPPET = """## Soft Flow (Creative Direction Bible v0.1)

Zlog builds a natural micro-narrative, not a beat-synced highlight reel.

Roles (adapt to footage; do not force a visible template):
1. hook — reason to keep watching (not merely the prettiest frame)
2. orientation — where / what / who, shown not narrated
3. development — small change each cut (place, action, distance, mood, energy)
4. zlog_moment — the most memorable beat; protect it with context before and breath after
5. release — ease pace/energy after the moment
6. resonance — ending that connects to the opening; leave aftertaste, do not over-explain

Effects: only with a clear purpose; budget strong effects (≤2 per 15s, ≤4 per 30s).
Captions: add context or aftertaste — never describe what the frame already shows.
"none" for caption/effect on zlog_moment is often correct.
Audio: duck BGM for speech and key natural sounds; preserve imperfect handheld when it has energy.
"""


def load_creative_bible() -> str:
    if not BIBLE_PATH.is_file():
        return SOFT_FLOW_PROMPT_SNIPPET
    return BIBLE_PATH.read_text(encoding="utf-8")


def soft_flow_roles() -> list[str]:
    return list(SOFT_FLOW_ROLES)


def effect_budget(duration_sec: float) -> int:
    """Max strong effects for a film of the given length (bible §3)."""
    d = max(0.0, float(duration_sec))
    if d <= 15.0:
        return 2
    if d <= 30.0:
        return 4
    # ~1 strong effect per additional ~10s beyond 30s, capped
    return min(8, 4 + int((d - 30.0) // 10))


def director_bible_block() -> str:
    """Short block safe to append to director system prompts."""
    return SOFT_FLOW_PROMPT_SNIPPET.strip()
