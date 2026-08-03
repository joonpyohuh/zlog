"""BGM beat grid — the only source of truth for cut rhythm.

Reads:  assets/bgm/<track>.mp3 (or .wav)
Writes: assets/bgm/<track>.beats.json
        ({tempo_bpm, beat_times: list[float], downbeat_times: list[float]})

Beat times come from librosa onset/beat tracking only. select_baseline.py
and select_ai.py snap cut points to this grid; neither is allowed to
choose its own rhythm.
"""

from __future__ import annotations

from pathlib import Path

import click


def extract_beat_grid(track_path: Path) -> Path:
    """Run librosa beat tracking on `track_path` and write the sibling
    <track>.beats.json file. Returns the output path.
    """
    raise NotImplementedError


@click.command()
@click.option("--track", type=click.Path(path_type=Path), required=True)
def main(track: Path) -> None:
    out = extract_beat_grid(track)
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    main()
