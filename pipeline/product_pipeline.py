"""End-to-end product pipeline shared by server.py and run.py (PROMPT 9).

Order:
  split → evidence(+features) → filter → sheet → analyze_assets → director
  → plan_timeline → evaluate_plan → render → grade → audio_engine → final

Quality modes (ZLOG_QUALITY_MODE / job form):
  economy  — Haiku only, Sonnet StoryPlan, code eval, no GPT
  balanced — Haiku + selective Sonnet, Luna eval, Claude revise, no Sol
  premium  — aggressive Sonnet escalate, Luna, Claude revise, Sol if needed
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from pipeline.ai.config import ModelConfig, QualityMode, load_model_config
from pipeline.creative_execution import finalize_comparison, preserve_previous_render
from pipeline.execution import DagExecutor, PipelineTask, TaskState

StageName = Literal[
    "split",
    "evidence",
    "features",
    "filter",
    "perception",
    "sheet",
    "analyze",
    "director",
    "plan",
    "evaluate",
    "render",
    "grade",
    "audio",
    "done",
]

PRODUCT_STAGES: list[str] = [
    "split",
    "evidence",
    "filter",
    "perception",
    "sheet",
    "analyze",
    "director",
    "plan",
    "evaluate",
    "render",
    "grade",
    "audio",
]

OnStage = Callable[[str, dict[str, Any]], None]


def _soft_promote_candidates(project_dir: Path) -> None:
    """If filter emptied candidates, promote soft still rejects (web/CLI shared)."""
    path = project_dir / "candidates.json"
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("candidates"):
        return
    rejected = data.get("rejected") or []
    if not rejected:
        raise RuntimeError("No usable clips after quality check.")
    stills = [
        item
        for item in rejected
        if str(item.get("source_file", "")).startswith("still_")
        or item.get("quality", {}).get("verdict") == "blurry"
    ]
    promote = stills or rejected
    for item in promote:
        item.setdefault("quality", {})["verdict"] = "pass"
    data["candidates"] = promote
    data["rejected"] = [r for r in rejected if r not in promote]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass
class StageTelemetry:
    stage: str
    provider: str | None = None
    model: str | None = None
    latency_ms: float = 0.0
    retry_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    escalated: bool = False
    ok: bool = True
    error: str | None = None

    def public_dict(self) -> dict[str, Any]:
        """Safe for UI — never includes prompts or API keys."""
        return asdict(self)


@dataclass
class PipelineResult:
    project: str
    quality_mode: str
    generator: str
    final_path: Path
    stages: list[StageTelemetry] = field(default_factory=list)
    estimated_cost_usd_total: float = 0.0

    def public_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "quality_mode": self.quality_mode,
            "generator": self.generator,
            "final": str(self.final_path),
            "estimated_cost_usd_total": round(self.estimated_cost_usd_total, 6),
            "stages": [s.public_dict() for s in self.stages],
        }


def escalation_threshold_for_mode(mode: QualityMode | str) -> float | None:
    """None = never escalate to Sonnet (economy). Lower = more aggressive."""
    m = str(mode).strip().lower()
    if m == "economy":
        return None
    if m in ("premium", "max"):
        return 0.35
    return 0.55  # balanced


def _load_usage_file(path: Path) -> tuple[float, dict[str, Any]]:
    if not path.exists():
        return 0.0, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0.0, {}
    cost = float(data.get("estimated_cost_usd_total") or 0.0)
    return cost, data


def _usage_to_telemetry(
    stage: str, usage_path: Path, *, escalated: bool = False
) -> StageTelemetry:
    cost, data = _load_usage_file(usage_path)
    calls = data.get("calls") or []
    inp = sum(int(c.get("input_tokens") or 0) for c in calls)
    out = sum(int(c.get("output_tokens") or 0) for c in calls)
    retries = sum(int(c.get("retry_count") or 0) for c in calls)
    latency = sum(float(c.get("latency_ms") or 0) for c in calls)
    provider = None
    model = None
    if calls:
        provider = calls[-1].get("provider")
        model = calls[-1].get("model")
    model = model or data.get("director_model") or data.get("evaluator_model")
    return StageTelemetry(
        stage=stage,
        provider=provider,
        model=model,
        latency_ms=round(latency, 2),
        retry_count=retries,
        input_tokens=inp,
        output_tokens=out,
        estimated_cost_usd=round(cost, 6),
        escalated=escalated
        or bool(data.get("escalated_segment_ids"))
        or bool(data.get("sol_used")),
        ok=True,
    )


def run_product_pipeline(
    *,
    work_root: Path,
    footage_dir: Path,
    project: str,
    bgm_track: Path | None,
    target_duration_s: float,
    quality_mode: QualityMode | str | None = None,
    force: bool = True,
    from_stage: str = "split",
    user_intent: str | None = None,
    config: ModelConfig | None = None,
    on_stage: OnStage | None = None,
    render_progress: Callable[[int, int], None] | None = None,
    render_scale: float | None = None,
    render_concurrency: int | None = None,
    use_hybrid: bool = True,
) -> PipelineResult:
    """Run the editorial pipeline (or the explicitly requested legacy baseline)."""
    from pipeline import evidence as evidence_stage
    from pipeline import filter as filter_stage
    from pipeline import select_baseline, sheet
    from pipeline import split as split_stage
    from pipeline.adaptive_perception import run_adaptive_perception
    from pipeline.analyze_assets import analyze_assets
    from pipeline.audio_engine import run_audio_engine
    from pipeline.editorial_intelligence import run_editorial_intelligence
    from pipeline.perception_models import EditingGoal
    from run import _stage_grade, _stage_render

    cfg = config or load_model_config()
    mode: QualityMode = quality_mode or cfg.quality_mode  # type: ignore[assignment]
    # Override config quality for this run (evaluate_plan / Sol gates).
    cfg = ModelConfig(
        analyzer_provider=cfg.analyzer_provider,
        analyzer_model=cfg.analyzer_model,
        analyzer_escalation_model=cfg.analyzer_escalation_model,
        director_provider=cfg.director_provider,
        director_model=cfg.director_model,
        evaluator_provider=cfg.evaluator_provider,
        evaluator_model=cfg.evaluator_model,
        repair_provider=cfg.repair_provider,
        repair_model=cfg.repair_model,
        quality_mode=mode
        if mode in ("economy", "balanced", "max", "premium")
        else "balanced",  # type: ignore[arg-type]
    )

    project_dir = work_root / project
    project_dir.mkdir(parents=True, exist_ok=True)
    if force:
        preserve_previous_render(project_dir)
    if user_intent and user_intent.strip():
        (project_dir / "note.txt").write_text(user_intent.strip(), encoding="utf-8")

    stages: list[StageTelemetry] = []
    total_cost = 0.0
    generator = "editorial"

    def emit(stage: str, **extra: Any) -> None:
        if on_stage:
            payload = {
                "stage": stage,
                "quality_mode": cfg.quality_mode,
                "generator": generator,
                "telemetry": [s.public_dict() for s in stages],
                "estimated_cost_usd_total": round(total_cost, 6),
            }
            payload.update(extra)
            on_stage(stage, payload)

    def timed(
        stage: str,
        fn: Callable[[], Any],
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        nonlocal total_cost
        t0 = time.perf_counter()
        try:
            fn()
            tel = StageTelemetry(
                stage=stage,
                provider=provider,
                model=model,
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
            stages.append(tel)
            emit(stage)
        except Exception as exc:
            stages.append(
                StageTelemetry(
                    stage=stage,
                    provider=provider,
                    model=model,
                    latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                    ok=False,
                    error=str(exc)[:300],
                )
            )
            emit(stage, error=str(exc)[:300])
            raise

    start_idx = PRODUCT_STAGES.index(from_stage) if from_stage in PRODUCT_STAGES else 0
    todo = PRODUCT_STAGES[start_idx:]
    execution_runs: list[dict[str, Any]] = []

    # These inputs are independent. Keep their expensive/blocking work out of the
    # event loop and bounded before downstream stages consume either artifact.
    prepare_tasks: list[PipelineTask] = []
    if "split" in todo:
        emit("split")
        prepare_tasks.append(
            PipelineTask(
                task_id="split",
                stage="split",
                work=lambda: split_stage.run_split(footage_dir, work_root, force=force),
                resource_class="ffmpeg",
                is_cached=(lambda: (project_dir / "segments.json").exists())
                if not force
                else None,
            )
        )
    if not use_hybrid:
        if bgm_track is None:
            raise ValueError("The legacy baseline requires a BGM track.")
        beats_json = bgm_track.with_suffix(".beats.json")
        emit("beats")
        from pipeline.beats import extract_beat_grid

        prepare_tasks.append(
            PipelineTask(
                task_id="beats",
                stage="beats",
                work=lambda: extract_beat_grid(bgm_track),
                resource_class="cpu",
                is_cached=beats_json.exists,
            )
        )
    if prepare_tasks:
        prepared = asyncio.run(DagExecutor(strict=True).run(prepare_tasks))
        execution_runs.extend(run.public_dict() for run in prepared.values())
        for task in prepare_tasks:
            run = prepared[task.task_id]
            stages.append(
                StageTelemetry(
                    stage=task.stage,
                    provider="ffmpeg" if task.resource_class == "ffmpeg" else "librosa",
                    model="pyscenedetect" if task.task_id == "split" else "beat_track",
                    latency_ms=round(run.wall_ms, 2),
                    ok=run.state in (TaskState.completed, TaskState.skipped_cached),
                    error=run.error,
                )
            )
            emit(task.stage)

    # --- evidence (+ deterministic features written inside) ---
    if "evidence" in todo:
        emit("evidence")
        timed(
            "evidence",
            lambda: evidence_stage.run_evidence(
                work_root, project, footage_dir=footage_dir, force=force
            ),
            provider="opencv",
            model="deterministic",
        )
        # Named features checkpoint for UI
        feats = project_dir / "deterministic_features.json"
        if feats.exists():
            stages.append(
                StageTelemetry(
                    stage="features", provider="opencv", model="deterministic"
                )
            )
            emit("features")

    # --- filter ---
    if "filter" in todo:
        emit("filter")

        def _filter() -> None:
            filter_stage.filter_scenes(work_root, project)
            _soft_promote_candidates(project_dir)

        timed(
            "filter",
            _filter,
            provider="opencv",
            model="deterministic",
        )

    # --- adaptive perception (local, independently cached passes) ---
    if "perception" in todo:
        emit("perception")
        timed(
            "perception",
            lambda: run_adaptive_perception(
                work_root,
                project,
                footage_dir=footage_dir,
                editing_goal=EditingGoal(
                    target_duration_seconds=target_duration_s,
                    pacing="fast" if target_duration_s <= 20 else "balanced",
                    narrative_preference=user_intent or "",
                ),
                force=force,
            ),
            provider="opencv",
            model="adaptive-perception-v1",
        )

    # --- sheet ---
    if "sheet" in todo:
        emit("sheet")
        timed(
            "sheet",
            lambda: sheet.build_contact_sheets(work_root, project),
            provider="pillow",
            model="deterministic",
        )

    if use_hybrid and any(
        s in todo for s in ("analyze", "director", "plan", "evaluate")
    ):
        try:
            # --- analyze ---
            if "analyze" in todo:
                emit("analyze")
                thr = escalation_threshold_for_mode(cfg.quality_mode)
                t0 = time.perf_counter()
                analyze_assets(
                    work_root,
                    project,
                    config=cfg,
                    force=force,
                    uncertainty_threshold=thr,
                )
                usage_path = project_dir / "asset_analysis_usage.json"
                tel = _usage_to_telemetry("analyze", usage_path)
                tel.latency_ms = round((time.perf_counter() - t0) * 1000, 2)
                if thr is None:
                    tel.escalated = False
                cost, data = _load_usage_file(usage_path)
                tel.escalated = bool(
                    (data.get("escalated_segment_ids") or [])
                    if isinstance(data, dict)
                    else False
                )
                total_cost += cost
                stages.append(tel)
                emit("analyze")

            # --- editorial intelligence ---
            # One content-led pass owns TripGraph, separate long/short plans,
            # actionable critics, and the renderer migration adapter.
            editorial_stages = [
                stage for stage in ("director", "plan", "evaluate") if stage in todo
            ]
            if editorial_stages:
                emit("director")
                timed(
                    "director",
                    lambda: run_editorial_intelligence(
                        work_root,
                        project,
                        user_prompt=user_intent or "",
                    ),
                    provider="code",
                    model="personalized-editorial-intelligence-v1",
                )
                for stage in editorial_stages:
                    if stage == "director":
                        continue
                    stages.append(
                        StageTelemetry(
                            stage=stage,
                            provider="code",
                            model=(
                                "dual-long-short-planner-v2"
                                if stage == "plan"
                                else "actionable-critic-v1"
                            ),
                        )
                    )
                    emit(stage)

            if not (project_dir / "edl_ai.json").exists():
                raise RuntimeError("editorial path produced no edl_ai.json")
            generator = "editorial"
        except Exception as editorial_exc:
            # A broken editorial contract must be visible. Silently replacing it
            # with order-based effects would produce a valid file but a wrong film.
            stages.append(
                StageTelemetry(
                    stage="editorial_contract",
                    provider="code",
                    model="personalized-editorial-intelligence-v1",
                    ok=False,
                    error=str(editorial_exc)[:300],
                )
            )
            emit("editorial_contract", error=str(editorial_exc)[:300])
            raise
    else:
        # Explicit baseline path
        if bgm_track is None:
            raise ValueError("The legacy baseline requires a BGM track.")
        generator = "baseline"
        emit("select")
        timed(
            "select",
            lambda: select_baseline.select_baseline(
                work_root, project, bgm_track, target_duration_s
            ),
            provider="code",
            model="select_baseline",
        )

    # --- render ---
    if "render" in todo:
        emit("render", progress_pct=0)
        t0 = time.perf_counter()
        _stage_render(
            project_dir,
            force=force,
            progress=render_progress,
            scale=render_scale,
            concurrency=render_concurrency,
        )
        stages.append(
            StageTelemetry(
                stage="render",
                provider="remotion",
                model="ZlogFilm",
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
        )
        emit("render", progress_pct=100)

    # --- grade ---
    if "grade" in todo:
        emit("grade")
        timed(
            "grade",
            lambda: _stage_grade(project_dir, force=force),
            provider="ffmpeg",
            model="lut3d",
        )

    # --- audio ---
    if "audio" in todo:
        emit("audio")
        final = project_dir / "final.mp4"
        t0 = time.perf_counter()
        outs = run_audio_engine(
            work_root,
            project,
            footage_dir=footage_dir,
            video_path=final if final.exists() else project_dir / "render.mp4",
            force=force,
        )
        mixed = outs.get("audio_mixed_video")
        if mixed and Path(mixed).exists() and final.exists():
            shutil.copy2(mixed, final)
        stages.append(
            StageTelemetry(
                stage="audio",
                provider="ffmpeg",
                model="audio_engine",
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
        )
        emit("audio")

    final_path = project_dir / "final.mp4"
    if not final_path.exists():
        raise RuntimeError("pipeline finished without final.mp4")
    finalize_comparison(project_dir)

    # Persist run summary (no secrets)
    summary = PipelineResult(
        project=project,
        quality_mode=cfg.quality_mode,
        generator=generator,
        final_path=final_path,
        stages=stages,
        estimated_cost_usd_total=round(total_cost, 6),
    )
    payload = summary.public_dict()
    payload["execution"] = {
        "tasks": execution_runs,
        "max_configured_concurrency": {
            "cpu": DagExecutor().limits.cpu,
            "ffmpeg": DagExecutor().limits.ffmpeg,
            "anthropic": DagExecutor().limits.anthropic,
            "openai": DagExecutor().limits.openai,
            "external_video": DagExecutor().limits.external_video,
        },
    }
    (project_dir / "pipeline_run.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    emit("done", generator=summary.generator)
    return summary
