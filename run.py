"""zlog orchestrator CLI.

Chains the pipeline stages together, but every stage is also runnable on
its own via `python -m pipeline.<stage>` (see CLAUDE.md for each stage's
I/O contract). This file adds no logic of its own beyond wiring calls in
order — all reads/writes still go through work/<project>/.
"""

from __future__ import annotations

from pathlib import Path

import click

from pipeline import beats, filter as filter_stage, select_ai, select_baseline, sheet, split


@click.group()
def cli() -> None:
    pass


@cli.command()
@click.argument("footage_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--work-root", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--bgm-track", type=click.Path(path_type=Path), required=True)
@click.option("--target-duration-s", type=float, default=30.0)
@click.option("--use-ai/--baseline", default=False, help="select_ai.py vs select_baseline.py")
@click.option("--force", is_flag=True, default=False, help="overwrite an existing segments.json")
def full(
    footage_dir: Path,
    work_root: Path,
    bgm_track: Path,
    target_duration_s: float,
    use_ai: bool,
    force: bool,
) -> None:
    """Run split -> filter -> sheet -> beats -> select -> (render, later)."""
    project = footage_dir.name
    split.run_split(footage_dir, work_root, force=force)
    filter_stage.filter_scenes(work_root, project)
    sheet.build_contact_sheets(work_root, project)
    beats.extract_beat_grid(bgm_track)
    selector = select_ai.select_ai if use_ai else select_baseline.select_baseline
    selector(work_root, project, bgm_track, target_duration_s)
    # TODO: render/ (Remotion) stage — not wired up yet.


if __name__ == "__main__":
    cli()
