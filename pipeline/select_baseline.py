"""Heuristic (no-AI) segment selection — the baseline / fallback EDL builder.

Reads:  work/<project>/segments_filtered.json
        assets/bgm/<track>.beats.json
Writes: work/<project>/edl.json  (EDL, created_by="baseline"; see edl.py)

Picks kept segments by quality score to fill target_duration_s and snaps
every in/out point to the nearest beat in the beat grid. No AI call.
"""

from __future__ import annotations

from pathlib import Path

import click


def select_baseline(
    work_dir: Path,
    project: str,
    bgm_track: Path,
    target_duration_s: float,
) -> Path:
    """Build a baseline EDL from segments_filtered.json + the beat grid and
    write it to work/<project>/edl.json.
    """
    raise NotImplementedError


@click.command()
@click.option("--work-dir", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--project", required=True)
@click.option("--bgm-track", type=click.Path(path_type=Path), required=True)
@click.option("--target-duration-s", type=float, default=30.0)
def main(work_dir: Path, project: str, bgm_track: Path, target_duration_s: float) -> None:
    out = select_baseline(work_dir, project, bgm_track, target_duration_s)
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
