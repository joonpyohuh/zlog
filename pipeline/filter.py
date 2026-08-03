"""Quality filter — drop blurry / over- or under-exposed / duplicate segments.

Reads:  work/<project>/segments.json
        work/<project>/frames/<segment_id>.jpg
Writes: work/<project>/segments_filtered.json  (list[FilteredScene], see edl.py)

This stage only scores and flags segments (keep / discard_reason). It
never changes a segment's timecodes and never adds new candidate segments.
"""

from __future__ import annotations

from pathlib import Path

import click


def filter_scenes(work_dir: Path, project: str) -> Path:
    """Score segments.json entries (sharpness via OpenCV, exposure, near-
    duplicate detection via imagehash) and write segments_filtered.json.
    """
    raise NotImplementedError


@click.command()
@click.option("--work-dir", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--project", required=True)
def main(work_dir: Path, project: str) -> None:
    out = filter_scenes(work_dir, project)
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
