"""PROMPT 6 — deterministic audio engine integration tests (no LLM)."""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np

from pipeline.audio_engine import (
    GAIN_NORMAL,
    GAIN_SPEECH,
    _classify_windows,
    analyze_music,
    build_ducking_envelope,
    mix_bgm_with_source,
    run_audio_engine,
    should_preserve_source_audio,
    write_ducking_graph,
)
from pipeline.edl import (
    EDL,
    Aesthetic,
    Audio,
    Canvas,
    Frame,
    Signature,
    TimelineClip,
)

SR = 22050


def test_callback_does_not_repeat_original_audio() -> None:
    normal = TimelineClip(
        order=1,
        segment_id="a",
        source_file="a.mp4",
        in_sec=0,
        out_sec=1,
    )
    callback = normal.model_copy(
        update={"order": 2, "reuse_reason": "opening_callback"}
    )
    assert should_preserve_source_audio(normal) is True
    assert should_preserve_source_audio(callback) is False


def _write_wav(path: Path, y: np.ndarray, sr: int = SR) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    y = np.clip(y, -1.0, 1.0)
    pcm = (y * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _synth_music(duration: float = 4.0, bpm: float = 120.0) -> np.ndarray:
    """Click track + soft pad — clear onsets for beat/onset analysis."""
    n = int(duration * SR)
    t = np.arange(n) / SR
    y = 0.05 * np.sin(2 * np.pi * 220 * t)
    beat = 60.0 / bpm
    for k in range(int(duration / beat) + 1):
        center = int(k * beat * SR)
        if center >= n:
            break
        env = np.exp(-np.arange(int(0.05 * SR)) / (0.015 * SR))
        end = min(n, center + len(env))
        y[center:end] += 0.6 * env[: end - center]
    return y.astype(np.float32)


def _synth_speech_like(duration: float = 3.0) -> np.ndarray:
    """Syllable-modulated mid-band tone → energy VAD speech class."""
    n = int(duration * SR)
    t = np.arange(n) / SR
    carrier = 0.35 * np.sin(2 * np.pi * 1800 * t) + 0.2 * np.sin(2 * np.pi * 1200 * t)
    # ~5 Hz syllable envelope
    syll = 0.5 + 0.5 * np.sin(2 * np.pi * 5.0 * t)
    # Gate middle 1.6s as "talking"
    gate = ((t >= 0.6) & (t <= 2.2)).astype(np.float32)
    noise = 0.01 * np.random.default_rng(0).standard_normal(n)
    return (carrier * syll * gate + noise).astype(np.float32)


def _synth_noise(duration: float = 3.0, amp: float = 0.02) -> np.ndarray:
    rng = np.random.default_rng(1)
    return (amp * rng.standard_normal(int(duration * SR))).astype(np.float32)


def test_analyze_music_fields(tmp_path: Path):
    path = tmp_path / "music.wav"
    _write_wav(path, _synth_music())
    music = analyze_music(path)
    assert music["duration_sec"] >= 3.5
    assert abs(music["tempo_bpm"] - 120.0) < 5.0
    assert len(music["beat_times"]) >= 2
    assert len(music["downbeat_times"]) >= 1
    assert music["onset_strength_series"]
    assert len(music["energy_curve"]) == 8
    assert path.with_suffix(".beats.json").exists()


def test_classify_speech_vs_noise():
    speech = _synth_speech_like()
    noise = _synth_noise()
    ev_s, sum_s = _classify_windows(speech, SR)
    ev_n, _sum_n = _classify_windows(noise, SR)
    assert sum_s["has_source_audio"] is True
    assert sum_s["noise_floor"] >= 0
    speech_events = [e for e in ev_s if e["kind"] == "speech"]
    assert speech_events, "expected speech window on syllabic mid-band tone"
    # Low noise should not produce strong speech windows
    speech_on_noise = [e for e in ev_n if e["kind"] == "speech"]
    assert len(speech_on_noise) <= len(speech_events)


def test_ducking_envelope_dips_on_speech():
    events = [
        {
            "start_sec": 1.0,
            "end_sec": 2.0,
            "kind": "speech",
            "mean_rms": 0.1,
        }
    ]
    source = {
        "has_source_audio": True,
        "events": events,
        "timeline_duration_sec": 4.0,
    }
    env = build_ducking_envelope(source, duration_sec=4.0, base_volume=0.8)
    assert env["keyframes"]
    # Mid-speech keyframe should be near speech gain
    mid = next(k for k in env["keyframes"] if 1.2 <= k["t"] <= 1.6)
    quiet = next(k for k in env["keyframes"] if 0.1 <= k["t"] <= 0.4)
    assert mid["gain"] <= GAIN_SPEECH + 0.05
    assert quiet["gain"] >= GAIN_NORMAL - 0.05
    # Fade-out in last 2s
    tail = env["keyframes"][-1]
    assert tail["gain"] <= 0.05
    assert any(e["kind"] == "speech" for e in env["events_applied"])


def test_ducking_graph_written(tmp_path: Path):
    env = build_ducking_envelope(
        {
            "has_source_audio": True,
            "events": [{"start_sec": 0.5, "end_sec": 1.0, "kind": "nature", "mean_rms": 0.05}],
        },
        duration_sec=3.0,
    )
    out = write_ducking_graph(env, tmp_path / "duck.png")
    assert out.exists() and out.stat().st_size > 500


def test_mix_ffmpeg_loudnorm(tmp_path: Path):
    bgm = tmp_path / "bgm.wav"
    src = tmp_path / "src.wav"
    _write_wav(bgm, _synth_music(3.0))
    _write_wav(src, _synth_speech_like(3.0))
    env = build_ducking_envelope(
        {
            "has_source_audio": True,
            "events": [{"start_sec": 0.6, "end_sec": 2.2, "kind": "speech", "mean_rms": 0.1}],
        },
        duration_sec=3.0,
        base_volume=0.8,
    )
    out = tmp_path / "mix.wav"
    mix_bgm_with_source(
        bgm_path=bgm,
        source_wav=src,
        envelope=env,
        duration_sec=3.0,
        out_wav=out,
    )
    assert out.exists() and out.stat().st_size > 1000


def test_run_audio_engine_end_to_end(tmp_path: Path):
    """Synthetic speech + music + noise under a tiny EDL project."""
    project = "aud_demo"
    footage = tmp_path / "footage" / project
    work = tmp_path / "work"
    project_dir = work / project
    assets = tmp_path / "assets" / "bgm"
    footage.mkdir(parents=True)
    project_dir.mkdir(parents=True)
    assets.mkdir(parents=True)

    music_path = assets / "demo_mix.wav"
    _write_wav(music_path, _synth_music(5.0))

    # Source clip: speech then noise
    speech = _synth_speech_like(2.5)
    noise = _synth_noise(2.5, amp=0.015)
    clip_audio = np.concatenate([speech, noise])
    # Make a tiny mp4 with audio via ffmpeg
    raw_wav = footage / "clip_a.wav"
    _write_wav(raw_wav, clip_audio)
    clip_mp4 = footage / "clip_a.mp4"
    import subprocess

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:d=5",
            "-i",
            str(raw_wav),
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(clip_mp4),
        ],
        check=True,
        capture_output=True,
    )

    edl = EDL(
        project=project,
        version="1.0",
        generator="ai",
        canvas=Canvas(width=320, height=240),
        frame=Frame(aspect="4:3", width=320, height=240, y_offset=0),
        aesthetic=Aesthetic(lut="ccd_cool_01.cube", grain=0.0, bloom=0.0),
        audio=Audio(bgm_id="demo_mix", start_sec=0.0, volume=0.8),
        timeline=[
            TimelineClip(
                order=1,
                segment_id="clip_a#s001",
                source_file="clip_a.mp4",
                in_sec=0.0,
                out_sec=5.0,
            )
        ],
        signature=Signature(enabled=False, text="", duration=0.0),
        style_preset="clean_vlog",
    )
    (project_dir / "edl_ai.json").write_text(edl.model_dump_json(indent=2), encoding="utf-8")

    outs = run_audio_engine(
        work,
        project,
        footage_dir=footage,
        assets_bgm=assets,
        force=True,
    )
    assert outs["music_analysis"].exists()
    assert outs["source_audio_analysis"].exists()
    assert outs["ducking_envelope"].exists()
    assert outs["ducking_graph"].exists()
    assert outs["audio_mix"].exists()

    source = json.loads(outs["source_audio_analysis"].read_text(encoding="utf-8"))
    env = json.loads(outs["ducking_envelope"].read_text(encoding="utf-8"))
    music = json.loads(outs["music_analysis"].read_text(encoding="utf-8"))

    assert music["beat_times"]
    assert source["has_source_audio"] is True
    # Speech portion should create at least one ducking event
    kinds = {e["kind"] for e in env.get("events_applied") or []}
    assert "speech" in kinds or "nature" in kinds or "sfx" in kinds
    # Envelope should not be flat at 1.0 through the talk region
    gains = [k["gain"] for k in env["keyframes"] if 0.8 <= k["t"] <= 2.0]
    assert gains and min(gains) < 0.9
