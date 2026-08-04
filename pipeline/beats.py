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

HOP_LENGTH = 512


def _estimate_beat_grid(y: np.ndarray, sr: int) -> tuple[float, list[float]]:
    """Estimate a stable 60-180 BPM grid without librosa's numba beat module."""
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP_LENGTH)
    duration = float(librosa.get_duration(y=y, sr=sr))
    if len(onset) < 2 or float(np.max(onset)) <= 1e-8:
        tempo = 120.0
        step = 60.0 / tempo
        return tempo, [round(t, 3) for t in np.arange(0.0, duration + step / 2, step)]

    centered = onset.astype(np.float64) - float(np.mean(onset))
    size = 1 << (2 * len(centered) - 1).bit_length()
    spectrum = np.fft.rfft(centered, n=size)
    correlation = np.fft.irfft(spectrum * np.conj(spectrum), n=size)[: len(centered)]
    min_lag = max(1, round(sr * 60 / (HOP_LENGTH * 180)))
    max_lag = min(len(correlation) - 1, round(sr * 60 / (HOP_LENGTH * 60)))
    if max_lag <= min_lag:
        tempo = 120.0
        step = 60.0 / tempo
        return tempo, [round(t, 3) for t in np.arange(0.0, duration + step / 2, step)]

    lag = min_lag + int(np.argmax(correlation[min_lag : max_lag + 1]))
    tempo = 60.0 * sr / (HOP_LENGTH * lag)
    # ponytail: prefer double-time below 90 BPM for short edits; add meter
    # inference if Zlog later needs musically exact half-time distinction.
    if tempo < 90 and round(lag / 2) >= min_lag:
        lag = round(lag / 2)
        tempo = 60.0 * sr / (HOP_LENGTH * lag)
    phase = max(
        range(lag),
        key=lambda offset: float(np.sum(onset[offset::lag])),
    )
    frames = np.arange(phase, len(onset), lag)
    times = [
        round(float(t), 3)
        for t in librosa.frames_to_time(frames, sr=sr, hop_length=HOP_LENGTH)
        if t <= duration
    ]
    if not times or times[0] > 0.05:
        times.insert(0, 0.0)
    return tempo, times


def extract_beat_grid(track_path: Path) -> Path:
    """Estimate beats for `track_path` and write sibling .beats.json."""
    track_path = Path(track_path)
    if not track_path.exists():
        raise FileNotFoundError(track_path)

    y, sr = librosa.load(str(track_path), sr=22050, mono=True)
    tempo_bpm, times = _estimate_beat_grid(y, int(sr))

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
