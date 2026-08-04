"""Claude Sonnet Director — StoryPlan from AssetAnalysis (PROMPT 4).

Reads:  work/<project>/asset_analyses.json
        work/<project>/deterministic_features.json (optional audio summary)
        work/<project>/evidence/** (frames for strong/ambiguous contact sheets)
        work/<project>/note.txt (user edit brief — NEVER a caption source)
Writes: work/<project>/story_plan.json
        work/<project>/story_plan_usage.json
        work/<project>/director_sheets/*.jpg (debug contact sheets sent to the model)

Default director model: Claude Sonnet 4.5 (ZLOG_DIRECTOR_MODEL).
Default style: clean_vlog. Does not fall back to the Y2K template.
Never invents timestamps / beat positions / unknown segment_ids.
"""

from __future__ import annotations

import json
import math
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import anthropic
import click
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

from pipeline.ai.config import ModelConfig, load_model_config
from pipeline.ai.json_schema import SUBMIT_STORY_PLAN_TOOL
from pipeline.ai.media import encode_image_block
from pipeline.ai.schemas import (
    AssetAnalysis,
    CaptionMode,
    Mood,
    StoryPlan,
    StylePreset,
    TargetPlatform,
    story_plan_against_candidates,
)
from pipeline.ai.usage import CallUsage, UsageTimer, record_usage
from pipeline.evidence import DeterministicFeaturesFile

MAX_TOKENS = 4096
INTENT_SIMILARITY_MAX = 0.72
DURATION_SLACK = 1.45
STRONG_N = 6
AMBIGUOUS_N = 4
CELL = 220
COLS = 3
LABEL_H = 48
MARGIN = 10

AVAILABLE_STYLE_PRESETS = [p.value for p in StylePreset]

Y2K_HINT = re.compile(
    r"\b(y2k|ccd|retro|캠코더|캠코더감성|레트로|2000s|letterbox|4:3|4x3)\b",
    re.IGNORECASE,
)

DIRECTOR_SYSTEM = """You are zlog's film director. You receive EDITING BRIEF text and
visual AssetAnalysis records. The brief is an instruction to you — it is NOT
on-screen dialogue and must NEVER be copied into captions, overlays, concept
strings that read like titles, or narrative_arc caption lines.

Your job:
1. Decide what the footage is actually about from visible evidence
2. Choose one central micro-narrative
3. Pick hook / development / highlight / ending segment_ids
4. You may cold-open with a later highlight, then usually return to chronology
5. Drop weak material
6. Set target_duration_sec to fit the amount of usable material (few stills → short)
7. Default allow_asset_reuse=false — do not pick duplicates for padding
8. Default style_preset=clean_vlog unless the brief explicitly asks for Y2K/CCD/retro
9. Use sparse captions: separate fact captions from mood captions in narrative_arc
   (prefix lines with "fact:" or "mood:"). clean_vlog still needs a few captions.
10. Never invent unverifiable inner feelings, relationships, place names, or dialogue

Forbidden:
- Copying the user brief into captions
- Inventing events / relationships / places / dialogue not grounded in analyses
- Selecting the same segment_id twice when allow_asset_reuse=false
- Selecting every near-duplicate in a redundancy_group
- Generating source timestamps or beat grid times
- Choosing segment_ids not in the allowed list
- Defaulting to y2k_4x3_letterbox without an explicit Y2K/CCD/retro request

You MUST call submit_story_plan with a full StoryPlan object.
Set provider="anthropic" and model to the model id you are running as.
Put the editing brief into user_intent unchanged (for provenance only).
"""


def suggest_target_duration_sec(n_usable: int) -> float:
    """Fit duration to material count — few photos → short film."""
    n = max(0, int(n_usable))
    if n <= 0:
        return 5.0
    if n == 1:
        return 5.0
    if n == 2:
        return 7.0
    if n == 3:
        return 9.0
    if n == 4:
        return 11.0
    if n <= 6:
        return 14.0
    if n <= 8:
        return 18.0
    return float(min(30.0, 10.0 + n * 1.25))


def max_allowed_duration_sec(n_selected: int) -> float:
    return round(suggest_target_duration_sec(n_selected) * DURATION_SLACK, 2)


def style_from_brief(user_intent: str) -> StylePreset:
    if Y2K_HINT.search(user_intent or ""):
        return StylePreset.y2k_4x3_letterbox
    return StylePreset.clean_vlog


def _normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def text_similarity(a: str, b: str) -> float:
    na, nb = _normalize_text(a), _normalize_text(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        # Near-copy / containment — treat as high leakage risk.
        shorter = na if len(na) <= len(nb) else nb
        if len(shorter) >= 8:
            return max(0.85, SequenceMatcher(None, na, nb).ratio())
    return SequenceMatcher(None, na, nb).ratio()


def intent_leaks_into_captions(user_intent: str, caption_candidates: list[str]) -> list[str]:
    """Return caption strings that are too similar to the raw edit brief."""
    leaks: list[str] = []
    for cap in caption_candidates:
        # Strip fact:/mood: prefixes for comparison.
        body = re.sub(r"^(fact|mood|caption)\s*:\s*", "", cap, flags=re.IGNORECASE).strip()
        if not body:
            continue
        if text_similarity(user_intent, body) >= INTENT_SIMILARITY_MAX:
            leaks.append(cap)
    return leaks


def _caption_candidates_from_plan(plan: StoryPlan) -> list[str]:
    out: list[str] = []
    for line in plan.narrative_arc:
        low = line.lower().strip()
        if low.startswith(("fact:", "mood:", "caption:")) or "caption" in low:
            out.append(line)
    # Also guard concept if it looks like a pasted brief.
    if plan.concept:
        out.append(plan.concept)
    return out


def _composite_score(a: AssetAnalysis) -> float:
    return (
        0.25 * a.hook_potential
        + 0.25 * a.narrative_value
        + 0.20 * a.aesthetic_value
        + 0.15 * a.technical_quality
        + 0.10 * a.emotional_value
        + 0.05 * a.novelty
        - 0.15 * a.uncertainty
    )


def sort_analyses_chronologically(analyses: list[AssetAnalysis]) -> list[AssetAnalysis]:
    """Time order: trusted capture_time, else upload_index (never alpha filename)."""
    return sorted(
        analyses,
        key=lambda a: (
            a.capture_time is None,
            a.capture_time or "",
            a.upload_index,
            a.segment_id,
        ),
    )


def audio_summary_from_features(project_dir: Path) -> dict[str, Any]:
    path = project_dir / "deterministic_features.json"
    if not path.exists():
        return {"available": False, "segments": []}
    feats = DeterministicFeaturesFile.model_validate_json(path.read_text(encoding="utf-8"))
    rows = []
    rms_vals = []
    onset_vals = []
    for s in feats.segments:
        rows.append(
            {
                "segment_id": s.segment_id,
                "audio_rms": s.audio_rms,
                "onset_density": s.onset_density,
                "duration_sec": s.duration_sec,
                "is_static": s.is_static,
                "is_high_motion": s.is_high_motion,
            }
        )
        rms_vals.append(s.audio_rms)
        onset_vals.append(s.onset_density)
    return {
        "available": True,
        "mean_audio_rms": round(sum(rms_vals) / len(rms_vals), 6) if rms_vals else 0.0,
        "mean_onset_density": round(sum(onset_vals) / len(onset_vals), 4) if onset_vals else 0.0,
        "segments": rows,
    }


def pick_strong_and_ambiguous(
    analyses: list[AssetAnalysis],
) -> tuple[list[AssetAnalysis], list[AssetAnalysis]]:
    ranked = sorted(analyses, key=_composite_score, reverse=True)
    strong = ranked[:STRONG_N]
    ambiguous = [
        a
        for a in ranked
        if a.uncertainty >= 0.45 or (0.35 <= _composite_score(a) <= 0.55)
    ][:AMBIGUOUS_N]
    # Avoid duplicating the same cards in both lists when possible.
    strong_ids = {a.segment_id for a in strong}
    ambiguous = [a for a in ambiguous if a.segment_id not in strong_ids][:AMBIGUOUS_N]
    return strong, ambiguous


def _evidence_thumb(project_dir: Path, analysis: AssetAnalysis) -> Path | None:
    for eid in analysis.evidence_frame_ids:
        # evidence/<id>.jpg convention from pipeline.evidence
        cand = project_dir / "evidence" / f"{eid}.jpg"
        if cand.exists():
            return cand
        # eid may already be a relative path
        alt = project_dir / eid
        if alt.exists():
            return alt
    # fallback mid-frame from split
    stem = analysis.segment_id
    legacy = project_dir / "frames" / f"{stem}.jpg"
    return legacy if legacy.exists() else None


def build_director_contact_sheet(
    project_dir: Path,
    analyses: list[AssetAnalysis],
    *,
    name: str,
) -> Path | None:
    if not analyses:
        return None
    sheets_dir = project_dir / "director_sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        font = ImageFont.load_default()

    rows = math.ceil(len(analyses) / COLS)
    cell_w = CELL + MARGIN
    cell_h = CELL + LABEL_H + MARGIN
    w = COLS * cell_w + MARGIN
    h = rows * cell_h + MARGIN
    canvas = Image.new("RGB", (w, h), (16, 16, 16))
    draw = ImageDraw.Draw(canvas)
    for i, a in enumerate(analyses):
        col, row = i % COLS, i // COLS
        x = MARGIN + col * cell_w
        y = MARGIN + row * cell_h
        thumb_path = _evidence_thumb(project_dir, a)
        if thumb_path is not None:
            thumb = Image.open(thumb_path).convert("RGB")
            thumb.thumbnail((CELL, CELL), Image.LANCZOS)
            px = x + (CELL - thumb.width) // 2
            py = y + (CELL - thumb.height) // 2
            canvas.paste(thumb, (px, py))
        draw.rectangle((x, y + CELL, x + CELL, y + CELL + LABEL_H), fill=(28, 28, 28))
        draw.text((x + 6, y + CELL + 6), a.segment_id, fill=(230, 230, 230), font=font)
        draw.text(
            (x + 6, y + CELL + 26),
            f"score={_composite_score(a):.2f} u={a.uncertainty:.2f}",
            fill=(160, 160, 160),
            font=font,
        )
    out = sheets_dir / name
    canvas.save(out, quality=90)
    return out


def validate_story_plan(
    plan: StoryPlan,
    *,
    allowed_segment_ids: set[str],
    analyses: list[AssetAnalysis],
) -> list[str]:
    """Post-model checks. Empty list means accept."""
    errors: list[str] = []
    try:
        story_plan_against_candidates(plan, allowed_segment_ids)
    except ValueError as exc:
        errors.append(str(exc))

    selected = plan.selected_segment_ids
    if plan.hook_segment_id not in selected:
        errors.append("hook_segment_id not in selected_segment_ids")
    if plan.ending_segment_id not in selected:
        errors.append("ending_segment_id not in selected_segment_ids")

    if not plan.allow_asset_reuse and len(selected) != len(set(selected)):
        errors.append("duplicate segment_id while allow_asset_reuse=false")

    # Duration vs material
    n = len(set(selected))
    cap = max_allowed_duration_sec(n)
    if plan.target_duration_sec > cap:
        errors.append(
            f"target_duration_sec {plan.target_duration_sec} too long for {n} clips (max {cap})"
        )

    # Intent → caption leakage
    leaks = intent_leaks_into_captions(plan.user_intent, _caption_candidates_from_plan(plan))
    if leaks:
        errors.append(f"user brief leaked into caption/concept text: {leaks[:3]!r}")

    # clean_vlog still needs captions (sparse, not none)
    if plan.style_preset == StylePreset.clean_vlog and plan.caption_mode == CaptionMode.none:
        errors.append("clean_vlog requires captions (caption_mode must not be none)")

    # Prefer not selecting entire redundancy groups when reuse is false
    if not plan.allow_asset_reuse:
        by_group: dict[str, list[str]] = {}
        analysis_by_id = {a.segment_id: a for a in analyses}
        for sid in selected:
            a = analysis_by_id.get(sid)
            if a and a.redundancy_group:
                by_group.setdefault(a.redundancy_group, []).append(sid)
        for group, ids in by_group.items():
            group_size = sum(
                1 for a in analyses if a.redundancy_group == group
            )
            if group_size >= 3 and len(ids) >= group_size:
                errors.append(
                    f"selected all members of redundancy_group={group!r} ({len(ids)})"
                )

    return errors


def deterministic_story_plan(
    user_intent: str,
    analyses: list[AssetAnalysis],
    *,
    model: str = "deterministic",
) -> StoryPlan:
    """Clean non-Y2K fallback when the API fails — grounded only in analyses."""
    ordered = sort_analyses_chronologically(analyses)
    # Drop weak
    usable = [
        a
        for a in ordered
        if a.technical_quality >= 0.25 and (a.narrative_value + a.aesthetic_value) >= 0.5
    ] or list(ordered)

    # Deduplicate redundancy groups — keep best composite in each group
    chosen: list[AssetAnalysis] = []
    seen_groups: set[str] = set()
    for a in sorted(usable, key=_composite_score, reverse=True):
        if a.redundancy_group:
            if a.redundancy_group in seen_groups:
                continue
            seen_groups.add(a.redundancy_group)
        chosen.append(a)
    # Restore chronology for selected set
    chosen_ids = {a.segment_id for a in chosen}
    chronological = [a for a in ordered if a.segment_id in chosen_ids]
    if not chronological:
        chronological = ordered[:1]

    # Limit count for short material
    if len(chronological) > 6:
        # keep top scores but preserve chrono order
        keep = {
            a.segment_id
            for a in sorted(chronological, key=_composite_score, reverse=True)[:6]
        }
        chronological = [a for a in chronological if a.segment_id in keep]

    selected_ids = [a.segment_id for a in chronological]
    hook = max(chronological, key=lambda a: a.hook_potential)
    ending = chronological[-1]
    # Cold open: if best hook is late, still allowed as hook while list stays chrono for body
    duration = suggest_target_duration_sec(len(selected_ids))
    style = style_from_brief(user_intent)

    subjects: list[str] = []
    for a in chronological:
        for s in a.subjects:
            if s not in subjects:
                subjects.append(s)
    concept = " / ".join(subjects[:3]) if subjects else "everyday moments"
    facts = []
    for a in chronological[:3]:
        facts.extend(a.visually_grounded_facts[:1])
    narrative_arc = ["hook", "develop", "close"]
    for f in facts[:2]:
        narrative_arc.append(f"fact: {f}")
    if chronological:
        narrative_arc.append(f"mood: {chronological[0].mood.value}")

    return StoryPlan(
        user_intent=user_intent,
        concept=concept,
        tone=chronological[0].mood if chronological else Mood.calm,
        target_platform=TargetPlatform.youtube,
        target_duration_sec=duration,
        style_preset=style,
        hook_segment_id=hook.segment_id,
        ending_segment_id=ending.segment_id,
        selected_segment_ids=selected_ids,
        narrative_arc=narrative_arc,
        caption_mode=CaptionMode.sparse,
        allow_asset_reuse=False,
        music_requirements="light underscore, no lyrics needed",
        reasoning_summary="deterministic fallback from AssetAnalysis scores; no API",
        confidence=0.35,
        provider="anthropic",
        model=model,
    )


def load_analyses(project_dir: Path) -> list[AssetAnalysis]:
    path = project_dir / "asset_analyses.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run pipeline.analyze_assets first")
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("analyses") or data
    if not isinstance(items, list):
        raise TypeError("asset_analyses.json missing analyses array")
    return [AssetAnalysis.model_validate(x) for x in items]


def load_user_intent(project_dir: Path, explicit: str | None = None) -> str:
    if explicit is not None and explicit.strip():
        return explicit.strip()
    note = project_dir / "note.txt"
    if note.exists():
        return note.read_text(encoding="utf-8").strip()
    return ""


def _build_user_content(
    *,
    user_intent: str,
    analyses: list[AssetAnalysis],
    audio_summary: dict[str, Any],
    strong_sheet: Path | None,
    ambiguous_sheet: Path | None,
    model_id: str,
) -> list[dict[str, Any]]:
    ordered = sort_analyses_chronologically(analyses)
    catalog = [a.model_dump(mode="json") for a in ordered]
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "=== EDITING BRIEF (instructions for you — NOT on-screen text) ===\n"
                f"{user_intent or '(empty brief)'}\n"
                "=== END EDITING BRIEF ===\n\n"
                "Do not copy the editing brief into captions, titles, or overlays.\n"
                f"Available style presets: {AVAILABLE_STYLE_PRESETS}\n"
                "Default style_preset=clean_vlog unless brief explicitly requests Y2K/CCD/retro.\n"
                f"Suggested duration for ~{len(ordered)} analyses: "
                f"{suggest_target_duration_sec(len(ordered))}s "
                f"(hard-ish upper after selection enforced in code).\n"
                f"allow_asset_reuse default false. caption_mode=sparse "
                f"(clean_vlog still needs fact/mood captions).\n"
                f"Set provider=anthropic and model={model_id}.\n"
            ),
        },
        {
            "type": "text",
            "text": (
                "Deterministic audio / motion summary:\n"
                + json.dumps(audio_summary, ensure_ascii=False)
            ),
        },
        {
            "type": "text",
            "text": (
                "AssetAnalysis list in chronological order "
                "(upload_index / capture_time — never invent timestamps):\n"
                + json.dumps(catalog, ensure_ascii=False)
            ),
        },
    ]
    if strong_sheet and strong_sheet.exists():
        content.append({"type": "text", "text": "Contact sheet — STRONGEST candidates:"})
        content.append(encode_image_block(strong_sheet))
    if ambiguous_sheet and ambiguous_sheet.exists():
        content.append({"type": "text", "text": "Contact sheet — AMBIGUOUS candidates:"})
        content.append(encode_image_block(ambiguous_sheet))
    content.append(
        {
            "type": "text",
            "text": "Call submit_story_plan now with a complete StoryPlan.",
        }
    )
    return content


def _call_director(
    client: anthropic.Anthropic,
    *,
    model: str,
    content: list[dict[str, Any]],
) -> tuple[dict[str, Any], CallUsage]:
    timer = UsageTimer()
    usage = CallUsage(
        provider="anthropic",
        model=model,
        operation="create_story_plan",
    )
    try:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=DIRECTOR_SYSTEM,
            tools=[SUBMIT_STORY_PLAN_TOOL],
            tool_choice={"type": "tool", "name": "submit_story_plan"},
            messages=[{"role": "user", "content": content}],
        )
    except Exception as exc:
        usage.ok = False
        usage.error = str(exc)[:300]
        usage.latency_ms = timer.ms()
        record_usage(usage)
        raise

    usage.latency_ms = timer.ms()
    if response.usage:
        usage.input_tokens = int(getattr(response.usage, "input_tokens", 0) or 0)
        usage.output_tokens = int(getattr(response.usage, "output_tokens", 0) or 0)
        cache_create = int(getattr(response.usage, "cache_creation_input_tokens", 0) or 0)
        cache_read = int(getattr(response.usage, "cache_read_input_tokens", 0) or 0)
        usage.cache_tokens = cache_create + cache_read

    tool_input: dict[str, Any] | None = None
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "submit_story_plan":
            tool_input = dict(block.input)
            break
    if tool_input is None:
        usage.ok = False
        usage.error = "missing tool_use submit_story_plan"
        record_usage(usage)
        raise RuntimeError("Anthropic response missing submit_story_plan tool_use")

    record_usage(usage)
    return tool_input, usage


def create_story_plan(
    work_dir: Path,
    project: str,
    *,
    user_intent: str | None = None,
    config: ModelConfig | None = None,
    client: anthropic.Anthropic | None = None,
    force: bool = False,
) -> Path:
    """Run Claude Director (or deterministic fallback) and write story_plan.json."""
    load_dotenv()
    cfg = config or load_model_config()
    project_dir = work_dir / project
    out_path = project_dir / "story_plan.json"
    if out_path.exists() and not force:
        click.echo(f"{out_path} exists, skipping (use --force to overwrite)")
        return out_path

    analyses = load_analyses(project_dir)
    if not analyses:
        raise ValueError("no AssetAnalysis records to direct")
    brief = load_user_intent(project_dir, user_intent)
    allowed = {a.segment_id for a in analyses}
    audio = audio_summary_from_features(project_dir)
    strong, ambiguous = pick_strong_and_ambiguous(analyses)
    strong_sheet = build_director_contact_sheet(project_dir, strong, name="strong.jpg")
    ambiguous_sheet = build_director_contact_sheet(
        project_dir, ambiguous, name="ambiguous.jpg"
    )

    model_id = cfg.director_model
    usages: list[dict[str, Any]] = []
    plan: StoryPlan | None = None
    source = "anthropic"

    try:
        aclient = client or anthropic.Anthropic()
        content = _build_user_content(
            user_intent=brief,
            analyses=analyses,
            audio_summary=audio,
            strong_sheet=strong_sheet,
            ambiguous_sheet=ambiguous_sheet,
            model_id=model_id,
        )
        raw, usage = _call_director(aclient, model=model_id, content=content)
        usages.append(usage.as_dict())
        raw.setdefault("provider", "anthropic")
        raw.setdefault("model", model_id)
        # Provenance: keep the real brief even if the model rewrote user_intent.
        raw["user_intent"] = brief
        candidate = StoryPlan.model_validate(raw)
        errors = validate_story_plan(
            candidate, allowed_segment_ids=allowed, analyses=analyses
        )
        if errors:
            click.echo(f"director plan failed validation: {errors}; using deterministic", err=True)
            plan = deterministic_story_plan(brief, analyses, model="deterministic")
            source = "deterministic_after_validation"
        else:
            plan = candidate
            source = "anthropic"
    except Exception as exc:  # noqa: BLE001
        click.echo(f"director API failed: {exc}; using deterministic StoryPlan", err=True)
        usages.append(
            CallUsage(
                provider="anthropic",
                model=model_id,
                operation="create_story_plan",
                ok=False,
                error=str(exc)[:300],
            ).as_dict()
        )
        plan = deterministic_story_plan(brief, analyses, model="deterministic")
        source = "deterministic_api_failure"

    assert plan is not None
    # Final safety: never ship a leaking / invalid plan
    final_errors = validate_story_plan(plan, allowed_segment_ids=allowed, analyses=analyses)
    if final_errors and source.startswith("deterministic"):
        # Soft-fix duration / caption_mode on deterministic path
        plan = plan.model_copy(
            update={
                "target_duration_sec": min(
                    plan.target_duration_sec,
                    max_allowed_duration_sec(len(set(plan.selected_segment_ids))),
                ),
                "caption_mode": CaptionMode.sparse,
                "allow_asset_reuse": False,
                "style_preset": style_from_brief(brief),
            }
        )
        # Strip leaking narrative lines
        cleaned_arc = [
            line
            for line in plan.narrative_arc
            if line not in intent_leaks_into_captions(brief, [line])
        ]
        if not any(l.lower().startswith("fact:") for l in cleaned_arc):
            for a in analyses[:2]:
                if a.visually_grounded_facts:
                    cleaned_arc.append(f"fact: {a.visually_grounded_facts[0]}")
                    break
        if not any(l.lower().startswith("mood:") for l in cleaned_arc):
            cleaned_arc.append(f"mood: {plan.tone.value}")
        plan = plan.model_copy(update={"narrative_arc": cleaned_arc})
    elif final_errors:
        plan = deterministic_story_plan(brief, analyses, model="deterministic")
        source = "deterministic_final_guard"

    payload = {
        "project": project,
        "generator": "director",
        "source": source,
        "plan": plan.model_dump(mode="json"),
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    usage_path = project_dir / "story_plan_usage.json"
    total_cost = sum(float(u.get("estimated_cost_usd") or 0) for u in usages)
    usage_path.write_text(
        json.dumps(
            {
                "project": project,
                "director_model": model_id,
                "source": source,
                "calls": usages,
                "estimated_cost_usd_total": round(total_cost, 6),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    click.echo(
        f"story plan ({source}): {len(plan.selected_segment_ids)} clips, "
        f"{plan.target_duration_sec}s, style={plan.style_preset.value} → {out_path}"
    )
    return out_path


@click.command()
@click.option("--work-dir", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--project", required=True)
@click.option("--intent", default=None, help="Editing brief (else note.txt)")
@click.option("--force", is_flag=True, default=False)
def main(work_dir: Path, project: str, intent: str | None, force: bool) -> None:
    out = create_story_plan(work_dir, project, user_intent=intent, force=force)
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
