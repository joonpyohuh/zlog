"""Batch render — four EditTimelines in, four mp4s out.

Sequential on purpose: correctness and a readable failure log matter more than
wall-clock here, and Remotion already parallelises frames internally.

Each variant's EDL is written to disk and rendered through the *existing*
render path (resolve-props.mjs then remotion render, via run.py's _stage_render)
so the taste loop can never drift from what the product actually renders.

Outputs land in work/<project>/taste_loop/<round_id>/<timeline_id>.mp4, so the
video is linked to its timeline by filename as well as by the stored record.
A failed variant is reported and recorded, never swallowed.
"""

from __future__ import annotations

import shutil
import time
import traceback
from collections.abc import Callable
from pathlib import Path

from pipeline.taste_loop.store import RenderedVariant
from pipeline.taste_loop.variants import EditTimeline

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# The renderer's font preload (render/src/loadFonts.ts) trips its own 60s
# delayRender timeout when the machine is busy — each headless Chrome tab
# loads the four font files independently, so back-to-back renders starve each
# other. Retrying usually clears it. A variant that fails every attempt is
# reported as failed with all errors kept, and can be re-rendered later with
# `taste_loop.cli rerender` without rebuilding the round.
RENDER_ATTEMPTS = 3


def round_output_dir(project: str, round_id: str, repo_root: Path | None = None) -> Path:
    root = repo_root or REPO_ROOT
    return root / "work" / project / "taste_loop" / round_id


def render_variants(
    timelines: list[EditTimeline],
    *,
    repo_root: Path | None = None,
    scale: float | None = None,
    concurrency: int | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> list[RenderedVariant]:
    """Render each timeline in order; return one record per variant.

    Never raises for a single variant's failure — the round should still be
    reviewable with three videos, as long as the missing one is visible as
    missing. An empty `timelines` list is a caller bug and does raise.
    """
    if not timelines:
        raise ValueError("render_variants got no timelines")

    # Imported here: run.py is the CLI entry point and importing it at module
    # scope would make `python -m pipeline.taste_loop.*` pay for click setup.
    from run import _stage_grade, _stage_render

    root = repo_root or REPO_ROOT
    results: list[RenderedVariant] = []

    def log(message: str) -> None:
        if on_progress:
            on_progress(message)

    for timeline in timelines:
        out_dir = round_output_dir(timeline.project, timeline.round_id, root)
        # _stage_render/_stage_grade are directory-shaped: they look for
        # edl_*.json and write render.mp4 / final.mp4 next to it. Give each
        # variant its own directory so the four never collide.
        variant_dir = out_dir / timeline.timeline_id
        variant_dir.mkdir(parents=True, exist_ok=True)
        (variant_dir / "edl_ai.json").write_text(
            timeline.edl.model_dump_json(indent=2), encoding="utf-8"
        )
        (variant_dir / "timeline.json").write_text(
            timeline.model_dump_json(indent=2), encoding="utf-8"
        )

        started = time.perf_counter()
        log(f"rendering {timeline.timeline_id} ({timeline.axis.value}={timeline.axis_value})")

        failures: list[str] = []
        record: RenderedVariant | None = None
        for attempt in range(1, RENDER_ATTEMPTS + 1):
            try:
                _stage_render(variant_dir, force=True, scale=scale, concurrency=concurrency)
                _stage_grade(variant_dir, force=True)
                final = variant_dir / "final.mp4"
                if not final.exists():
                    raise FileNotFoundError(f"render finished but {final} is missing")
                # Keep a flat copy named after the timeline so the id -> video
                # link is legible from the filesystem alone.
                flat = out_dir / f"{timeline.timeline_id}.mp4"
                shutil.copy2(final, flat)
                elapsed = round(time.perf_counter() - started, 2)
                record = RenderedVariant(
                    timeline_id=timeline.timeline_id,
                    variant_index=timeline.variant_index,
                    axis=timeline.axis,
                    axis_value=timeline.axis_value,
                    video_path=str(flat.relative_to(root)).replace("\\", "/"),
                    ok=True,
                    attempts=attempt,
                    render_seconds=elapsed,
                )
                log(f"  ok  {timeline.timeline_id} -> {flat} ({elapsed}s, attempt {attempt})")
                break
            except Exception as exc:  # noqa: BLE001 — one bad variant must not kill the round
                detail = f"{type(exc).__name__}: {exc}"
                failures.append(f"--- attempt {attempt} ---\n{detail}\n{traceback.format_exc()}")
                log(f"  attempt {attempt}/{RENDER_ATTEMPTS} failed: {detail[:180]}")

        if record is None:
            elapsed = round(time.perf_counter() - started, 2)
            (variant_dir / "render_error.txt").write_text(
                "\n\n".join(failures), encoding="utf-8"
            )
            record = RenderedVariant(
                timeline_id=timeline.timeline_id,
                variant_index=timeline.variant_index,
                axis=timeline.axis,
                axis_value=timeline.axis_value,
                video_path=None,
                ok=False,
                attempts=RENDER_ATTEMPTS,
                error=failures[-1].splitlines()[1][:500] if failures else "unknown render failure",
                render_seconds=elapsed,
            )
            log(f"  FAIL {timeline.timeline_id} after {RENDER_ATTEMPTS} attempts")

        results.append(record)

    return results
