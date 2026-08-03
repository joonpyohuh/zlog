"""Contact sheet generation — the only thing the AI selector is allowed to see.

Reads:  work/<project>/segments_filtered.json
        work/<project>/frames/<segment_id>.jpg
Writes: work/<project>/contact_sheets/sheet_*.jpg
        work/<project>/contact_sheet_manifest.json
        (maps each segment_id -> {sheet file, grid position/bbox})

select_ai.py must only ever be given these contact sheet images plus
segment_ids — never raw video files, never a chance to invent a timecode.
"""

from __future__ import annotations

from pathlib import Path

import click


def build_contact_sheets(work_dir: Path, project: str) -> Path:
    """Lay out kept-segment frames (from segments_filtered.json) into grid
    images with segment_id labels, and write the manifest that maps each
    segment_id back to its sheet + position.
    """
    raise NotImplementedError


@click.command()
@click.option("--work-dir", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--project", required=True)
def main(work_dir: Path, project: str) -> None:
    out = build_contact_sheets(work_dir, project)
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
