"""Deterministic beat-aware audio engine (PROMPT 6).

Never sends source audio (or BGM) to Claude/GPT — analysis is local only
(librosa/numpy + FFmpeg filters).

Reads:  assets/bgm/<track>.{wav,mp3} (+ optional .beats.json)
        work/<project>/edl_ai.json | edl_baseline.json
        footage/<project>/* (clip source files for natural audio)
Writes: work/<project>/music_analysis.json
        work/<project>/source_audio_analysis.json
        work/<project>/ducking_envelope.json
        work/<project>/ducking_graph.png
        work/<project>/audio_mix.wav
        work/<project>/audio_mixed.mp4  (optional mux onto render/final video)

BGM gain uses attack/release curves (not instant jumps), applied through
FFmpeg asendcmd+volume, then amix + loudnorm + alimiter + end afade.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal

import click
import librosa
import numpy as np
from PIL import Image, ImageDraw

from pipeline.beats import extract_beat_grid
from pipeline.edl import EDL, load_edl

SR = 22050
HOP = 512
MUSIC_BASE_VOLUME = 0.78
# Ducking target gains (multiply BGM)
GAIN_SPEECH = 0.18
GAIN_NATURE = 0.42
GAIN_SFX = 0.62
GAIN_NORMAL = 1.0
ATTACK_SPEECH_S = 0.04
RELEASE_SPEECH_S = 0.35
ATTACK_NATURE_S = 0.06
RELEASE_NATURE_S = 0.45
ATTACK_SFX_S = 0.01
RELEASE_SFX_S = 0.12
FADE_OUT_S = 2.0
ENVELOPE_DT = 0.03  # seconds between volume keyframes for asendcmd


EventKind = Literal["speech", "nature", "sfx", "noise"]
CALLBACK_REUSE_REASONS = frozenset(
    {"opening_callback", "visual_motif_callback", "narrative_payoff"}
)


def should_preserve_source_audio(clip: Any) -> bool:
    """Callbacks reuse the image, not the original audio beat."""
    return clip.reuse_reason not in CALLBACK_REUSE_REASONS


def _run_ffmpeg(args: list[str]) -> None:
    cmd = ["ffmpeg", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed:\n" + (proc.stderr or proc.stdout or "")[-2000:]
        )


def _has_audio_stream(path: Path) -> bool:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0 and "audio" in (proc.stdout or "")


def analyze_music(track_path: Path) -> dict[str, Any]:
    """BPM, beats, downbeats, onset strength, sectional energy, duration."""
    track_path = Path(track_path)
    if not track_path.exists():
        raise FileNotFoundError(track_path)

    # Reuse / refresh beat grid artifact for the rest of the pipeline.
    beats_path = extract_beat_grid(track_path)
    beats = json.loads(beats_path.read_text(encoding="utf-8"))

    y, sr = librosa.load(str(track_path), sr=SR, mono=True)
    duration = float(librosa.get_duration(y=y, sr=sr))
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    onset_times = librosa.frames_to_time(
        np.arange(len(onset_env)), sr=sr, hop_length=HOP
    )
    # Normalize onset curve 0..1 for JSON
    onset_peak = float(np.max(onset_env)) if len(onset_env) else 1.0
    onset_norm = (onset_env / onset_peak).astype(np.float64) if onset_peak > 0 else onset_env

    # Sectional energy: 8 equal windows over the track
    n_sec = 8
    rms = librosa.feature.rms(y=y, hop_length=HOP)[0]
    rms_times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=HOP)
    energy_curve: list[dict[str, float]] = []
    for i in range(n_sec):
        t0 = duration * i / n_sec
        t1 = duration * (i + 1) / n_sec
        mask = (rms_times >= t0) & (rms_times < t1)
        val = float(np.mean(rms[mask])) if np.any(mask) else 0.0
        energy_curve.append(
            {
                "start_sec": round(t0, 3),
                "end_sec": round(t1, 3),
                "rms": round(val, 6),
            }
        )

    # Downsample onset series for JSON size (~5 Hz)
    step = max(1, round(0.2 * sr / HOP))
    onset_series = [
        {"t": round(float(onset_times[i]), 3), "v": round(float(onset_norm[i]), 4)}
        for i in range(0, len(onset_norm), step)
    ]

    return {
        "track": track_path.name,
        "duration_sec": round(duration, 3),
        "tempo_bpm": beats["tempo_bpm"],
        "beat_times": beats["beat_times"],
        "downbeat_times": beats["downbeat_times"],
        "onset_strength_series": onset_series,
        "onset_strength_mean": round(float(np.mean(onset_norm)) if len(onset_norm) else 0.0, 4),
        "energy_curve": energy_curve,
        "sample_rate": sr,
        "beats_json": str(beats_path.as_posix()),
    }


def _noise_floor(rms: np.ndarray) -> float:
    if rms.size == 0:
        return 1e-6
    return float(max(1e-6, np.percentile(rms, 15)))


def _classify_windows(
    y: np.ndarray,
    sr: int,
    *,
    hop: int = HOP,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Energy/spectral heuristics → speech / nature / sfx / silence windows."""
    if y.size == 0:
        return [], {
            "has_source_audio": False,
            "noise_floor": 0.0,
            "rms_envelope": [],
            "silence_windows": [],
            "transient_events": [],
            "nature_candidates": [],
            "speech_windows": [],
        }

    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    floor = _noise_floor(rms)
    speech_thr = max(floor * 8.0, 0.02)
    nature_thr = max(floor * 4.0, 0.012)
    silence_thr = max(floor * 2.0, 0.008)

    # Spectral features for speech-ish vs broadband noise
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop)[0]
    zcr = librosa.feature.zero_crossing_rate(y, hop_length=hop)[0]
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr, hop_length=hop, units="frames"
    )

    # Frame labels
    labels: list[EventKind | Literal["silence"]] = []
    for i, r in enumerate(rms):
        c = float(centroid[i]) if i < len(centroid) else 0.0
        z = float(zcr[i]) if i < len(zcr) else 0.0
        if r < silence_thr:
            labels.append("silence")
        elif r >= speech_thr and 800 <= c <= 4500 and z < 0.25:
            labels.append("speech")
        elif r >= nature_thr and (c < 800 or (800 <= c <= 6000 and z < 0.35)):
            # Sustained non-speech energy → nature/ambience candidate
            labels.append("nature")
        elif r >= nature_thr:
            labels.append("noise")
        else:
            labels.append("silence")

    def _merge(kind: EventKind | Literal["silence"]) -> list[dict[str, Any]]:
        windows: list[dict[str, Any]] = []
        start: int | None = None
        for i, lab in enumerate(labels):
            if lab == kind and start is None:
                start = i
            elif lab != kind and start is not None:
                t0 = float(times[start])
                t1 = float(times[i - 1]) + (hop / sr)
                if t1 - t0 >= 0.08:
                    windows.append(
                        {
                            "start_sec": round(t0, 3),
                            "end_sec": round(t1, 3),
                            "kind": kind,
                            "mean_rms": round(float(np.mean(rms[start:i])), 6),
                        }
                    )
                start = None
        if start is not None:
            t0 = float(times[start])
            t1 = float(times[-1]) + (hop / sr)
            if t1 - t0 >= 0.08:
                windows.append(
                    {
                        "start_sec": round(t0, 3),
                        "end_sec": round(t1, 3),
                        "kind": kind,
                        "mean_rms": round(float(np.mean(rms[start:])), 6),
                    }
                )
        return windows

    speech_windows = _merge("speech")
    # Nature: keep longer windows only (important natural sound)
    nature_windows = [w for w in _merge("nature") if w["end_sec"] - w["start_sec"] >= 0.25]
    silence_windows = _merge("silence")

    transient_events: list[dict[str, Any]] = []
    for fr in onset_frames:
        i = int(fr)
        if i >= len(rms):
            continue
        t = float(times[i]) if i < len(times) else 0.0
        # Skip if already inside speech
        if any(w["start_sec"] <= t <= w["end_sec"] for w in speech_windows):
            continue
        strength = float(onset_env[i]) if i < len(onset_env) else 0.0
        if strength < float(np.percentile(onset_env, 75)) if len(onset_env) else 0:
            continue
        transient_events.append(
            {
                "time_sec": round(t, 3),
                "rms": round(float(rms[i]), 6),
                "onset_strength": round(strength, 4),
                "kind": "sfx",
            }
        )

    # Downsample RMS envelope
    step = max(1, round(0.05 * sr / hop))
    rms_envelope = [
        {"t": round(float(times[i]), 3), "v": round(float(rms[i]), 6)}
        for i in range(0, len(rms), step)
    ]

    summary = {
        "has_source_audio": bool(np.max(np.abs(y)) > 1e-4),
        "noise_floor": round(floor, 8),
        "duration_sec": round(float(len(y) / sr), 3),
        "rms_envelope": rms_envelope,
        "silence_windows": silence_windows,
        "speech_windows": speech_windows,
        "nature_candidates": nature_windows,
        "transient_events": transient_events,
        "mean_rms": round(float(np.mean(rms)), 6),
        "peak_rms": round(float(np.max(rms)), 6),
    }
    events: list[dict[str, Any]] = []
    for w in speech_windows:
        events.append({**w, "kind": "speech"})
    for w in nature_windows:
        events.append({**w, "kind": "nature"})
    for tr in transient_events:
        events.append(
            {
                "start_sec": tr["time_sec"],
                "end_sec": round(tr["time_sec"] + 0.12, 3),
                "kind": "sfx",
                "mean_rms": tr["rms"],
            }
        )
    events.sort(key=lambda e: e["start_sec"])
    return events, summary


def extract_timeline_source_audio(
    edl: EDL,
    footage_dir: Path,
    out_wav: Path,
    *,
    sr: int = SR,
) -> tuple[Path | None, float]:
    """Build a mono WAV of source audio laid out on the EDL timeline.

    Returns (wav_path or None if no audio, timeline_duration_sec).
    """
    timeline_dur = sum(c.out_sec - c.in_sec for c in edl.timeline)
    if timeline_dur <= 0:
        return None, 0.0

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    t_cursor = 0.0
    with tempfile.TemporaryDirectory(prefix="zlog_aud_") as tmp:
        tmp_path = Path(tmp)
        for i, clip in enumerate(edl.timeline):
            src = footage_dir / clip.source_file
            dur = clip.out_sec - clip.in_sec
            part = tmp_path / f"part_{i:03d}.wav"
            if (
                not should_preserve_source_audio(clip)
                or not src.exists()
                or not _has_audio_stream(src)
            ):
                # silence for this cut
                _run_ffmpeg(
                    [
                        "-f",
                        "lavfi",
                        "-i",
                        f"anullsrc=r={sr}:cl=mono",
                        "-t",
                        f"{dur:.4f}",
                        str(part),
                    ]
                )
            else:
                _run_ffmpeg(
                    [
                        "-ss",
                        f"{clip.in_sec:.4f}",
                        "-i",
                        str(src),
                        "-t",
                        f"{dur:.4f}",
                        "-ac",
                        "1",
                        "-ar",
                        str(sr),
                        "-vn",
                        str(part),
                    ]
                )
            parts.append(part)
            t_cursor += dur

        if not parts:
            return None, timeline_dur

        concat_list = tmp_path / "concat.txt"
        concat_list.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in parts),
            encoding="utf-8",
        )
        _run_ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-ac",
                "1",
                "-ar",
                str(sr),
                str(out_wav),
            ]
        )

    # Detect all-silence
    y, _ = librosa.load(str(out_wav), sr=sr, mono=True)
    if float(np.max(np.abs(y))) < 1e-4:
        return None, timeline_dur
    return out_wav, timeline_dur


def analyze_source_audio(
    edl: EDL,
    footage_dir: Path,
    work_dir: Path,
) -> dict[str, Any]:
    """Analyze natural audio on the edit timeline (never sent to an LLM)."""
    wav_path = work_dir / "source_timeline_audio.wav"
    extracted, timeline_dur = extract_timeline_source_audio(edl, footage_dir, wav_path)
    if extracted is None:
        return {
            "has_source_audio": False,
            "timeline_duration_sec": round(timeline_dur, 3),
            "noise_floor": 0.0,
            "rms_envelope": [],
            "silence_windows": [{"start_sec": 0.0, "end_sec": round(timeline_dur, 3), "kind": "silence"}],
            "speech_windows": [],
            "nature_candidates": [],
            "transient_events": [],
            "events": [],
            "mean_rms": 0.0,
            "peak_rms": 0.0,
        }

    y, sr = librosa.load(str(extracted), sr=SR, mono=True)
    events, summary = _classify_windows(y, int(sr))
    summary["timeline_duration_sec"] = round(timeline_dur, 3)
    summary["events"] = events
    summary["source_wav"] = str(extracted.as_posix())
    return summary


def _apply_segment_gain(
    gain: np.ndarray,
    dt: float,
    start: float,
    end: float,
    target: float,
    attack: float,
    release: float,
) -> None:
    """Lower gain toward target over [start,end] with attack/release ramps."""
    n = len(gain)
    duration = max(0.0, end - start)
    if duration <= 0:
        return
    for i in range(n):
        t = i * dt
        if t < start - attack or t > end + release:
            continue
        if start <= t <= end:
            # inside: approach target with attack from left edge
            if t < start + attack and attack > 0:
                u = (t - start) / attack
                desired = GAIN_NORMAL + (target - GAIN_NORMAL) * min(1.0, max(0.0, u))
            else:
                desired = target
        elif t < start:
            # pre-attack
            u = 1.0 - (start - t) / attack if attack > 0 else 1.0
            desired = GAIN_NORMAL + (target - GAIN_NORMAL) * min(1.0, max(0.0, u))
        else:
            # release after end
            u = (t - end) / release if release > 0 else 1.0
            desired = target + (GAIN_NORMAL - target) * min(1.0, max(0.0, u))
        gain[i] = min(gain[i], desired)


def build_ducking_envelope(
    source_analysis: dict[str, Any],
    *,
    duration_sec: float,
    base_volume: float = MUSIC_BASE_VOLUME,
    dt: float = ENVELOPE_DT,
) -> dict[str, Any]:
    """Piecewise BGM gain curve from classified source events + attack/release."""
    n = max(1, math.ceil(duration_sec / dt) + 1)
    gain = np.ones(n, dtype=np.float64)  # relative to base_volume

    if not source_analysis.get("has_source_audio"):
        times = [round(i * dt, 4) for i in range(n)]
        # End fade-out baked into envelope
        for i, t in enumerate(times):
            if t >= max(0.0, duration_sec - FADE_OUT_S):
                u = (duration_sec - t) / FADE_OUT_S if FADE_OUT_S > 0 else 0.0
                gain[i] *= max(0.0, min(1.0, u))
        keyframes = [
            {"t": times[i], "gain": round(float(gain[i]), 4), "volume": round(float(gain[i] * base_volume), 4)}
            for i in range(n)
        ]
        return {
            "dt_sec": dt,
            "base_volume": base_volume,
            "duration_sec": round(duration_sec, 3),
            "keyframes": keyframes,
            "events_applied": [],
        }

    events_applied: list[dict[str, Any]] = []
    for ev in source_analysis.get("events") or []:
        kind = ev.get("kind")
        if kind == "speech":
            target, attack, release = GAIN_SPEECH, ATTACK_SPEECH_S, RELEASE_SPEECH_S
        elif kind == "nature":
            target, attack, release = GAIN_NATURE, ATTACK_NATURE_S, RELEASE_NATURE_S
        elif kind == "sfx":
            target, attack, release = GAIN_SFX, ATTACK_SFX_S, RELEASE_SFX_S
        else:
            continue  # ignore bare noise
        _apply_segment_gain(
            gain,
            dt,
            float(ev["start_sec"]),
            float(ev["end_sec"]),
            target,
            attack,
            release,
        )
        events_applied.append(
            {
                "kind": kind,
                "start_sec": ev["start_sec"],
                "end_sec": ev["end_sec"],
                "target_gain": target,
                "attack_sec": attack,
                "release_sec": release,
            }
        )

    # End fade-out
    fade_start = max(0.0, duration_sec - FADE_OUT_S)
    for i in range(n):
        t = i * dt
        if t >= fade_start:
            u = (duration_sec - t) / FADE_OUT_S if FADE_OUT_S > 0 else 0.0
            gain[i] *= max(0.0, min(1.0, u))

    keyframes = [
        {
            "t": round(i * dt, 4),
            "gain": round(float(gain[i]), 4),
            "volume": round(float(gain[i] * base_volume), 4),
        }
        for i in range(n)
        if i * dt <= duration_sec + 1e-6
    ]
    return {
        "dt_sec": dt,
        "base_volume": base_volume,
        "duration_sec": round(duration_sec, 3),
        "keyframes": keyframes,
        "events_applied": events_applied,
    }


def write_ducking_graph(envelope: dict[str, Any], out_path: Path) -> Path:
    """Simple PNG plot of BGM volume over time (Pillow — no matplotlib)."""
    kfs = envelope.get("keyframes") or []
    w, h = 900, 280
    img = Image.new("RGB", (w, h), (16, 16, 16))
    draw = ImageDraw.Draw(img)
    pad = 40
    draw.rectangle((pad, pad, w - pad, h - pad), outline=(60, 60, 60))
    if len(kfs) >= 2:
        t0 = kfs[0]["t"]
        t1 = kfs[-1]["t"] or 1.0
        vols = [k["volume"] for k in kfs]
        vmin, vmax = 0.0, max(0.01, max(vols))
        pts: list[tuple[float, float]] = []
        for k in kfs:
            x = pad + (k["t"] - t0) / (t1 - t0) * (w - 2 * pad)
            y = h - pad - (k["volume"] - vmin) / (vmax - vmin) * (h - 2 * pad)
            pts.append((x, y))
        draw.line(pts, fill=(120, 200, 255), width=2)
        # Mark ducking events
        for ev in envelope.get("events_applied") or []:
            color = {
                "speech": (255, 120, 120),
                "nature": (120, 255, 160),
                "sfx": (255, 220, 120),
            }.get(ev["kind"], (180, 180, 180))
            x0 = pad + (ev["start_sec"] - t0) / (t1 - t0) * (w - 2 * pad)
            x1 = pad + (ev["end_sec"] - t0) / (t1 - t0) * (w - 2 * pad)
            draw.rectangle((x0, pad, x1, h - pad), outline=color)
    draw.text((pad, 8), "BGM ducking envelope (volume vs time)", fill=(220, 220, 220))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    return out_path


def _write_asendcmd(envelope: dict[str, Any], path: Path) -> Path:
    """FFmpeg asendcmd script driving the volume filter."""
    lines: list[str] = []
    for kf in envelope["keyframes"]:
        # asendcmd: TIMESTAMP target command arg
        lines.append(f"{kf['t']:.4f} volume volume {kf['volume']:.4f};")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def mix_bgm_with_source(
    *,
    bgm_path: Path,
    source_wav: Path | None,
    envelope: dict[str, Any],
    duration_sec: float,
    out_wav: Path,
) -> Path:
    """Duck BGM via FFmpeg asendcmd+volume, mix with source, loudnorm, limit."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="zlog_mix_") as tmp:
        tmp_path = Path(tmp)
        cmd_file = tmp_path / "ducking.cmd"
        _write_asendcmd(envelope, cmd_file)
        ducked = tmp_path / "bgm_ducked.wav"

        # Trim/pad BGM to duration, apply volume automation + safety ceiling
        # asendcmd path must be escaped for filtergraph on Windows.
        cmd_esc = cmd_file.as_posix().replace(":", "\\:")
        _run_ffmpeg(
            [
                "-stream_loop",
                "-1",
                "-i",
                str(bgm_path),
                "-t",
                f"{duration_sec:.4f}",
                "-af",
                f"asendcmd=f='{cmd_esc}',volume,alimiter=limit=0.95",
                "-ac",
                "2",
                "-ar",
                "48000",
                str(ducked),
            ]
        )

        if source_wav is None or not source_wav.exists():
            # loudnorm the ducked BGM alone
            _run_ffmpeg(
                [
                    "-i",
                    str(ducked),
                    "-af",
                    "loudnorm=I=-14:TP=-1.5:LRA=11,alimiter=limit=0.97",
                    "-ar",
                    "48000",
                    str(out_wav),
                ]
            )
            return out_wav

        # Mix: keep natural source relatively present; BGM already ducked
        mixed = tmp_path / "mixed_raw.wav"
        _run_ffmpeg(
            [
                "-i",
                str(ducked),
                "-i",
                str(source_wav),
                "-filter_complex",
                (
                    "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=1.0[src];"
                    "[0:a][src]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[m];"
                    "[m]loudnorm=I=-14:TP=-1.5:LRA=11,alimiter=limit=0.97[out]"
                ),
                "-map",
                "[out]",
                "-ar",
                "48000",
                str(mixed),
            ]
        )
        shutil.copy2(mixed, out_wav)
    return out_wav


def mux_audio_onto_video(video_path: Path, audio_wav: Path, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-i",
            str(video_path),
            "-i",
            str(audio_wav),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            str(out_path),
        ]
    )
    return out_path


def resolve_bgm(edl: EDL, assets_bgm: Path) -> Path:
    stem = edl.audio.bgm_id
    for ext in (".wav", ".mp3", ".m4a"):
        cand = assets_bgm / f"{stem}{ext}"
        if cand.exists():
            return cand
    raise FileNotFoundError(f"BGM not found for bgm_id={stem!r} under {assets_bgm}")


def run_audio_engine(
    work_dir: Path,
    project: str,
    *,
    footage_dir: Path | None = None,
    assets_bgm: Path = Path("assets/bgm"),
    video_path: Path | None = None,
    force: bool = False,
) -> dict[str, Path]:
    """Full analyze → duck → mix pipeline for one project."""
    project_dir = work_dir / project
    edl_path = project_dir / "edl_ai.json"
    if not edl_path.exists():
        edl_path = project_dir / "edl_baseline.json"
    if not edl_path.exists():
        raise FileNotFoundError(f"no EDL in {project_dir}")

    out_music = project_dir / "music_analysis.json"
    out_source = project_dir / "source_audio_analysis.json"
    out_env = project_dir / "ducking_envelope.json"
    out_graph = project_dir / "ducking_graph.png"
    out_mix = project_dir / "audio_mix.wav"

    if out_mix.exists() and out_env.exists() and not force:
        click.echo(f"{out_mix} exists, skipping (use --force)")
        return {
            "music_analysis": out_music,
            "source_audio_analysis": out_source,
            "ducking_envelope": out_env,
            "ducking_graph": out_graph,
            "audio_mix": out_mix,
        }

    edl = load_edl(edl_path)
    if footage_dir is None:
        footage_dir = Path("footage") / project
    bgm = resolve_bgm(edl, assets_bgm)

    music = analyze_music(bgm)
    out_music.write_text(json.dumps(music, indent=2), encoding="utf-8")

    source = analyze_source_audio(edl, footage_dir, project_dir)
    out_source.write_text(json.dumps(source, indent=2), encoding="utf-8")

    duration = float(
        source.get("timeline_duration_sec")
        or sum(c.out_sec - c.in_sec for c in edl.timeline)
    )
    # Prefer rendered video duration when available
    if video_path and video_path.exists():
        try:
            duration = float(librosa.get_duration(path=str(video_path)))
        except (OSError, ValueError):
            pass

    base_vol = float(edl.audio.volume) if edl.audio.volume else MUSIC_BASE_VOLUME
    envelope = build_ducking_envelope(
        source, duration_sec=duration, base_volume=base_vol
    )
    out_env.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    write_ducking_graph(envelope, out_graph)

    source_wav = None
    if source.get("has_source_audio") and source.get("source_wav"):
        source_wav = Path(source["source_wav"])

    mix_bgm_with_source(
        bgm_path=bgm,
        source_wav=source_wav,
        envelope=envelope,
        duration_sec=duration,
        out_wav=out_mix,
    )

    outputs: dict[str, Path] = {
        "music_analysis": out_music,
        "source_audio_analysis": out_source,
        "ducking_envelope": out_env,
        "ducking_graph": out_graph,
        "audio_mix": out_mix,
    }

    # Optional remux onto graded/render video
    for cand in (
        video_path,
        project_dir / "final.mp4",
        project_dir / "render.mp4",
    ):
        if cand and Path(cand).exists():
            mixed_video = project_dir / "audio_mixed.mp4"
            mux_audio_onto_video(Path(cand), out_mix, mixed_video)
            outputs["audio_mixed_video"] = mixed_video
            break

    click.echo(
        f"audio engine: music={music['tempo_bpm']}bpm "
        f"source_audio={source.get('has_source_audio')} "
        f"duck_events={len(envelope.get('events_applied') or [])} → {out_mix}"
    )
    return outputs


@click.command()
@click.option("--work-dir", type=click.Path(path_type=Path), default=Path("work"))
@click.option("--project", required=True)
@click.option("--footage-dir", type=click.Path(path_type=Path), default=None)
@click.option("--video", "video_path", type=click.Path(path_type=Path), default=None)
@click.option("--force", is_flag=True, default=False)
def main(
    work_dir: Path,
    project: str,
    footage_dir: Path | None,
    video_path: Path | None,
    force: bool,
) -> None:
    outs = run_audio_engine(
        work_dir,
        project,
        footage_dir=footage_dir,
        video_path=video_path,
        force=force,
    )
    for k, p in outs.items():
        click.echo(f"{k}: {p}")


if __name__ == "__main__":
    main()
