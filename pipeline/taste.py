"""Self-pilot taste injection — folds the founder's explicit editing rules
and past keep/drop judgments into select_ai.py's system prompt.

Reads:  taste/taste_profile.json  (explicit rules, optional — see schema below)
        taste/examples.jsonl      (past judgments, optional; each line one
                                    Example — appended to by `python run.py review`)
        taste/examples/*.jpg      (frame thumbnails examples.jsonl points at)
Writes: nothing of its own — this module only assembles prompt content for
        select_ai.py to send. It never invents a segment_id or a timecode;
        every example here was a real human decision recorded by `review`.

taste_profile.json schema:
    {
      "editing_rules": ["음식 클로즈업은 1편에 최대 1컷", "..."],
      "avoid": ["초점 나간 인물", "..."],
      "pacing": "빠른 편",
      "notes": "..."
    }

Anthropic's `system` parameter only accepts text content — no images — so
the taste block built here is text-only (profile rules + example
segment_id/decision/reason) and carries `cache_control: ephemeral` since
it barely changes between calls. The example *images* select_ai.py wants
alongside it have to travel in the user turn instead; build_taste_block()
also returns the sampled Example records so select_ai.py can attach their
thumbnails there.
"""

from __future__ import annotations

import random
from pathlib import Path

from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parent.parent
TASTE_DIR = REPO_ROOT / "taste"
TASTE_PROFILE_PATH = TASTE_DIR / "taste_profile.json"
EXAMPLES_PATH = TASTE_DIR / "examples.jsonl"
EXAMPLES_IMAGE_DIR = TASTE_DIR / "examples"

EXAMPLES_PER_DECISION = 10


class TasteProfile(BaseModel):
    editing_rules: list[str] = []
    avoid: list[str] = []
    pacing: str = ""
    notes: str = ""


class Example(BaseModel):
    segment_id: str
    frame_path: str  # repo-root-relative, e.g. "taste/examples/trip_01__raw_01_s007.jpg"
    decision: str  # "keep" | "drop"
    reason: str


def load_taste_profile() -> TasteProfile:
    if not TASTE_PROFILE_PATH.exists():
        return TasteProfile()
    return TasteProfile.model_validate_json(TASTE_PROFILE_PATH.read_text(encoding="utf-8"))


def load_examples() -> list[Example]:
    if not EXAMPLES_PATH.exists():
        return []
    examples = []
    for line in EXAMPLES_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            examples.append(Example.model_validate_json(line))
    return examples


def _sample(examples: list[Example], decision: str, n: int) -> list[Example]:
    pool = [e for e in examples if e.decision == decision]
    return pool if len(pool) <= n else random.sample(pool, n)


def _profile_text(profile: TasteProfile) -> str:
    lines = ["## Editor taste (taste/taste_profile.json)"]
    if profile.editing_rules:
        lines.append("Rules:")
        lines += [f"- {r}" for r in profile.editing_rules]
    if profile.avoid:
        lines.append("Avoid:")
        lines += [f"- {a}" for a in profile.avoid]
    if profile.pacing:
        lines.append(f"Pacing: {profile.pacing}")
    if profile.notes:
        lines.append(f"Notes: {profile.notes}")
    return "\n".join(lines)


def _examples_text(keep: list[Example], drop: list[Example]) -> str:
    lines = [
        "## Past judgments (sampled from taste/examples.jsonl)",
        "Frame images are attached above with [KEEP]/[DROP] labels.",
    ]
    for label, group in (("KEEP", keep), ("DROP", drop)):
        if not group:
            continue
        lines.append(f"{label}:")
        lines += [f"- {e.segment_id}: {e.reason}" for e in group]
    return "\n".join(lines)


def record_example(
    project: str,
    segment_id: str,
    frame_abs: Path,
    decision: str,
    reason: str,
) -> Path:
    """Copy frame into taste/examples and append one jsonl line. Returns image path."""
    import shutil

    EXAMPLES_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    TASTE_DIR.mkdir(parents=True, exist_ok=True)
    dest_name = f"{project}__{segment_id.replace('#', '_')}.jpg"
    dest_path = EXAMPLES_IMAGE_DIR / dest_name
    shutil.copyfile(frame_abs, dest_path)

    example = Example(
        segment_id=segment_id,
        frame_path=str(dest_path.relative_to(REPO_ROOT)).replace("\\", "/"),
        decision=decision,
        reason=reason,
    )
    with EXAMPLES_PATH.open("a", encoding="utf-8") as f:
        f.write(example.model_dump_json() + "\n")
    return dest_path


def build_taste_block(
    keep_n: int = EXAMPLES_PER_DECISION,
    drop_n: int = EXAMPLES_PER_DECISION,
) -> tuple[dict | None, list[Example]]:
    """Assemble the cached system-prompt text block plus the Example
    records whose thumbnails should be attached as images in the user
    turn. Returns (None, []) if there's no profile and no examples yet —
    injecting an empty block would just waste tokens.
    """
    profile = load_taste_profile()
    examples = load_examples()
    keep = _sample(examples, "keep", keep_n)
    drop = _sample(examples, "drop", drop_n)

    has_profile = bool(profile.editing_rules or profile.avoid or profile.pacing or profile.notes)
    if not has_profile and not keep and not drop:
        return None, []

    text = _profile_text(profile)
    if keep or drop:
        text += "\n\n" + _examples_text(keep, drop)

    block = {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}
    return block, keep + drop
