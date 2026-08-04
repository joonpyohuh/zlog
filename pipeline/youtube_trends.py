"""Fetch trending vlog metadata + thumbnails, analyze look/pacing into taste_profile.

Frames only (thumbnails) — never raw YouTube video files (architecture rule 1).
"""

from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from anthropic import Anthropic
from dotenv import load_dotenv
from googleapiclient.discovery import build

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parent.parent
TASTE_PATH = REPO_ROOT / "taste" / "taste_profile.json"
TREND_FRAMES_DIR = REPO_ROOT / "taste" / "trend_frames"
MODEL = "claude-sonnet-4-5-20250929"
MAX_THUMBS = 8


def fetch_youtube_trends(api_key: str, max_results: int = 10) -> list[dict]:
    youtube = build("youtube", "v3", developerKey=api_key)
    queries = ["vlog", "브이로그", "day in my life vlog"]
    # Recent window: last ~180 days from "today" in repo clock
    published_after = datetime(2025, 1, 1, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_videos: list[dict] = []
    seen: set[str] = set()

    for query in queries:
        search_response = (
            youtube.search()
            .list(
                q=query,
                part="id,snippet",
                maxResults=max_results,
                type="video",
                order="viewCount",
                publishedAfter=published_after,
            )
            .execute()
        )
        video_ids = [
            item["id"]["videoId"]
            for item in search_response.get("items", [])
            if item["id"]["videoId"] not in seen
        ]
        if not video_ids:
            continue
        seen.update(video_ids)

        video_response = (
            youtube.videos()
            .list(id=",".join(video_ids), part="snippet,statistics,contentDetails")
            .execute()
        )
        for item in video_response.get("items", []):
            snippet = item["snippet"]
            thumbs = snippet.get("thumbnails") or {}
            thumb = (
                (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get(
                    "url"
                )
            )
            all_videos.append(
                {
                    "video_id": item["id"],
                    "title": snippet.get("title"),
                    "description": (snippet.get("description") or "")[:200],
                    "tags": snippet.get("tags", [])[:12],
                    "duration": item["contentDetails"].get("duration"),
                    "viewCount": item["statistics"].get("viewCount"),
                    "thumbnail_url": thumb,
                }
            )
    return all_videos


def _download_thumbnails(videos: list[dict], limit: int = MAX_THUMBS) -> list[Path]:
    TREND_FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    # clear old thumbs so taste stays current
    for old in TREND_FRAMES_DIR.glob("*.jpg"):
        old.unlink()

    paths: list[Path] = []
    for video in videos:
        if len(paths) >= limit:
            break
        url = video.get("thumbnail_url")
        if not url:
            continue
        vid = re.sub(r"[^\w\-]+", "_", video.get("video_id") or f"t{len(paths)}")
        dest = TREND_FRAMES_DIR / f"{vid}.jpg"
        try:
            req = Request(url, headers={"User-Agent": "zlog-trends/1.0"})
            with urlopen(req, timeout=20) as resp:
                dest.write_bytes(resp.read())
            paths.append(dest)
            video["thumbnail_path"] = str(dest.relative_to(REPO_ROOT)).replace("\\", "/")
        except Exception as exc:
            print(f"  skip thumb {vid}: {exc}")
    return paths


def analyze_trends_and_generate_taste(videos: list[dict], thumb_paths: list[Path]) -> dict:
    """Vision + metadata → taste_profile.json schema."""
    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    meta = [
        {
            "title": v.get("title"),
            "duration": v.get("duration"),
            "viewCount": v.get("viewCount"),
            "tags": v.get("tags", [])[:8],
        }
        for v in videos
    ]

    content: list[dict] = [
        {
            "type": "text",
            "text": (
                "You are an expert editor for short emotional vlogs (CCD / low-fi / trendy YouTube).\n"
                "Below: metadata for high-view vlogs, plus thumbnail stills (look at color, framing, "
                "text overlays, mood — not faces to identify people).\n"
                "Deduce aesthetic + pacing rules for an automated editor.\n\n"
                f"<videos>\n{json.dumps(meta, ensure_ascii=False, indent=2)}\n</videos>\n\n"
                "Return ONLY JSON matching:\n"
                "{\n"
                '  "editing_rules": [string, ...],\n'
                '  "avoid": [string, ...],\n'
                '  "pacing": string,\n'
                '  "notes": string,\n'
                '  "look": {\n'
                '    "palette": string,\n'
                '    "grain": string,\n'
                '    "framing": string,\n'
                '    "text_style": string\n'
                "  }\n"
                "}"
            ),
        }
    ]
    for path in thumb_paths:
        data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
            }
        )

    response = client.messages.create(
        model=MODEL,
        max_tokens=1200,
        messages=[{"role": "user", "content": content}],
    )
    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    profile = json.loads(text.strip())
    # keep schema compatible with TasteProfile (extra look is ok if we strip for pydantic
    # — taste.py TasteProfile doesn't have look; store look inside notes if needed)
    look = profile.pop("look", None)
    if look and isinstance(look, dict):
        look_line = (
            f"Look — palette: {look.get('palette', '')}; grain: {look.get('grain', '')}; "
            f"framing: {look.get('framing', '')}; text: {look.get('text_style', '')}"
        )
        notes = profile.get("notes") or ""
        profile["notes"] = (notes + " | " + look_line).strip(" |")
        # also push look cues into editing_rules for select_ai
        rules = list(profile.get("editing_rules") or [])
        for key, label in (
            ("palette", "Color"),
            ("grain", "Texture"),
            ("framing", "Framing"),
            ("text_style", "On-screen text"),
        ):
            if look.get(key):
                rules.append(f"{label}: {look[key]}")
        profile["editing_rules"] = rules
    return profile


def main() -> None:
    """PROMPT 9: refuse automatic overwrite of taste/taste_profile.json.

    Opt in only with ZLOG_ALLOW_YOUTUBE_TASTE_OVERWRITE=1 (founder experiments).
    """
    if os.environ.get("ZLOG_ALLOW_YOUTUBE_TASTE_OVERWRITE", "").strip() not in (
        "1",
        "true",
        "yes",
    ):
        print(
            "youtube_trends: taste overwrite disabled "
            "(set ZLOG_ALLOW_YOUTUBE_TASTE_OVERWRITE=1 to enable)."
        )
        return

    youtube_api_key = os.environ.get("YOUTUBE_API_KEY")
    if not youtube_api_key:
        print("Error: YOUTUBE_API_KEY not found in environment.")
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY not found in environment.")
        return

    print("Fetching vlog trends from YouTube...")
    try:
        videos = fetch_youtube_trends(youtube_api_key)
        print(f"Fetched {len(videos)} videos.")
    except Exception as e:
        print(f"Failed to fetch from YouTube: {e}")
        return

    print("Downloading thumbnails (look frames)...")
    thumbs = _download_thumbnails(videos)
    print(f"Got {len(thumbs)} thumbnails → {TREND_FRAMES_DIR}")

    print("Analyzing trends + look with Claude vision...")
    try:
        taste_profile = analyze_trends_and_generate_taste(videos, thumbs)
    except Exception as e:
        print(f"Failed to analyze trends: {e}")
        return

    TASTE_PATH.parent.mkdir(exist_ok=True)
    payload = json.dumps(taste_profile, ensure_ascii=False, indent=2)
    TASTE_PATH.write_text(payload, encoding="utf-8")
    print(f"Successfully updated {TASTE_PATH}")
    try:
        print(payload)
    except UnicodeEncodeError:
        print(payload.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))


if __name__ == "__main__":
    main()
