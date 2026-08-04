"""Build on-screen captions for an EDL timeline.

Captions attach to existing timeline clips only — no absolute timestamps.
Two entry points:
  - build_captions():      heuristic Korean vlog captions from tags (baseline)
  - build_captions_from_ai(): place AI-written caption *text* (select_ai)
Either way, placement math (offsets inside the host clip) lives here and
only here — the LLM never emits a number that ends up as a timestamp.
"""

from __future__ import annotations

from pipeline.edl import Caption, CandidateScene, TimelineClip

MAX_CAPTION_CHARS = 24

# Korean vlog-style lines keyed on tag.py's structured tags. Deliberately
# short — burned-in vlog captions read at a glance or not at all.
_SUBJECT_LINE = {
    "person": "오늘의 주인공",
    "food": "일단 먹고 시작",
    "landscape": "이 풍경 실화?",
    "object": "요즘 최애템",
    "text": "기록해두기",
}

_MOOD_TITLE = {
    "bright": "맑음, 완벽한 하루",
    "calm": "느긋한 오후의 기록",
    "lively": "쉴 틈 없는 하루",
    "moody": "블루 아워",
}

_MOOD_LINE = {
    "bright": "날씨가 다 했다",
    "calm": "이 순간이 좋아서",
    "lively": "정신없지만 행복해",
    "moody": "괜히 감성적인 시간",
}

_CLOSING_LINES = ["오늘도 잘 살았다", "내일 또 만나요", "여기까지, 오늘의 기록"]


def _offsets(clip_dur: float, start_want: float, end_want: float) -> tuple[float, float]:
    """Keep caption window inside the clip. Short reuses must not inherit
    longer end offsets from a previous occurrence of the same segment_id."""
    if clip_dur <= 0.05:
        return 0.0, round(clip_dur, 3)
    start = min(max(0.0, start_want), max(0.0, clip_dur - 0.05))
    end = min(clip_dur, max(start + 0.05, end_want))
    return round(start, 3), round(end, 3)


def _clip_text(text: str) -> str:
    text = " ".join(text.split())
    return text[:MAX_CAPTION_CHARS].strip()


def _title_caption(clip: TimelineClip, text: str) -> Caption:
    dur = clip.out_sec - clip.in_sec
    start, end = _offsets(dur, min(0.2, dur * 0.1), max(1.4, dur * 0.8))
    return Caption(
        segment_id=clip.segment_id,
        text=text,
        style="title",
        font="display",
        position="center",
        start_offset_sec=start,
        end_offset_sec=end,
    )


def _sub_caption(clip: TimelineClip, text: str, style: str = "subtitle") -> Caption:
    dur = clip.out_sec - clip.in_sec
    start, end = _offsets(dur, min(0.15, dur * 0.1), max(1.2, dur * 0.9))
    return Caption(
        segment_id=clip.segment_id,
        text=text,
        style=style,  # type: ignore[arg-type]
        font="body",
        position="bottom",
        start_offset_sec=start,
        end_offset_sec=end,
    )


def build_captions(
    timeline: list[TimelineClip],
    candidates_by_id: dict[str, CandidateScene],
    note: str | None = None,
) -> list[Caption]:
    """Heuristic Korean vlog captions: opening title, situational subtitles
    on distinct mid-film clips (from tags), and a closing line.

    Captions bind to the *first* timeline occurrence of each chosen
    segment_id (same rule as validate_edl / Remotion).
    """
    if not timeline:
        return []

    note = (note or "").strip()
    captions: list[Caption] = []

    # One caption slot per distinct segment_id, first occurrence wins.
    seen: set[str] = set()
    hosts: list[TimelineClip] = []
    for clip in timeline:
        if clip.segment_id not in seen:
            seen.add(clip.segment_id)
            hosts.append(clip)

    first = hosts[0]
    first_tags = _tags(candidates_by_id, first.segment_id)
    opening = _clip_text(note) if note else _opening_text(first_tags)
    captions.append(_title_caption(first, opening))

    # Situational subtitles on up to 3 distinct middle clips, no repeats.
    middle = hosts[1:-1] if len(hosts) > 2 else []
    used_lines: set[str] = set()
    step = max(1, len(middle) // 3)
    for clip in middle[::step]:
        tags = _tags(candidates_by_id, clip.segment_id)
        line = _mid_text(tags)
        if line in used_lines:
            continue
        used_lines.add(line)
        captions.append(_sub_caption(clip, line))
        if len(used_lines) >= 3:
            break

    if len(hosts) > 1:
        last = hosts[-1]
        closing = _CLOSING_LINES[len(hosts) % len(_CLOSING_LINES)]
        captions.append(_sub_caption(last, closing, style="lower_third"))

    return captions


def build_captions_from_ai(
    timeline: list[TimelineClip],
    selected: list[dict],
    candidates_by_id: dict[str, CandidateScene],
    note: str | None = None,
) -> list[Caption]:
    """Place captions whose *text* the model wrote in submit_selection.

    `selected` items may carry `caption` (situational Korean line for that
    cut); the model also gives `title`/`closing` via select_ai. Placement
    offsets are computed here from the host clip's played window — the
    model contributes words, never numbers. Falls back to heuristics for
    anything missing.
    """
    if not timeline:
        return []

    by_first: dict[str, TimelineClip] = {}
    for clip in timeline:
        by_first.setdefault(clip.segment_id, clip)

    captions: list[Caption] = []
    note = (note or "").strip()

    ai_caps: dict[str, str] = {}
    title_text = ""
    closing_text = ""
    for sel in selected:
        text = _clip_text(str(sel.get("caption") or ""))
        if not text:
            continue
        role = sel.get("role")
        # Soft Flow (hook/resonance) + legacy (opening/closing)
        if role in {"opening", "hook"} and not title_text:
            title_text = text
            continue
        if role in {"closing", "resonance"} and not closing_text:
            closing_text = text
            continue
        ai_caps.setdefault(str(sel.get("segment_id")), text)

    first = timeline[0]
    first_tags = _tags(candidates_by_id, first.segment_id)
    opening = _clip_text(note) or title_text or _opening_text(first_tags)
    captions.append(_title_caption(first, opening))

    last = timeline[-1] if len(timeline) > 1 else None
    for segment_id, text in ai_caps.items():
        host = by_first.get(segment_id)
        if host is None or host.segment_id == first.segment_id:
            continue
        if last is not None and host.segment_id == last.segment_id:
            continue
        captions.append(_sub_caption(host, text))

    if last is not None:
        closing = closing_text or _CLOSING_LINES[len(by_first) % len(_CLOSING_LINES)]
        host = by_first.get(last.segment_id, last)
        captions.append(_sub_caption(host, closing, style="lower_third"))

    return captions


def _tags(by_id: dict[str, CandidateScene], segment_id: str):
    cand = by_id.get(segment_id)
    return cand.tags if cand is not None else None


def _opening_text(tags) -> str:
    if tags is not None and tags.mood in _MOOD_TITLE:
        return _MOOD_TITLE[tags.mood]
    return "오늘의 기록"


def _mid_text(tags) -> str:
    if tags is not None:
        if tags.subject in _SUBJECT_LINE:
            return _SUBJECT_LINE[tags.subject]
        if tags.mood in _MOOD_LINE:
            return _MOOD_LINE[tags.mood]
    return "지금 이 순간"


if __name__ == "__main__":
    from pipeline.edl import Quality, Tags

    def _cand(seg: str, subject: str = "landscape", mood: str = "calm") -> CandidateScene:
        return CandidateScene(
            segment_id=seg,
            source_file="x.mp4",
            start_sec=0.0,
            end_sec=5.0,
            duration=5.0,
            frame_path="frames/a.jpg",
            quality=Quality(
                blur_score=1.0, brightness=0.5, phash="0", verdict="pass", duplicate_of=None
            ),
            tags=Tags(shot="medium", subject=subject, mood=mood, face_visible=False, keep_score=4),
        )

    def _clip(order: int, seg: str, in_s: float = 1.0, out_s: float = 3.0) -> TimelineClip:
        return TimelineClip(
            order=order, segment_id=seg, source_file="x.mp4", in_sec=in_s, out_sec=out_s
        )

    tl = [_clip(1, "a#1"), _clip(2, "a#2"), _clip(3, "a#3"), _clip(4, "a#4")]
    by_id = {s: _cand(s) for s in ("a#1", "a#2", "a#3", "a#4")}

    caps = build_captions(tl, by_id, note="midnight city")
    assert caps[0].text == "midnight city"
    assert caps[-1].style == "lower_third"
    assert all(len(c.text) <= MAX_CAPTION_CHARS for c in caps)

    short = _clip(1, "a#1", 0.0, 0.5)
    short_caps = build_captions([short], by_id, note="hi")
    assert short_caps[0].end_offset_sec is not None
    assert short_caps[0].end_offset_sec <= 0.5 + 1e-6
    assert short_caps[0].end_offset_sec > short_caps[0].start_offset_sec

    ai_caps = build_captions_from_ai(
        tl,
        [
            {"segment_id": "a#1", "role": "opening", "caption": "하루의 시작"},
            {"segment_id": "a#2", "role": "body", "caption": "카페에서 잠깐"},
            {"segment_id": "a#4", "role": "closing", "caption": "오늘 끝!"},
        ],
        by_id,
    )
    assert ai_caps[0].text == "하루의 시작"
    assert any(c.text == "카페에서 잠깐" for c in ai_caps)
    assert ai_caps[-1].text == "오늘 끝!"
    print("captions.py self-check ok:", len(caps), "heuristic /", len(ai_caps), "ai caption(s)")
