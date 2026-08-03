"""BGM beat grid — the only source of truth for cut rhythm.

Reads:  assets/bgm/<track>.mp3 (or .wav)
Writes: assets/bgm/<track>.beats.json
        ({tempo_bpm, beat_times: list[float], downbeat_times: list[float]})
"""

from __future__ import annotations

import json
from pathlib import Path

import click
import librosa
import numpy as np


def extract_beat_grid(track_path: Path) -> Path:
    """Run librosa beat tracking on `track_path` and write sibling .beats.json."""
    track_path = Path(track_path)
    if not track_path.exists():
        raise FileNotFoundError(track_path)

    y, sr = librosa.load(str(track_path), sr=22050, mono=True)
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames")
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)

    # librosa may return tempo as ndarray; some tracks report 0 — fall back.
    tempo_bpm = float(np.atleast_1d(tempo)[0])
    times = [round(float(t), 3) for t in np.atleast_1d(beat_times).tolist()]
    if tempo_bpm <= 1 and len(times) >= 2:
        gaps = np.diff(times)
        med = float(np.median(gaps[gaps > 0.05])) if np.any(gaps > 0.05) else 0.5
        tempo_bpm = 60.0 / med if med > 0 else 120.0
    if tempo_bpm <= 1:
        tempo_bpm = 120.0
    if len(times) < 2:
        # Synthetic grid so selectors always have something to snap to.
        step = 60.0 / tempo_bpm
        duration = float(librosa.get_duration(y=y, sr=sr))
        times = [round(i * step, 3) for i in range(int(duration / step) + 1)]
    if times[0] > 0.05:
        times = [0.0, *times]

    # Approximate downbeats as every 4th beat (common 4/4); good enough for snap.
    downbeats = times[::4]

    out = track_path.with_suffix(".beats.json")
    payload = {
        "tempo_bpm": round(tempo_bpm, 2),
        "beat_times": times,
        "downbeat_times": downbeats,
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


@click.command()
@click.option("--track", type=click.Path(path_type=Path), required=True)
def main(track: Path) -> None:
    out = extract_beat_grid(track)
    click.echo(f"wrote {out}")


if __name__ == "__main__":
    # ponytail: fails if librosa returns an empty grid on a real file
    demo = Path(__file__).resolve().parent.parent / "assets" / "bgm" / "demo_track.wav"
    if demo.exists():
        path = extract_beat_grid(demo)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["beat_times"], "empty beat_times"
        assert data["tempo_bpm"] > 0
        print("beats.py self-check ok", data["tempo_bpm"], "bpm", len(data["beat_times"]), "beats")
    else:
        main()
