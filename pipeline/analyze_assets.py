"""Claude AssetAnalysis over adaptive evidence bundles (PROMPT 3).

Reads:  work/<project>/evidence_manifest.json
        work/<project>/deterministic_features.json
        work/<project>/evidence/*.jpg
        work/<project>/candidates.json (optional — limits to passing IDs)
Writes: work/<project>/asset_analyses.json
        work/<project>/asset_analysis_usage.json
        work/<project>/asset_analysis_report.html

Default runtime model: Claude Haiku 4.5 (ZLOG_ANALYZER_MODEL).
Escalates *only flagged segments* to Sonnet. Never invents timestamps /
segment_ids / evidence_frame_ids. Does not decide the final timeline.

GPT-5.6 Luna sits behind OpenAIProvider as an inactive fallback
(ZLOG_ANALYZER_FALLBACK=openai) — happy path never dual-calls Claude+GPT.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import anthropic
import click
from dotenv import load_dotenv

from pipeline.ai.config import ModelConfig, load_model_config
from pipeline.ai.json_schema import SUBMIT_ASSET_ANALYSES_TOOL
from pipeline.ai.media import encode_image_block
from pipeline.ai.schemas import AssetAnalysis, MediaType, Mood, SceneKind, ShotType
from pipeline.ai.usage import CallUsage, UsageTimer, record_usage
from pipeline.edl import CandidatesFile
from pipeline.evidence import (
    DeterministicFeaturesFile,
    EvidenceManifest,
    EvidenceSegment,
    SegmentFeatures,
)

MAX_TOKENS = 4096
BATCH_SIZE = 4
UNCERTAINTY_ESCALATE = 0.55
CROP_CONFIDENCE_LOW = 0.35
AMBIGUOUS_ACTION = frozenset(
    {
        "",
        "unknown",
        "unclear",
        "ambiguous",
        "n/a",
        "na",
        "none",
        "?",
        "uncertain",
        "not sure",
        "unclear action",
        "no clear action",
    }
)

SYSTEM_RULES = """You are zlog's asset analyzer for vlog evidence frames.

For each segment you receive a temporal bundle: start / middle / end evidence
frames (or a single representative frame for static shots). Analyze the bundle
as one unit — do NOT emit one analysis per frame.

You MUST call submit_asset_analyses with exactly one AssetAnalysis per segment.

Do:
- Compare start vs middle vs end frames
- Describe subjects actually visible
- Note place/environment visual traits (not unverifiable place names)
- Describe action progression across the bundle
- Judge the best moment within the bundle (encode in action_progression / scores)
- Separate technical_quality from aesthetic_value / emotional_value
- Score narrative_value and hook_potential
- Assign redundancy_group labels shared by visually similar segments
- Propose 9:16 crop focus (focus_x/y/width/height in 0..1) + crop_confidence
- List visually_grounded_facts usable later as captions (only what is visible)
- Record uncertainty in 0..1

Never:
- Invent source timestamps or cut times
- Invent segment_id or evidence_frame_id values not listed
- Decide the final timeline / selection order
- Treat user editing instructions as on-screen visual facts
- Infer unverifiable emotions, relationships, or place names
"""


@dataclass
class SegmentBundle:
    segment: EvidenceSegment
    features: SegmentFeatures | None
    evidence_paths: list[Path]  # parallel to evidence ids kept
    expected_evidence_ids: list[str]


@dataclass
class EscalationDecision:
    segment_id: str
    reasons: list[str] = field(default_factory=list)

    @property
    def should_escalate(self) -> bool:
        return bool(self.reasons)


def media_type_for(source_file: str) -> MediaType:
    name = source_file.lower()
    if name.startswith("still_"):
        return MediaType.still_video
    if Path(name).suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return MediaType.image
    return MediaType.video


def needs_sonnet_escalation(
    analysis: AssetAnalysis | None,
    *,
    expected_evidence_ids: list[str],
    validation_failed: bool = False,
    uncertainty_threshold: float = UNCERTAINTY_ESCALATE,
    crop_confidence_low: float = CROP_CONFIDENCE_LOW,
) -> EscalationDecision:
    """Return escalation reasons for one Haiku result (empty → keep Haiku)."""
    sid = analysis.segment_id if analysis else "?"
    reasons: list[str] = []
    if validation_failed or analysis is None:
        reasons.append("structured_output_validation_failed")
        return EscalationDecision(segment_id=sid, reasons=reasons)

    if analysis.uncertainty >= uncertainty_threshold:
        reasons.append("high_uncertainty")
    if analysis.crop_confidence < crop_confidence_low:
        reasons.append("low_crop_confidence")
    if len(analysis.subjects) >= 3 and (
        analysis.focus_width >= 0.75
        or analysis.focus_height >= 0.75
        or analysis.crop_confidence < 0.45
    ):
        reasons.append("scattered_subjects")
    action = (analysis.action_progression or "").strip().lower()
    if action in AMBIGUOUS_ACTION or len(action) < 4:
        reasons.append("ambiguous_action_progression")
    missing = [eid for eid in expected_evidence_ids if eid not in analysis.evidence_frame_ids]
    if missing:
        reasons.append("missing_evidence_ids")
    return EscalationDecision(segment_id=analysis.segment_id, reasons=reasons)


def validate_analyses(
    raw_items: list[dict[str, Any]],
    *,
    allowed_segment_ids: set[str],
    allowed_evidence_by_segment: dict[str, set[str]],
    analysis_provider: Literal["anthropic", "openai"],
    analysis_model: str,
) -> list[AssetAnalysis]:
    """Parse + enforce segment/evidence id contracts. Rejects duplicates."""
    seen: set[str] = set()
    out: list[AssetAnalysis] = []
    for item in raw_items:
        data = dict(item)
        data.setdefault("analysis_provider", analysis_provider)
        data.setdefault("analysis_model", analysis_model)
        analysis = AssetAnalysis.model_validate(data)
        if analysis.segment_id not in allowed_segment_ids:
            raise ValueError(f"unknown segment_id: {analysis.segment_id!r}")
        if analysis.segment_id in seen:
            raise ValueError(f"duplicate segment_id in model output: {analysis.segment_id!r}")
        seen.add(analysis.segment_id)
        allowed_ev = allowed_evidence_by_segment.get(analysis.segment_id, set())
        unknown_ev = [e for e in analysis.evidence_frame_ids if e not in allowed_ev]
        if unknown_ev:
            raise ValueError(
                f"unknown evidence_frame_id(s) for {analysis.segment_id}: {unknown_ev}"
            )
        out.append(analysis)
    return out


def _norm01(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return float(min(1.0, max(0.0, (x - lo) / (hi - lo))))


def deterministic_fallback_analysis(
    bundle: SegmentBundle,
    *,
    analysis_provider: Literal["anthropic", "openai"] = "anthropic",
    analysis_model: str = "deterministic",
) -> AssetAnalysis:
    """Minimal AssetAnalysis from deterministic features when the API fails."""
    seg = bundle.segment
    feat = bundle.features
    blur = 0.0
    bright = 0.5
    contrast = 0.3
    motion = 0.0
    if feat and feat.frames:
        blur = float(sum(f.blur for f in feat.frames) / len(feat.frames))
        bright = float(sum(f.brightness for f in feat.frames) / len(feat.frames))
        contrast = float(sum(f.contrast for f in feat.frames) / len(feat.frames))
        motion = float(feat.mean_motion)
    tech = round(
        0.45 * _norm01(blur, 0.0, 400.0)
        + 0.35 * (1.0 - abs(bright - 0.5) * 2.0)
        + 0.20 * min(1.0, contrast / 0.35),
        4,
    )
    tech = float(min(1.0, max(0.0, tech)))
    motion_q = round(min(1.0, motion / 3.0), 4)
    is_still = media_type_for(seg.source_file) == MediaType.still_video
    facts = [
        f"duration {seg.duration:.2f}s",
        f"brightness≈{bright:.2f}",
    ]
    if feat:
        facts.append("static scene" if feat.is_static else "moving scene")
    return AssetAnalysis(
        asset_id=f"asset:{seg.segment_id}",
        segment_id=seg.segment_id,
        source_file=seg.source_file,
        media_type=media_type_for(seg.source_file),
        upload_index=seg.upload_index,
        capture_time=seg.capture_time,
        evidence_frame_ids=list(bundle.expected_evidence_ids),
        subjects=["person"] if is_still else ["scene"],
        scene=SceneKind.portrait if is_still else SceneKind.other,
        shot_type=ShotType.medium if is_still else ShotType.unknown,
        action_progression="static frame" if is_still or (feat and feat.is_static) else "motion present",
        mood=Mood.calm,
        technical_quality=tech,
        aesthetic_value=0.45,
        emotional_value=0.4,
        narrative_value=0.35,
        hook_potential=0.25 if is_still else min(0.5, motion_q),
        motion_quality=0.05 if is_still else motion_q,
        novelty=0.4,
        redundancy_group=None,
        focus_x=0.5,
        focus_y=0.45,
        focus_width=0.55,
        focus_height=0.65,
        crop_confidence=0.25,
        visually_grounded_facts=facts,
        uncertainty=0.85,
        analysis_provider=analysis_provider,
        analysis_model=analysis_model,
    )


def _load_bundles(
    project_dir: Path,
    manifest: EvidenceManifest,
) -> list[SegmentBundle]:
    feats_path = project_dir / "deterministic_features.json"
    feats_by_id: dict[str, SegmentFeatures] = {}
    if feats_path.exists():
        feats_file = DeterministicFeaturesFile.model_validate_json(
            feats_path.read_text(encoding="utf-8")
        )
        feats_by_id = {s.segment_id: s for s in feats_file.segments}

    allowed_ids: set[str] | None = None
    cand_path = project_dir / "candidates.json"
    if cand_path.exists():
        cands = CandidatesFile.model_validate_json(cand_path.read_text(encoding="utf-8"))
        if cands.candidates:
            allowed_ids = {c.segment_id for c in cands.candidates}

    bundles: list[SegmentBundle] = []
    for seg in manifest.segments:
        if allowed_ids is not None and seg.segment_id not in allowed_ids:
            continue
        if not seg.evidence:
            continue
        paths: list[Path] = []
        eids: list[str] = []
        for ev in seg.evidence:
            p = project_dir / ev.frame_path
            if not p.exists():
                continue
            paths.append(p)
            eids.append(ev.evidence_id)
        if not paths:
            continue
        bundles.append(
            SegmentBundle(
                segment=seg,
                features=feats_by_id.get(seg.segment_id),
                evidence_paths=paths,
                expected_evidence_ids=eids,
            )
        )
    return bundles


def _bundle_user_content(bundles: list[SegmentBundle]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    catalog_lines: list[str] = []
    for b in bundles:
        seg = b.segment
        catalog_lines.append(
            f"- segment_id={seg.segment_id} source_file={seg.source_file} "
            f"upload_index={seg.upload_index} media_type={media_type_for(seg.source_file).value} "
            f"evidence_frame_ids={b.expected_evidence_ids} "
            f"capture_time={seg.capture_time!r}"
        )
        if b.features:
            catalog_lines.append(
                f"  deterministic: duration={b.features.duration_sec} "
                f"mean_motion={b.features.mean_motion} "
                f"is_static={b.features.is_static} is_high_motion={b.features.is_high_motion} "
                f"audio_rms={b.features.audio_rms} onset_density={b.features.onset_density}"
            )
        content.append(
            {
                "type": "text",
                "text": (
                    f"=== TEMPORAL BUNDLE segment_id={seg.segment_id} "
                    f"(frames in time order) ==="
                ),
            }
        )
        for eid, path in zip(b.expected_evidence_ids, b.evidence_paths, strict=True):
            content.append({"type": "text", "text": f"evidence_frame_id={eid}"})
            content.append(encode_image_block(path))

    content.append(
        {
            "type": "text",
            "text": (
                "Catalog (ONLY these ids are legal):\n"
                + "\n".join(catalog_lines)
                + "\n\nSubmit exactly one AssetAnalysis per segment via submit_asset_analyses. "
                "evidence_frame_ids must be chosen from that segment's list."
            ),
        }
    )
    return content


async def _anthropic_analyze_batch(
    client: anthropic.AsyncAnthropic,
    *,
    model: str,
    bundles: list[SegmentBundle],
    operation: str,
    retry_count: int = 0,
) -> tuple[list[dict[str, Any]], CallUsage]:
    timer = UsageTimer()
    usage = CallUsage(
        provider="anthropic",
        model=model,
        operation=operation,
        retry_count=retry_count,
    )
    try:
        response = await client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_RULES,
            tools=[SUBMIT_ASSET_ANALYSES_TOOL],
            tool_choice={"type": "tool", "name": "submit_asset_analyses"},
            messages=[{"role": "user", "content": _bundle_user_content(bundles)}],
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
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "submit_asset_analyses":
            tool_input = dict(block.input)
            break
    if tool_input is None:
        usage.ok = False
        usage.error = "missing tool_use submit_asset_analyses"
        record_usage(usage)
        raise RuntimeError("Anthropic response missing submit_asset_analyses tool_use")

    record_usage(usage)
    return list(tool_input.get("analyses") or []), usage


def _openai_fallback_analyze(
    bundles: list[SegmentBundle],
    *,
    model: str,
    allowed_segment_ids: set[str],
) -> tuple[list[AssetAnalysis], CallUsage]:
    """Inactive path — only when ZLOG_ANALYZER_FALLBACK=openai after Claude failure."""
    from pipeline.ai.openai_provider import OpenAIProvider

    provider = OpenAIProvider(default_model=model)
    # Flatten: one representative image per segment (bundle mid), keep ids aligned.
    image_paths: list[Path] = []
    segment_ids: list[str] = []
    source_files: list[str] = []
    for b in bundles:
        mid = b.evidence_paths[len(b.evidence_paths) // 2]
        image_paths.append(mid)
        segment_ids.append(b.segment.segment_id)
        source_files.append(b.segment.source_file)
    analyses, usage = provider.analyze_assets(
        image_paths=image_paths,
        segment_ids=segment_ids,
        source_files=source_files,
        allowed_segment_ids=allowed_segment_ids,
        model=model,
    )
    # Re-validate evidence ids against bundles (OpenAI path may omit extras).
    allowed_ev = {b.segment.segment_id: set(b.expected_evidence_ids) for b in bundles}
    cleaned: list[AssetAnalysis] = []
    for a in analyses:
        ev = [e for e in a.evidence_frame_ids if e in allowed_ev.get(a.segment_id, set())]
        if not ev:
            ev = list(allowed_ev.get(a.segment_id, []))
        cleaned.append(a.model_copy(update={"evidence_frame_ids": ev}))
    return cleaned, usage


def _chunk(items: list[SegmentBundle], size: int) -> list[list[SegmentBundle]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _run_haiku_pass(
    client: anthropic.AsyncAnthropic,
    bundles: list[SegmentBundle],
    model: str,
) -> tuple[dict[str, AssetAnalysis], dict[str, EscalationDecision], list[CallUsage]]:
    by_id = {b.segment.segment_id: b for b in bundles}
    results: dict[str, AssetAnalysis] = {}
    decisions: dict[str, EscalationDecision] = {}
    usages: list[CallUsage] = []

    async def one_batch(batch: list[SegmentBundle]) -> None:
        try:
            raw, usage = await _anthropic_analyze_batch(
                client, model=model, bundles=batch, operation="analyze_assets_haiku"
            )
            usages.append(usage)
            try:
                parsed = validate_analyses(
                    raw,
                    allowed_segment_ids={b.segment.segment_id for b in batch},
                    allowed_evidence_by_segment={
                        b.segment.segment_id: set(b.expected_evidence_ids) for b in batch
                    },
                    analysis_provider="anthropic",
                    analysis_model=model,
                )
                parsed_by = {a.segment_id: a for a in parsed}
                for b in batch:
                    a = parsed_by.get(b.segment.segment_id)
                    if a is None:
                        decisions[b.segment.segment_id] = EscalationDecision(
                            b.segment.segment_id, ["structured_output_validation_failed"]
                        )
                        continue
                    # Fill provider/model in case tool omitted them inconsistently.
                    a = a.model_copy(
                        update={"analysis_provider": "anthropic", "analysis_model": model}
                    )
                    results[b.segment.segment_id] = a
                    decisions[b.segment.segment_id] = needs_sonnet_escalation(
                        a, expected_evidence_ids=b.expected_evidence_ids
                    )
            except ValueError:
                # One structured failure → escalate each segment in the batch once.
                for b in batch:
                    decisions[b.segment.segment_id] = EscalationDecision(
                        b.segment.segment_id, ["structured_output_validation_failed"]
                    )
        except Exception as exc:  # noqa: BLE001 — network/SDK failures become deterministic fill
            usages.append(
                CallUsage(
                    provider="anthropic",
                    model=model,
                    operation="analyze_assets_haiku",
                    ok=False,
                    error=str(exc)[:300],
                )
            )
            for b in batch:
                decisions[b.segment.segment_id] = EscalationDecision(
                    b.segment.segment_id, ["api_failure"]
                )

    await asyncio.gather(*[one_batch(batch) for batch in _chunk(bundles, BATCH_SIZE)])
    # Mark missing (never returned) as validation failure for escalation.
    for sid in by_id:
        if sid not in decisions:
            decisions[sid] = EscalationDecision(sid, ["structured_output_validation_failed"])
    return results, decisions, usages


async def _run_sonnet_pass(
    client: anthropic.AsyncAnthropic,
    bundles: list[SegmentBundle],
    model: str,
) -> tuple[dict[str, AssetAnalysis], list[CallUsage]]:
    results: dict[str, AssetAnalysis] = {}
    usages: list[CallUsage] = []
    if not bundles:
        return results, usages

    async def one_batch(batch: list[SegmentBundle]) -> None:
        try:
            raw, usage = await _anthropic_analyze_batch(
                client, model=model, bundles=batch, operation="analyze_assets_sonnet"
            )
            usages.append(usage)
            parsed = validate_analyses(
                raw,
                allowed_segment_ids={b.segment.segment_id for b in batch},
                allowed_evidence_by_segment={
                    b.segment.segment_id: set(b.expected_evidence_ids) for b in batch
                },
                analysis_provider="anthropic",
                analysis_model=model,
            )
            for a in parsed:
                results[a.segment_id] = a.model_copy(
                    update={"analysis_provider": "anthropic", "analysis_model": model}
                )
        except Exception as exc:  # noqa: BLE001
            usages.append(
                CallUsage(
                    provider="anthropic",
                    model=model,
                    operation="analyze_assets_sonnet",
                    ok=False,
                    error=str(exc)[:300],
                )
            )

    await asyncio.gather(*[one_batch(batch) for batch in _chunk(bundles, BATCH_SIZE)])
    return results, usages


def write_debug_report(
    project_dir: Path,
    project: str,
    analyses: list[AssetAnalysis],
    escalations: dict[str, EscalationDecision],
    bundles: list[SegmentBundle],
) -> Path:
    by_bundle = {b.segment.segment_id: b for b in bundles}
    cards: list[str] = []
    for a in analyses:
        b = by_bundle.get(a.segment_id)
        thumbs = ""
        if b:
            for eid, path in zip(b.expected_evidence_ids, b.evidence_paths, strict=False):
                rel = path.relative_to(project_dir).as_posix()
                thumbs += (
                    f'<figure><img src="{html.escape(rel)}" alt="{html.escape(eid)}"/>'
                    f"<figcaption>{html.escape(eid)}</figcaption></figure>"
                )
        esc = escalations.get(a.segment_id)
        esc_html = ""
        if esc and esc.reasons:
            esc_html = (
                "<p class='esc'>escalated: "
                + html.escape(", ".join(esc.reasons))
                + "</p>"
            )
        facts = "".join(f"<li>{html.escape(f)}</li>" for f in a.visually_grounded_facts)
        cards.append(
            f"""
<article>
  <h2>{html.escape(a.segment_id)}</h2>
  {esc_html}
  <div class="thumbs">{thumbs}</div>
  <ul>
    <li>model: {html.escape(a.analysis_model)}</li>
    <li>subjects: {html.escape(", ".join(a.subjects))}</li>
    <li>scene/shot/mood: {a.scene.value} / {a.shot_type.value} / {a.mood.value}</li>
    <li>action: {html.escape(a.action_progression)}</li>
    <li>tech/aesthetic/emotion/narrative/hook:
        {a.technical_quality:.2f}/{a.aesthetic_value:.2f}/{a.emotional_value:.2f}/
        {a.narrative_value:.2f}/{a.hook_potential:.2f}</li>
    <li>crop: ({a.focus_x:.2f},{a.focus_y:.2f}) {a.focus_width:.2f}x{a.focus_height:.2f}
        conf={a.crop_confidence:.2f}</li>
    <li>uncertainty: {a.uncertainty:.2f}</li>
    <li>redundancy_group: {html.escape(a.redundancy_group or "-")}</li>
  </ul>
  <ol>{facts}</ol>
</article>
"""
        )
    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>asset analysis — {html.escape(project)}</title>
<style>
body {{ font-family: ui-sans-serif, system-ui, sans-serif; background:#111; color:#eee; margin:24px; }}
article {{ border:1px solid #333; padding:16px; margin:0 0 20px; border-radius:8px; }}
.thumbs {{ display:flex; flex-wrap:wrap; gap:8px; }}
figure {{ margin:0; }}
img {{ max-height:160px; border:1px solid #444; }}
.esc {{ color:#f6c; }}
h1,h2 {{ font-weight:600; }}
</style></head><body>
<h1>asset analysis — {html.escape(project)}</h1>
<p>{len(analyses)} segment(s)</p>
{"".join(cards)}
</body></html>
"""
    out = project_dir / "asset_analysis_report.html"
    out.write_text(doc, encoding="utf-8")
    return out


def analyze_assets(
    work_dir: Path,
    project: str,
    *,
    config: ModelConfig | None = None,
    client: anthropic.AsyncAnthropic | None = None,
    force: bool = False,
) -> Path:
    """Run Haiku analysis + selective Sonnet escalation; write artifacts."""
    load_dotenv()
    cfg = config or load_model_config()
    project_dir = work_dir / project
    out_path = project_dir / "asset_analyses.json"
    if out_path.exists() and not force:
        click.echo(f"{out_path} exists, skipping (use --force to overwrite)")
        return out_path

    manifest_path = project_dir / "evidence_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"{manifest_path} missing — run pipeline.evidence first"
        )
    manifest = EvidenceManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    bundles = _load_bundles(project_dir, manifest)
    if not bundles:
        raise ValueError(f"no evidence bundles to analyze in {project_dir}")

    usages: list[dict[str, Any]] = []
    escalations: dict[str, EscalationDecision] = {}
    final: dict[str, AssetAnalysis] = {}

    use_openai_fallback = (
        os.getenv("ZLOG_ANALYZER_FALLBACK", "").strip().lower() == "openai"
    )

    # Happy path: Anthropic only (even if analyzer_provider env is openai — runtime
    # default for this stage is Claude Haiku unless explicitly falling back).
    haiku_model = cfg.analyzer_model
    sonnet_model = cfg.analyzer_escalation_model

    try:
        aclient = client or anthropic.AsyncAnthropic()
        haiku_results, decisions, haiku_usages = asyncio.run(
            _run_haiku_pass(aclient, bundles, haiku_model)
        )
        usages.extend(u.as_dict() for u in haiku_usages)
        escalations = decisions
        final.update(haiku_results)

        escalate_ids = [
            sid for sid, d in decisions.items() if d.should_escalate and d.reasons != ["api_failure"]
        ]
        # api_failure on Haiku → try Sonnet for those too (still not "all segments")
        escalate_ids += [
            sid for sid, d in decisions.items() if "api_failure" in d.reasons
        ]
        escalate_ids = list(dict.fromkeys(escalate_ids))
        escalate_bundles = [b for b in bundles if b.segment.segment_id in escalate_ids]

        if escalate_bundles:
            click.echo(
                f"escalating {len(escalate_bundles)}/{len(bundles)} segment(s) → {sonnet_model}"
            )
            sonnet_results, sonnet_usages = asyncio.run(
                _run_sonnet_pass(aclient, escalate_bundles, sonnet_model)
            )
            usages.extend(u.as_dict() for u in sonnet_usages)
            final.update(sonnet_results)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"anthropic analyze failed: {exc}", err=True)
        if use_openai_fallback:
            click.echo("using inactive OpenAI Luna fallback (ZLOG_ANALYZER_FALLBACK=openai)")
            try:
                analyses, usage = _openai_fallback_analyze(
                    bundles,
                    model=cfg.evaluator_model,  # gpt-5.6-luna by default
                    allowed_segment_ids={b.segment.segment_id for b in bundles},
                )
                usages.append(usage.as_dict())
                for a in analyses:
                    final[a.segment_id] = a
            except Exception as openai_exc:  # noqa: BLE001
                click.echo(f"openai fallback failed: {openai_exc}", err=True)

    # Deterministic fill for anything still missing.
    for b in bundles:
        if b.segment.segment_id not in final:
            final[b.segment.segment_id] = deterministic_fallback_analysis(b)
            escalations.setdefault(
                b.segment.segment_id,
                EscalationDecision(b.segment.segment_id, ["deterministic_fallback"]),
            )

    # Stable order: evidence manifest / upload time_order.
    order = {b.segment.segment_id: i for i, b in enumerate(bundles)}
    analyses = sorted(final.values(), key=lambda a: order.get(a.segment_id, 10_000))

    # Final contract check (duplicates / bad ids).
    validate_analyses(
        [a.model_dump(mode="json") for a in analyses],
        allowed_segment_ids={b.segment.segment_id for b in bundles},
        allowed_evidence_by_segment={
            b.segment.segment_id: set(b.expected_evidence_ids) for b in bundles
        },
        analysis_provider="anthropic",
        analysis_model=haiku_model,
    )

    payload = {
        "project": project,
        "generator": "analyze_assets",
        "analyzer_model": haiku_model,
        "escalation_model": sonnet_model,
        "analyses": [a.model_dump(mode="json") for a in analyses],
        "escalations": {
            sid: d.reasons for sid, d in escalations.items() if d.reasons
        },
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    usage_path = project_dir / "asset_analysis_usage.json"
    total_cost = sum(float(u.get("estimated_cost_usd") or 0) for u in usages)
    usage_path.write_text(
        json.dumps(
            {
                "project": project,
                "calls": usages,
                "escalated_segment_ids": [
                    sid for sid, d in escalations.items() if d.should_escalate
                ],
                "estimated_cost_usd_total": round(total_cost, 6),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    report = write_debug_report(project_dir, project, analyses, escalations, bundles)
    click.echo(
        f"analyzed {len(analyses)} segment(s); "
        f"escalated {sum(1 for d in escalations.values() if d.should_escalate)}; "
        f"wrote {out_path.name}, {usage_path.name}, {report.name}"
    )
    return out_path


@click.command()
@click.option("--work-dir", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--project", required=True)
@click.option("--force", is_flag=True, default=False)
def main(work_dir: Path, project: str, force: bool) -> None:
    out = analyze_assets(work_dir, project, force=force)
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
