"""Claude-based segment selection (later — not wired up yet).

Reads:  work/<project>/contact_sheet_manifest.json
        work/<project>/contact_sheets/sheet_*.jpg
        work/<project>/segments_filtered.json
        assets/bgm/<track>.beats.json
Writes: work/<project>/edl.json  (EDL, created_by="ai"; see edl.py)

The model only ever sees contact sheet images and picks segment_ids from
segments_filtered.json — it never receives a video file and never emits a
timecode itself. Output must satisfy the same schema/validation as
select_baseline.py's (edl.validate_edl).
"""

from __future__ import annotations

from pathlib import Path

import click


def select_ai(
    work_dir: Path,
    project: str,
    bgm_track: Path,
    target_duration_s: float,
) -> Path:
    """Ask Claude to pick segment_ids from the contact sheets, snap the
    chosen segments' timecodes to the beat grid, and write edl.json.
    """
    raise NotImplementedError


@click.command()
@click.option("--work-dir", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--project", required=True)
@click.option("--bgm-track", type=click.Path(path_type=Path), required=True)
@click.option("--target-duration-s", type=float, default=30.0)
def main(work_dir: Path, project: str, bgm_track: Path, target_duration_s: float) -> None:
    out = select_ai(work_dir, project, bgm_track, target_duration_s)
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
