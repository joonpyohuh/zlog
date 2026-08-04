"""Local API for the zlog web UI / Next.js Studio.

    uv run uvicorn server:app --host 127.0.0.1 --port 8000

Accepts photo/video/text, stages footage/<job_id>/, runs the hybrid product
pipeline (analyze → director → plan → evaluate → Remotion → audio → final).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent
FOOTAGE_ROOT = REPO_ROOT / "footage"
WORK_ROOT = REPO_ROOT / "work"
JOBS_ROOT = REPO_ROOT / ".zlog_jobs"
WEB_DIST = REPO_ROOT / "web" / "dist"
TASTE_PATH = REPO_ROOT / "taste" / "taste_profile.json"
DEFAULT_BGM = "demo_track"
# Web demo target — Remotion at 1080x1920 is slow locally; keep shorts short.
DEFAULT_DURATION = 12.0
WEB_DURATION_CAP = 12.0
# Half-res render (~4× fewer pixels) for the local web path; CLI can override.
WEB_RENDER_SCALE = float(os.getenv("ZLOG_WEB_RENDER_SCALE", "0.5"))
WEB_RENDER_CONCURRENCY = int(os.getenv("ZLOG_WEB_RENDER_CONCURRENCY", "2"))

_jobs_lock = threading.Lock()


def _mark_stale_jobs() -> None:
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    for path in JOBS_ROOT.glob("*.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if job.get("status") in ("queued", "running"):
            job["status"] = "error"
            job["error"] = "Interrupted — server restarted. Try again."
            path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # YouTube thumbnail → taste_profile overwrite is disabled (PROMPT 9).
    _mark_stale_jobs()
    yield


app = FastAPI(title="zlog", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS_ROOT.mkdir(parents=True, exist_ok=True)


def _safe_name(name: str) -> str:
    base = Path(name).name
    return re.sub(r"[^\w.\-]+", "_", base) or "file.bin"


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(
        cmd,
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(cmd)}\n{result.stdout}\n{result.stderr}"
        )


def _target_duration(upload_count: int = 0) -> float:
    """Web jobs always stay under WEB_DURATION_CAP — local Remotion cannot
    keep up with 30–40s 1080×1920 targets (looks like infinite loading)."""
    del upload_count  # reserved for future pacing heuristics
    return WEB_DURATION_CAP


def _image_to_clip(image: Path, out: Path, seconds: float) -> None:
    _run(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-t",
            f"{seconds:.2f}",
            "-i",
            str(image),
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,unsharp=5:5:0.8:5:5:0.0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            str(out),
        ]
    )


def _ensure_candidates(project: str) -> None:
    """Promote soft stills that fail blur; keep true dups/exposure rejects out when possible."""
    path = WORK_ROOT / project / "candidates.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("candidates"):
        return
    rejected = data.get("rejected") or []
    if not rejected:
        raise RuntimeError("No usable clips after quality check.")

    stills = [
        item
        for item in rejected
        if str(item.get("source_file", "")).startswith("still_")
        or item.get("quality", {}).get("verdict") == "blurry"
    ]
    promote = stills or rejected
    for item in promote:
        item.setdefault("quality", {})["verdict"] = "pass"
    data["candidates"] = promote
    data["rejected"] = [r for r in rejected if r not in promote]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _job_path(job_id: str) -> Path:
    return JOBS_ROOT / f"{job_id}.json"


def _read_job(job_id: str) -> dict | None:
    path = _job_path(job_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _set_job(job_id: str, **fields) -> None:
    with _jobs_lock:
        path = _job_path(job_id)
        job = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"id": job_id}
        job.update(fields)
        # Cap oversized Remotion dumps so the status JSON stays valid/readable.
        if isinstance(job.get("detail"), str) and len(job["detail"]) > 2500:
            job["detail"] = job["detail"][-2500:]
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def _resolve_bgm() -> Path:
    for ext in ("wav", "mp3"):
        candidate = REPO_ROOT / "assets" / "bgm" / f"{DEFAULT_BGM}.{ext}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"BGM '{DEFAULT_BGM}' not found")


def _friendly_error(exc: Exception) -> str:
    msg = str(exc)
    low = msg.lower()
    if "ANTHROPIC_API_KEY" in msg or "api_key" in low:
        return "AI key missing or invalid. Check .env."
    if "no usable" in low or "no passing" in low or "no usable clips" in low:
        return "Could not build a film from that upload. Try clearer photos or a short clip."
    if "bgm" in low and "not found" in low:
        return "Music track missing. Add assets/bgm/demo_track."
    if "remotion" in low or "npx" in low:
        # Surface a short actionable line from Remotion's stderr if present.
        for marker in ("Error:", "Error ", "✗", "Failed to"):
            idx = msg.rfind(marker)
            if idx >= 0:
                snippet = msg[idx : idx + 180].splitlines()[0].strip()
                return f"Render failed: {snippet}"
        return "Render failed on this machine. Retry once — photo films can take a few minutes."
    if "validation" in low:
        return "Edit list failed checks; falling back did not recover. Try again with a short video clip."
    if len(msg) > 220:
        return msg[:220] + "…"
    return msg


def _process_job(
    job_id: str,
    project: str,
    note: str,
    *,
    quality_mode: str = "balanced",
    dev_mode: bool = False,
) -> None:
    try:
        from pipeline.evidence import load_upload_order
        from pipeline.product_pipeline import run_product_pipeline

        footage_dir = FOOTAGE_ROOT / project
        order = load_upload_order(footage_dir) or []
        image_ext = {".jpg", ".jpeg", ".png", ".webp"}
        by_name = {
            p.name: p
            for p in footage_dir.iterdir()
            if p.is_file() and p.suffix.lower() in image_ext
        }
        if order:
            images = [by_name[n] for n in order if n in by_name]
            for n, p in by_name.items():
                if n not in order:
                    images.append(p)
        else:
            images = list(by_name.values())
        videos = [
            p
            for p in footage_dir.iterdir()
            if p.suffix.lower() in {".mp4", ".mov"} and not p.name.startswith("still_")
        ]
        if not videos and not images:
            raise RuntimeError("no usable photo or video uploaded")

        duration = _target_duration(len(images) + len(videos))
        bgm = _resolve_bgm()
        use_hybrid = bool(os.getenv("ANTHROPIC_API_KEY"))
        mode = (quality_mode or "balanced").strip().lower()
        if mode not in ("economy", "balanced", "premium", "max"):
            mode = "balanced"

        _set_job(
            job_id,
            status="running",
            stage="prepare",
            duration_s=duration,
            generator="hybrid" if use_hybrid else "baseline",
            quality_mode=mode,
            dev_mode=bool(dev_mode),
        )

        if images:
            divisor = max(min(len(images), 4), 1)
            still_sec = max(4.0, min(16.0, duration / divisor))
            for i, image in enumerate(images, start=1):
                _image_to_clip(image, footage_dir / f"still_{i:02d}.mp4", still_sec)

        project_dir = WORK_ROOT / project
        if project_dir.exists():
            shutil.rmtree(project_dir)
        project_dir.mkdir(parents=True, exist_ok=True)
        if note.strip():
            (project_dir / "note.txt").write_text(note.strip(), encoding="utf-8")
            (footage_dir / "note.txt").write_text(note.strip(), encoding="utf-8")

        beats_json = bgm.with_suffix(".beats.json")
        if not beats_json.exists():
            from pipeline import beats

            _set_job(job_id, status="running", stage="beats")
            beats.extract_beat_grid(bgm)

        # Soft-promote stills after filter (inside product pipeline filter stage)
        def _on_stage(stage: str, payload: dict) -> None:
            fields: dict = {
                "status": "running",
                "stage": stage,
                "generator": payload.get("generator") or "hybrid",
                "quality_mode": mode,
                "estimated_cost_usd_total": payload.get("estimated_cost_usd_total"),
            }
            if stage == "render":
                fields["progress_pct"] = payload.get("progress_pct", 0)
            # Dev telemetry: stage/provider/model/tokens/cost — never prompts/keys
            if dev_mode:
                fields["telemetry"] = payload.get("telemetry") or []
            _set_job(job_id, **fields)

        last_pct = {"v": -1}

        def _on_render_progress(done: int, total: int) -> None:
            if total <= 0:
                return
            pct = min(99, int(100 * done / total))
            if pct == last_pct["v"] or (pct < 99 and pct - last_pct["v"] < 2):
                return
            last_pct["v"] = pct
            fields = {
                "status": "running",
                "stage": "render",
                "progress_pct": pct,
                "progress": f"{done}/{total}",
            }
            _set_job(job_id, **fields)

        result = run_product_pipeline(
            work_root=WORK_ROOT,
            footage_dir=footage_dir,
            project=project,
            bgm_track=bgm,
            target_duration_s=duration,
            quality_mode=mode,
            force=True,
            user_intent=note,
            on_stage=_on_stage,
            render_progress=_on_render_progress,
            render_scale=WEB_RENDER_SCALE,
            render_concurrency=WEB_RENDER_CONCURRENCY,
            use_hybrid=use_hybrid,
        )

        done_fields = {
            "status": "done",
            "stage": "done",
            "video_url": f"/api/jobs/{job_id}/video",
            "finished_at": time.time(),
            "generator": result.generator,
            "quality_mode": result.quality_mode,
            "estimated_cost_usd_total": result.estimated_cost_usd_total,
            "progress_pct": 100,
        }
        if dev_mode:
            done_fields["telemetry"] = [s.public_dict() for s in result.stages]
        _set_job(job_id, **done_fields)
    except Exception as exc:
        _set_job(job_id, status="error", error=_friendly_error(exc), detail=str(exc)[:1200])


@app.post("/api/jobs")
async def create_job(
    note: str = Form(""),
    files: list[UploadFile] | None = File(None),
    quality_mode: str = Form("balanced"),
    dev_mode: str = Form("0"),
):
    uploads = files or []
    if not note.strip() and not uploads:
        raise HTTPException(400, "Attach a photo, video, or note.")

    job_id = uuid.uuid4().hex[:10]
    project = f"web_{job_id}"
    footage_dir = FOOTAGE_ROOT / project
    footage_dir.mkdir(parents=True, exist_ok=True)

    saved = 0
    upload_names: list[str] = []
    for upload in uploads:
        if not upload.filename:
            continue
        # skip HEIC — ffmpeg often can't decode without extras
        if Path(upload.filename).suffix.lower() in {".heic", ".heif"}:
            continue
        dest = footage_dir / _safe_name(upload.filename)
        data = await upload.read()
        if not data:
            continue
        dest.write_bytes(data)
        upload_names.append(dest.name)
        saved += 1

    if saved == 0 and not note.strip():
        shutil.rmtree(footage_dir, ignore_errors=True)
        raise HTTPException(400, "Empty upload.")

    # Preserve multipart order explicitly — never recover chronology from alpha sort.
    if upload_names:
        from pipeline.evidence import write_upload_order

        write_upload_order(footage_dir, upload_names)

    if saved == 0:
        slate = footage_dir / "slate.mp4"
        _run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x3a3a3a:s=1080x1920:d=8,noise=alls=40:allf=t+u",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-r",
                "30",
                str(slate),
            ]
        )

    duration = _target_duration(saved)
    mode = (quality_mode or "balanced").strip().lower()
    if mode not in ("economy", "balanced", "premium", "max"):
        mode = "balanced"
    dev = str(dev_mode).strip().lower() in ("1", "true", "yes", "on")
    _set_job(
        job_id,
        status="queued",
        project=project,
        stage="queued",
        duration_s=duration,
        quality_mode=mode,
        dev_mode=dev,
    )
    threading.Thread(
        target=_process_job,
        args=(job_id, project, note),
        kwargs={"quality_mode": mode, "dev_mode": dev},
        daemon=True,
    ).start()
    return {
        "id": job_id,
        "status": "queued",
        "project": project,
        "duration_s": duration,
        "quality_mode": mode,
        "dev_mode": dev,
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = _read_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    return job


@app.get("/api/jobs/{job_id}/video")
def get_job_video(job_id: str):
    job = _read_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    project = job.get("project")
    final = WORK_ROOT / str(project) / "final.mp4"
    if not final.exists():
        raise HTTPException(404, "Video not ready.")
    return FileResponse(final, media_type="video/mp4", filename="zlog.mp4")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "ai": bool(os.getenv("ANTHROPIC_API_KEY")),
        "openai": bool(os.getenv("OPENAI_API_KEY")),
        "duration_s": _target_duration(),
        "taste": TASTE_PATH.exists(),
        "pipeline": "hybrid",
        "youtube_taste_overwrite": False,
    }


@app.post("/api/trends/run")
def run_trends_now():
    """Disabled — YouTube thumbnail taste overwrite is off (PROMPT 9)."""
    return {
        "ok": False,
        "status": "disabled",
        "reason": "YouTube thumbnail → taste_profile overwrite is disabled",
    }


@app.get("/api/jobs/{job_id}/review-items")
def review_items(job_id: str):
    """Frames for quick keep/drop after a finished film."""
    job = _read_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    project = str(job.get("project"))
    project_dir = WORK_ROOT / project
    cand_path = project_dir / "candidates.json"
    if not cand_path.exists():
        raise HTTPException(404, "No candidates.")

    data = json.loads(cand_path.read_text(encoding="utf-8"))
    edl_path = project_dir / "edl_ai.json"
    if not edl_path.exists():
        edl_path = project_dir / "edl_baseline.json"
    edl = json.loads(edl_path.read_text(encoding="utf-8")) if edl_path.exists() else {"timeline": []}
    selected_ids = {c["segment_id"] for c in edl.get("timeline", [])}

    items = []
    for c in data.get("candidates", []):
        sid = c["segment_id"]
        frame = c.get("frame_path")
        abs_frame = project_dir / frame
        if not abs_frame.exists():
            continue
        items.append(
            {
                "segment_id": sid,
                "suggested": "keep" if sid in selected_ids else "drop",
                "frame_url": f"/api/jobs/{job_id}/frame?segment_id={quote(sid, safe='')}",
            }
        )
    # Cap UI load
    keeps = [i for i in items if i["suggested"] == "keep"][:4]
    drops = [i for i in items if i["suggested"] == "drop"][:4]
    return {"items": keeps + drops}


@app.get("/api/jobs/{job_id}/frame")
def job_frame(job_id: str, segment_id: str):
    job = _read_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    project_dir = WORK_ROOT / str(job.get("project"))
    frame = project_dir / "frames" / f"{segment_id}.jpg"
    if not frame.exists():
        cand_path = project_dir / "candidates.json"
        if cand_path.exists():
            data = json.loads(cand_path.read_text(encoding="utf-8"))
            for c in data.get("candidates", []) + data.get("rejected", []):
                if c.get("segment_id") == segment_id:
                    frame = project_dir / c["frame_path"]
                    break
    if not frame.exists():
        raise HTTPException(404, "Frame not found.")
    return FileResponse(frame, media_type="image/jpeg")


@app.post("/api/jobs/{job_id}/review")
async def submit_review(job_id: str, payload: dict):
    """Body: { decisions: [{segment_id, decision, reason?}] } → taste/examples.jsonl."""
    from pipeline import taste

    job = _read_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found.")
    project = str(job.get("project"))
    project_dir = WORK_ROOT / project
    decisions = payload.get("decisions") or []
    recorded = 0
    for item in decisions:
        sid = item.get("segment_id")
        decision = item.get("decision")
        if decision not in ("keep", "drop") or not sid:
            continue
        frame = project_dir / "frames" / f"{sid}.jpg"
        if not frame.exists():
            cand_path = project_dir / "candidates.json"
            data = json.loads(cand_path.read_text(encoding="utf-8"))
            for c in data.get("candidates", []) + data.get("rejected", []):
                if c.get("segment_id") == sid:
                    frame = project_dir / c["frame_path"]
                    break
        if not frame.exists():
            continue
        taste.record_example(
            project,
            sid,
            frame,
            decision,
            (item.get("reason") or f"web review ({decision})").strip(),
        )
        recorded += 1
    return {"ok": True, "recorded": recorded}


if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
