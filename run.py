"""zlog orchestrator CLI.

    python run.py footage/trip_01 --bgm y2k_synth_01 --duration 15
    python run.py review work/trip_01

The first form (no subcommand keyword) chains split -> filter -> select
(baseline) -> render (Remotion) -> grade (ffmpeg LUT), writing
work/<project>/final.mp4. Every stage is also runnable on its own via
`python -m pipeline.<stage>` (see CLAUDE.md for each stage's I/O
contract) — this file adds no logic beyond wiring calls in order,
skip-if-exists bookkeeping, and error/timing reporting.

`review` walks the selected and dropped candidates from a finished
project one at a time and records keep/drop + reason into
taste/examples.jsonl (see pipeline/taste.py) for select_ai.py to draw on
later — this is the only place a human's own judgment enters the
pipeline's data, so it never touches segments.json/candidates.json.

`sheet.py`/`tag.py`/`select_ai.py` aren't part of the automatic chain:
sheet.py (contact sheets) is still a skeleton, so nothing downstream of it
can run unattended yet. Add a "tag"/"select-ai" path back in once
--use-ai exists and sheet.py actually produces sheets.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import click

from pipeline import filter as filter_stage
from pipeline import grade as grade_stage
from pipeline import select_baseline
from pipeline import split
from pipeline import taste
from pipeline.edl import CandidatesFile, EDL

REPO_ROOT = Path(__file__).resolve().parent
RENDER_DIR = REPO_ROOT / "render"

STAGES = ["split", "filter", "select", "render", "grade"]


class DefaultGroup(click.Group):
    """A click.Group where an unrecognized first token is treated as
    belonging to `default_command` instead of raising "no such command" —
    lets `python run.py footage/trip_01 ...` work without a subcommand
    keyword, while `python run.py review ...` still dispatches normally.
    """

    def __init__(self, *args, default_command: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_command = default_command

    def resolve_command(self, ctx, args):
        if args and args[0] not in self.commands:
            args = [self.default_command, *args]
        return super().resolve_command(ctx, args)


def _which(cmd: str) -> str:
    # subprocess.run() on Windows won't resolve PATHEXT (.cmd/.bat) shims
    # like npx on its own, so look the executable up ourselves.
    found = shutil.which(cmd)
    if not found:
        raise FileNotFoundError(f"'{cmd}' not found on PATH")
    return found


def _resolve_bgm(bgm_name: str) -> Path:
    bgm_dir = REPO_ROOT / "assets" / "bgm"
    for ext in ("mp3", "wav"):
        candidate = bgm_dir / f"{bgm_name}.{ext}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"no BGM file found for '{bgm_name}' under {bgm_dir} (.mp3/.wav)")


def _run_subprocess(
    cmd: list[str],
    cwd: Path,
    on_line: Callable[[str], None] | None = None,
) -> None:
    # node/npx can emit UTF-8 output (arrows, box-drawing chars) that the
    # OS locale's default codec can't decode — force utf-8 explicitly.
    if on_line is None:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if result.returncode != 0:
            stdout_tail = (result.stdout or "")[-1200:]
            stderr_tail = (result.stderr or "")[-1200:]
            raise RuntimeError(
                f"command failed (exit {result.returncode}): {' '.join(cmd)}\n"
                f"--- stdout (tail) ---\n{stdout_tail}\n--- stderr (tail) ---\n{stderr_tail}"
            )
        return

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    tail: list[str] = []
    for line in proc.stdout:
        tail.append(line)
        if len(tail) > 80:
            tail = tail[-80:]
        on_line(line)
    code = proc.wait()
    if code != 0:
        raise RuntimeError(
            f"command failed (exit {code}): {' '.join(cmd)}\n"
            f"--- output (tail) ---\n{''.join(tail)[-2000:]}"
        )


def _stage_split(project_dir: Path, footage_dir: Path, work_root: Path, force: bool, **_) -> None:
    out = project_dir / "segments.json"
    if out.exists() and not force:
        click.echo(f"  skip (exists): {out}")
        return
    split.run_split(footage_dir, work_root, force=force)


def _stage_filter(project_dir: Path, project: str, work_root: Path, force: bool, **_) -> None:
    out = project_dir / "candidates.json"
    if out.exists() and not force:
        click.echo(f"  skip (exists): {out}")
        return
    filter_stage.filter_scenes(work_root, project)


def _stage_select(
    project_dir: Path,
    project: str,
    work_root: Path,
    bgm_track: Path,
    target_duration_s: float,
    force: bool,
    **_,
) -> None:
    out = project_dir / "edl_baseline.json"
    if out.exists() and not force:
        click.echo(f"  skip (exists): {out}")
        return
    select_baseline.select_baseline(work_root, project, bgm_track, target_duration_s)


def _stage_render(
    project_dir: Path,
    force: bool,
    progress: Callable[[int, int], None] | None = None,
    scale: float | None = None,
    concurrency: int | None = None,
    **_,
) -> None:
    edl_path = project_dir / "edl_ai.json"
    if not edl_path.exists():
        edl_path = project_dir / "edl_baseline.json"
    if not edl_path.exists():
        raise FileNotFoundError(f"no edl_ai.json or edl_baseline.json in {project_dir}")

    out = project_dir / "render.mp4"
    if out.exists() and not force:
        click.echo(f"  skip (exists): {out}")
        return

    resolved_props = project_dir / "edl_resolved.json"
    _run_subprocess(
        [_which("node"), "resolve-props.mjs", str(edl_path.resolve()), str(resolved_props.resolve())],
        cwd=RENDER_DIR,
    )

    render_scale = scale if scale is not None else float(os.getenv("ZLOG_RENDER_SCALE", "1") or "1")
    workers = concurrency if concurrency is not None else (2 if os.name == "nt" else 4)
    cmd = [
        _which("npx"),
        "remotion",
        "render",
        "src/index.ts",
        "ZlogFilm",
        str(out.resolve()),
        f"--props={resolved_props.resolve()}",
        f"--concurrency={max(1, int(workers))}",
        f"--scale={render_scale}",
    ]

    rendered_re = re.compile(r"Rendered\s+(\d+)\s*/\s*(\d+)", re.IGNORECASE)

    def on_line(line: str) -> None:
        click.echo(line.rstrip())
        if progress is None:
            return
        match = rendered_re.search(line)
        if match:
            progress(int(match.group(1)), int(match.group(2)))

    _run_subprocess(cmd, cwd=RENDER_DIR, on_line=on_line if progress else None)


def _stage_grade(project_dir: Path, force: bool, **_) -> None:
    rendered = project_dir / "render.mp4"
    if not rendered.exists():
        raise FileNotFoundError(f"{rendered} missing — run the 'render' stage first")

    out = project_dir / "final.mp4"
    if out.exists() and not force:
        click.echo(f"  skip (exists): {out}")
        return

    edl_path = project_dir / "edl_ai.json"
    if not edl_path.exists():
        edl_path = project_dir / "edl_baseline.json"
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    lut_path = REPO_ROOT / "assets" / "luts" / edl["aesthetic"]["lut"]
    grade_stage.apply_lut(rendered, lut_path, out)


@click.group(cls=DefaultGroup, default_command="pipeline")
def cli() -> None:
    pass


@cli.command("pipeline")
@click.argument("footage_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--bgm", "bgm_name", required=True, help="BGM track name under assets/bgm/ (no extension)")
@click.option("--duration", "target_duration_s", type=float, default=15.0, help="target duration in seconds")
@click.option("--work-root", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--force", is_flag=True, default=False, help="ignore existing outputs and rerun every stage run")
@click.option(
    "--from", "from_stage",
    type=click.Choice(STAGES),
    default=STAGES[0],
    help="restart from this stage, treating earlier stages' outputs as already on disk",
)
def pipeline(
    footage_dir: Path,
    bgm_name: str,
    target_duration_s: float,
    work_root: Path,
    force: bool,
    from_stage: str,
) -> None:
    """Run split -> filter -> select -> render -> grade for footage_dir."""
    project = footage_dir.name
    project_dir = work_root / project
    bgm_track = _resolve_bgm(bgm_name)

    stages_to_run = STAGES[STAGES.index(from_stage):]
    click.echo(f"project: {project}")
    click.echo(f"stages: {', '.join(stages_to_run)}")

    runners = {
        "split": lambda: _stage_split(project_dir, footage_dir, work_root, force),
        "filter": lambda: _stage_filter(project_dir, project, work_root, force),
        "select": lambda: _stage_select(project_dir, project, work_root, bgm_track, target_duration_s, force),
        "render": lambda: _stage_render(project_dir, force),
        "grade": lambda: _stage_grade(project_dir, force),
    }

    total_start = time.monotonic()
    for stage in stages_to_run:
        click.echo(f"\n=== {stage} ===")
        stage_start = time.monotonic()
        try:
            runners[stage]()
        except Exception as exc:
            elapsed = time.monotonic() - stage_start
            click.echo(f"\nFAILED at stage '{stage}' after {elapsed:.1f}s: {exc}", err=True)
            sys.exit(1)
        click.echo(f"  done in {time.monotonic() - stage_start:.1f}s")

    click.echo(f"\ntotal: {time.monotonic() - total_start:.1f}s")
    click.echo(f"wrote {project_dir / 'final.mp4'}")


def _open_image(path: Path) -> None:
    try:
        import os

        os.startfile(path)  # noqa: S606 — local CLI tool, opening the OS's own default viewer
    except Exception as exc:
        click.echo(f"  (couldn't open {path} automatically: {exc})")


def _load_project_edl(project_dir: Path) -> EDL:
    for name in ("edl_baseline.json", "edl_ai.json"):
        path = project_dir / name
        if path.exists():
            return EDL.model_validate_json(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"no edl_baseline.json or edl_ai.json found in {project_dir}")


def _record_example(project: str, segment_id: str, frame_abs: Path, decision: str, reason: str) -> None:
    taste.record_example(project, segment_id, frame_abs, decision, reason)


@cli.command("review")
@click.argument("project_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
def review(project_dir: Path) -> None:
    """Walk a finished project's selected + dropped candidates one at a
    time, recording keep/drop + reason into taste/examples.jsonl.
    """
    candidates_file = CandidatesFile.model_validate_json(
        (project_dir / "candidates.json").read_text(encoding="utf-8")
    )
    edl = _load_project_edl(project_dir)
    project = candidates_file.project

    selected_ids = [c.segment_id for c in edl.timeline]
    by_id = {c.segment_id: c for c in candidates_file.candidates}
    selected = [by_id[sid] for sid in selected_ids if sid in by_id]
    dropped = [c for c in candidates_file.candidates if c.segment_id not in selected_ids]

    click.echo(f"project: {project}  |  selected: {len(selected)}  dropped: {len(dropped)}")
    recorded = 0
    for label, group in (("SELECTED", selected), ("DROPPED", dropped)):
        for candidate in group:
            frame_abs = project_dir / candidate.frame_path
            click.echo(f"\n[{label}] {candidate.segment_id}")
            click.echo(f"  frame: {frame_abs}")
            _open_image(frame_abs)

            decision = click.prompt(
                "  keep/drop/skip", type=click.Choice(["keep", "drop", "skip"]), default="skip"
            )
            if decision == "skip":
                continue
            reason = click.prompt("  reason", default="", show_default=False)
            _record_example(project, candidate.segment_id, frame_abs, decision, reason)
            recorded += 1
            click.echo(f"  recorded -> taste/examples.jsonl")

    click.echo(f"\nrecorded {recorded} judgment(s) -> {taste.EXAMPLES_PATH}")


@cli.command("trends")
def trends() -> None:
    """Fetch latest YouTube vlog trends and update taste profile."""
    click.echo("Running youtube trends analysis...")
    from pipeline import youtube_trends
    youtube_trends.main()


@cli.command("inspect-edl")
@click.argument("edl_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--json", "as_json", is_flag=True, default=False, help="emit JSON report")
def inspect_edl_cmd(edl_path: Path, as_json: bool) -> None:
    """Print source/segment repetition, captions, style, and generator info."""
    from pipeline.inspect_edl import format_report, inspect_edl

    report = inspect_edl(edl_path)
    if as_json:
        click.echo(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        click.echo(format_report(report))


if __name__ == "__main__":
    cli()
