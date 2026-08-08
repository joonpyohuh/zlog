"""Trip understanding, personalized dual planning, critic, and EDL migration."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from itertools import pairwise
from pathlib import Path

from pipeline.ai.schemas import AssetAnalysis
from pipeline.editorial_models import (
    CriticIssue,
    CriticReport,
    DayNode,
    EditingIntent,
    EditorialPackage,
    EditorialRole,
    EditTimeline,
    EffectCommand,
    EventNode,
    HighlightDecision,
    ObservableCue,
    RepairAction,
    SceneUnderstanding,
    SourceRange,
    TimelineCaptionItem,
    TimelineVideoItem,
    TripGraph,
)
from pipeline.edl import (
    EDL,
    Aesthetic,
    Audio,
    CandidatesFile,
    Canvas,
    Caption,
    Frame,
    SegmentsFile,
    Signature,
    TimelineClip,
)

THEORY_VERSION = "zlog-editorial-theory/1"
SHORT_TARGET_SEC = 35.0
LONG_TARGET_SEC = 600.0


def parse_editing_intent(prompt: str) -> EditingIntent:
    """Extract only preferences the user actually stated; unknown stays unknown."""

    text = prompt.strip()
    low = text.lower()
    mood = next(
        (
            value
            for terms, value in (
                (("잔잔", "차분", "calm", "soft"), "calm"),
                (("신나", "활기", "energetic", "upbeat"), "energetic"),
                (("청춘", "nostalg", "감성"), "nostalgic"),
                (("cinematic", "영화"), "cinematic"),
            )
            if any(term in low for term in terms)
        ),
        None,
    )
    pacing = None
    if any(term in low for term in ("잔잔", "차분", "천천히", "slow", "calm")):
        pacing = "calm"
    elif any(term in low for term in ("빠르", "신나", "energetic", "fast", "punchy")):
        pacing = "energetic"
    focus_terms = {
        "도시": ("도시", "city", "street"),
        "풍경": ("풍경", "landscape", "scenery"),
        "음식": ("음식", "food", "meal", "coffee", "카페"),
        "친구": ("친구", "friends", "people", "interaction"),
        "이동": ("이동", "train", "airport", "transit"),
        "밤": ("밤", "night"),
        "비": ("비", "rain"),
    }
    narrative_focus = [
        label
        for label, terms in focus_terms.items()
        if any(term in low for term in terms)
    ]
    avoid: list[str] = []
    for label, terms in focus_terms.items():
        if any(
            re.search(
                rf"(?:보다|말고|제외|적게|less|avoid)[^.!?]{{0,16}}{re.escape(term)}",
                low,
            )
            or re.search(
                rf"{re.escape(term)}[^.!?]{{0,16}}(?:보다|말고|제외|적게|less|avoid)",
                low,
            )
            for term in terms
        ):
            avoid.append(label)
    speaking = "unspecified"
    if any(term in low for term in ("말하는 장면은 적게", "대화 적게", "less talking")):
        speaking = "less"
    elif any(
        term in low for term in ("대화 중심", "말하는 장면", "narration", "speaking")
    ):
        speaking = "more"
    captions = "unspecified"
    if any(term in low for term in ("자막 없이", "자막 없음", "no caption")):
        captions = "none"
    elif any(term in low for term in ("자막은 적게", "자막 적게", "sparse caption")):
        captions = "sparse"
    elif any(term in low for term in ("자막", "caption", "subtitle")):
        captions = "contextual"
    visual_style = mood if mood in {"cinematic", "nostalgic"} else None
    return EditingIntent(
        raw_prompt=text,
        desired_mood=mood,
        pacing=pacing,
        visual_style=visual_style,
        narrative_focus=narrative_focus,
        avoid=list(dict.fromkeys(avoid)),
        speaking_preference=speaking,
        caption_preference=captions,
        unknowns=[] if text else ["No creative direction was provided."],
    )


def _load_analyses(project_dir: Path) -> dict[str, AssetAnalysis]:
    path = project_dir / "asset_analyses.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        analysis.segment_id: analysis
        for raw in data.get("analyses", [])
        if (analysis := AssetAnalysis.model_validate(raw))
    }


def _event_title(analysis: AssetAnalysis | None) -> str:
    if not analysis:
        return "Unclassified moment"
    action = analysis.action_progression.strip()
    if action and action.lower() not in {"unknown", "none"}:
        return action.split(".", 1)[0][:56]
    return analysis.scene.value.replace("_", " ").title()


def _roles_for(
    analysis: AssetAnalysis | None,
    *,
    first_in_event: bool,
    first_overall: bool,
    last_overall: bool,
) -> list[EditorialRole]:
    roles: list[EditorialRole] = []
    if first_in_event or first_overall:
        roles.append(EditorialRole.establishing)
    if analysis:
        if analysis.scene.value == "transit":
            roles.extend([EditorialRole.progression, EditorialRole.transition])
        elif analysis.scene.value == "food":
            roles.extend([EditorialRole.action, EditorialRole.detail])
        elif analysis.scene.value == "activity":
            roles.extend([EditorialRole.progression, EditorialRole.action])
        elif analysis.scene.value in {"indoor", "outdoor"}:
            roles.append(EditorialRole.atmosphere)
        elif analysis.scene.value == "portrait":
            roles.append(EditorialRole.interaction)
        observed = " ".join(
            [analysis.action_progression, *analysis.visually_grounded_facts]
        ).lower()
        if any(word in observed for word in ("laugh", "smile", "reaction", "gaze")):
            roles.extend([EditorialRole.reaction, EditorialRole.highlight])
        if analysis.motion_quality > 0.45 and EditorialRole.action not in roles:
            roles.append(EditorialRole.action)
    if not roles:
        roles.append(EditorialRole.breathing)
    if last_overall:
        roles.extend([EditorialRole.payoff, EditorialRole.ending])
    return list(dict.fromkeys(roles))


def _observable_cues(analysis: AssetAnalysis | None) -> list[ObservableCue]:
    if not analysis:
        return []
    cues: list[ObservableCue] = []
    observed = " ".join(
        [analysis.action_progression, *analysis.visually_grounded_facts]
    ).lower()
    evidence = analysis.evidence_frame_ids
    if any(word in observed for word in ("laugh", "smile")):
        cues.append(
            ObservableCue(
                kind="laughter",
                description="Visible laughter or smile cue.",
                evidence_ids=evidence,
            )
        )
    if analysis.subjects and any(
        word in observed for word in ("together", "friend", "talk", "look")
    ):
        cues.append(
            ObservableCue(
                kind="interaction",
                description="People interaction is visually grounded.",
                evidence_ids=evidence,
            )
        )
    if analysis.motion_quality > 0.45:
        cues.append(
            ObservableCue(
                kind="movement",
                description="Visible motion is present.",
                evidence_ids=evidence,
            )
        )
    return cues


def build_trip_graph(project_dir: Path, project: str) -> TripGraph:
    segments = SegmentsFile.model_validate_json(
        (project_dir / "segments.json").read_text(encoding="utf-8")
    )
    candidates = CandidatesFile.model_validate_json(
        (project_dir / "candidates.json").read_text(encoding="utf-8")
    )
    analyses = _load_analyses(project_dir)
    candidate_by_id = {
        item.segment_id: item for item in [*candidates.candidates, *candidates.rejected]
    }
    source_order: dict[str, int] = {}
    evidence_path = project_dir / "evidence_manifest.json"
    if evidence_path.exists():
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        source_order = {
            source["source_file"]: int(
                source.get("time_order", source.get("upload_index", i))
            )
            for i, source in enumerate(evidence.get("sources", []))
        }
    ordered = sorted(
        segments.segments,
        key=lambda scene: (
            source_order.get(scene.source_file, 10_000),
            scene.start_sec,
            scene.segment_id,
        ),
    )
    dates = {
        sid: analysis.capture_time[:10]
        for sid, analysis in analyses.items()
        if analysis.capture_time and len(analysis.capture_time) >= 10
    }
    trusted_days = bool(ordered) and all(scene.segment_id in dates for scene in ordered)
    day_key_by_scene = {
        scene.segment_id: dates[scene.segment_id] if trusted_days else "unknown-day"
        for scene in ordered
    }
    day_keys = list(dict.fromkeys(day_key_by_scene.values()))
    day_id_by_key = {key: f"day-{i + 1}" for i, key in enumerate(day_keys)}

    # ponytail: adjacent scene-kind grouping is a v1 hypothesis; replace it with
    # a validated semantic-boundary model once project correction data exists.
    event_keys: list[tuple[str, str]] = []
    event_for_scene: dict[str, str] = {}
    previous_key: tuple[str, str] | None = None
    event_index = 0
    for scene in ordered:
        analysis = analyses.get(scene.segment_id)
        day_id = day_id_by_key[day_key_by_scene[scene.segment_id]]
        semantic = analysis.scene.value if analysis else "unknown"
        key = (day_id, semantic)
        if key != previous_key:
            event_index += 1
            event_keys.append(key)
            previous_key = key
        event_for_scene[scene.segment_id] = f"event-{event_index}"

    scene_models: list[SceneUnderstanding] = []
    for index, scene in enumerate(ordered):
        analysis = analyses.get(scene.segment_id)
        candidate = candidate_by_id.get(scene.segment_id)
        event_id = event_for_scene[scene.segment_id]
        first_event = (
            index == 0 or event_for_scene[ordered[index - 1].segment_id] != event_id
        )
        roles = _roles_for(
            analysis,
            first_in_event=first_event,
            first_overall=index == 0,
            last_overall=index == len(ordered) - 1,
        )
        scene_models.append(
            SceneUnderstanding(
                scene_id=f"scene-{scene.segment_id}",
                segment_id=scene.segment_id,
                source_range=SourceRange(
                    source_file=scene.source_file,
                    start_ms=round(scene.start_sec * 1000),
                    end_ms=round(scene.end_sec * 1000),
                ),
                chronological_index=index,
                day_id=day_id_by_key[day_key_by_scene[scene.segment_id]],
                event_id=event_id,
                label=_event_title(analysis),
                subjects=analysis.subjects if analysis else [],
                observed_action=analysis.action_progression if analysis else "unknown",
                vlog_mode="unknown",
                roles=roles,
                observable_cues=_observable_cues(analysis),
                emotional_progression=(
                    "excitement"
                    if analysis
                    and any(
                        role in roles
                        for role in (EditorialRole.reaction, EditorialRole.highlight)
                    )
                    else "unknown"
                ),
                technical_quality=(
                    "usable"
                    if candidate and candidate.quality.verdict == "pass"
                    else "limited"
                    if candidate
                    else "unknown"
                ),
                redundancy_group=analysis.redundancy_group if analysis else None,
                focus_x=analysis.focus_x if analysis else 0.5,
                focus_y=analysis.focus_y if analysis else 0.5,
                crop_confidence=analysis.crop_confidence if analysis else 0,
                evidence_frame_ids=analysis.evidence_frame_ids if analysis else [],
                confidence=1 - analysis.uncertainty if analysis else 0.2,
            )
        )

    scenes_by_event: dict[str, list[SceneUnderstanding]] = defaultdict(list)
    for scene in scene_models:
        scenes_by_event[scene.event_id].append(scene)
    events = [
        EventNode(
            event_id=event_id,
            day_id=scenes[0].day_id,
            title=scenes[0].label,
            scene_ids=[scene.scene_id for scene in scenes],
        )
        for event_id, scenes in scenes_by_event.items()
    ]
    events_by_day: dict[str, list[str]] = defaultdict(list)
    for event in events:
        events_by_day[event.day_id].append(event.event_id)
    days = [
        DayNode(
            day_id=day_id_by_key[key],
            title=f"DAY {i + 1}",
            date=key if trusted_days else None,
            event_ids=events_by_day[day_id_by_key[key]],
            boundary_confidence=0.95 if trusted_days else 0.35,
            boundary_reason="capture date"
            if trusted_days
            else "upload chronology; date unknown",
        )
        for i, key in enumerate(day_keys)
    ]
    return TripGraph(
        project=project,
        input_mode=(
            "single_long_video"
            if len({scene.source_file for scene in ordered}) == 1
            else "multiple_clips"
        ),
        days=days,
        events=events,
        scenes=scene_models,
        unknowns=(
            []
            if trusted_days
            else ["Capture dates are incomplete; Day boundaries need user review."]
        ),
    )


def _matches_intent(scene: SceneUnderstanding, intent: EditingIntent) -> bool:
    haystack = " ".join(
        [scene.label, scene.observed_action, *scene.subjects, *scene.roles]
    ).lower()
    return any(term.lower() in haystack for term in intent.narrative_focus)


def _is_avoided(scene: SceneUnderstanding, intent: EditingIntent) -> bool:
    haystack = " ".join([scene.label, scene.observed_action, *scene.subjects]).lower()
    return any(term.lower() in haystack for term in intent.avoid)


def select_highlights(
    graph: TripGraph, intent: EditingIntent, output_type: str
) -> list[HighlightDecision]:
    decisions: list[HighlightDecision] = []
    event_first = {event.scene_ids[0] for event in graph.events}
    for scene in graph.scenes:
        reasons: list[str] = []
        rejected: list[str] = []
        if scene.scene_id in event_first:
            reasons.append("Introduces a new observed event or place context.")
        if EditorialRole.reaction in scene.roles:
            reasons.append("Contains an observable human reaction.")
        if EditorialRole.ending in scene.roles or EditorialRole.payoff in scene.roles:
            reasons.append("Provides an ending or payoff.")
        if _matches_intent(scene, intent):
            reasons.append("Directly matches the current creative direction.")
        if scene.technical_quality == "limited":
            rejected.append(
                "Technical quality is limited, but context role is preserved."
            )
        if _is_avoided(scene, intent):
            rejected.append("Conflicts with an explicit current-project preference.")
        if output_type == "long":
            selected = not _is_avoided(scene, intent)
            if not reasons:
                reasons.append("Preserves chronological travel progression.")
        else:
            selected = bool(reasons) and not _is_avoided(scene, intent)
            if scene.redundancy_group:
                rejected.append("A visually redundant alternative may be preferred.")
            if not reasons:
                reasons.append("No short-form-specific story reason was observed.")
        decisions.append(
            HighlightDecision(
                scene_id=scene.scene_id,
                output_type=output_type,  # type: ignore[arg-type]
                selected=selected,
                reasons=reasons,
                rejected_reasons=rejected,
            )
        )
    return decisions


def _desired_duration(
    scene: SceneUnderstanding, intent: EditingIntent, output_type: str
) -> float:
    source_sec = (scene.source_range.end_ms - scene.source_range.start_ms) / 1000
    if output_type == "long":
        if any(
            role in scene.roles
            for role in (EditorialRole.highlight, EditorialRole.payoff)
        ):
            wanted = 6.0
        elif any(
            role in scene.roles
            for role in (EditorialRole.atmosphere, EditorialRole.breathing)
        ):
            wanted = 5.0
        elif EditorialRole.transition in scene.roles:
            wanted = 2.5
        else:
            wanted = 4.0
    else:
        if any(
            role in scene.roles
            for role in (
                EditorialRole.highlight,
                EditorialRole.payoff,
                EditorialRole.ending,
            )
        ):
            wanted = 3.5
        elif EditorialRole.establishing in scene.roles:
            wanted = 2.2
        else:
            wanted = 1.8
    if intent.pacing == "calm" and EditorialRole.transition not in scene.roles:
        wanted *= 1.25
    elif intent.pacing == "energetic" and EditorialRole.highlight not in scene.roles:
        wanted *= 0.8
    return max(0.6, min(source_sec, wanted))


def _effect_for_scene(
    scene: SceneUnderstanding,
    output_type: str,
    *,
    reaction_effect_available: bool,
) -> EffectCommand:
    if (
        output_type == "short"
        and reaction_effect_available
        and EditorialRole.reaction in scene.roles
    ):
        return EffectCommand(
            effect_id="reaction_punch_in",
            duration_frames=12,
            reason="A visible human reaction is the short-form emphasis point.",
        )
    if EditorialRole.payoff in scene.roles:
        return EffectCommand(
            effect_id="micro_push_in",
            duration_frames=30,
            reason="The observed ending/payoff receives a restrained visual emphasis.",
        )
    return EffectCommand()


def _short_order(
    graph: TripGraph, decisions: list[HighlightDecision], intent: EditingIntent
) -> list[SceneUnderstanding]:
    selected_ids = {decision.scene_id for decision in decisions if decision.selected}
    selected = [scene for scene in graph.scenes if scene.scene_id in selected_ids]
    if not selected:
        selected = graph.scenes[:]
    hook = max(
        selected,
        key=lambda scene: (
            EditorialRole.reaction in scene.roles,
            _matches_intent(scene, intent),
            EditorialRole.highlight in scene.roles,
            -scene.chronological_index,
        ),
    )
    ending = next(
        (
            scene
            for scene in reversed(selected)
            if any(
                role in scene.roles
                for role in (EditorialRole.ending, EditorialRole.payoff)
            )
        ),
        selected[-1],
    )
    anchors = {hook.scene_id, ending.scene_id}
    middle = [scene for scene in selected if scene.scene_id not in anchors]
    middle.sort(key=lambda scene: scene.chronological_index)
    ordered = [hook, *middle, ending]
    return list({scene.scene_id: scene for scene in ordered}.values())


def build_timeline(
    graph: TripGraph,
    intent: EditingIntent,
    decisions: list[HighlightDecision],
    output_type: str,
) -> EditTimeline:
    selected_ids = {decision.scene_id for decision in decisions if decision.selected}
    scenes = (
        _short_order(graph, decisions, intent)
        if output_type == "short"
        else [scene for scene in graph.scenes if scene.scene_id in selected_ids]
    )
    if not scenes:
        scenes = graph.scenes[:1]
    target = SHORT_TARGET_SEC if output_type == "short" else LONG_TARGET_SEC
    fps = 30
    durations = [_desired_duration(scene, intent, output_type) for scene in scenes]
    chosen: list[tuple[SceneUnderstanding, float]] = []
    total = 0.0
    for scene, duration in zip(scenes, durations, strict=True):
        if output_type == "short" and chosen and total + duration > target:
            continue
        chosen.append((scene, duration))
        total += duration
        if total >= target:
            break
    if output_type == "short":
        ending = scenes[-1]
        if not any(scene.scene_id == ending.scene_id for scene, _ in chosen):
            ending_duration = _desired_duration(ending, intent, output_type)
            while len(chosen) > 1 and total + ending_duration > target:
                _, removed_duration = chosen.pop()
                total -= removed_duration
            chosen.append((ending, ending_duration))
            total += ending_duration
    if output_type == "short" and total < target * 0.75:
        for scene in graph.scenes:
            if any(existing.scene_id == scene.scene_id for existing, _ in chosen):
                continue
            duration = _desired_duration(scene, intent, output_type)
            if total + duration > target:
                continue
            chosen.append((scene, duration))
            total += duration
            if total >= target * 0.9:
                break
    if output_type == "short":
        ending_id = scenes[-1].scene_id
        ending_item = next(
            (item for item in chosen if item[0].scene_id == ending_id), None
        )
        if ending_item:
            chosen = [item for item in chosen if item[0].scene_id != ending_id]
            chosen.append(ending_item)
    video_items: list[TimelineVideoItem] = []
    cursor = 0
    reaction_effect_available = True
    for index, (scene, duration) in enumerate(chosen):
        frames = max(1, round(duration * fps))
        source_available_ms = scene.source_range.end_ms - scene.source_range.start_ms
        source_duration_ms = min(source_available_ms, round(frames / fps * 1000))
        source_in_ms = scene.source_range.start_ms
        effect = _effect_for_scene(
            scene,
            output_type,
            reaction_effect_available=reaction_effect_available,
        )
        if effect.effect_id == "reaction_punch_in":
            reaction_effect_available = False
        video_items.append(
            TimelineVideoItem(
                item_id=f"{output_type}-item-{index + 1}",
                scene_id=scene.scene_id,
                segment_id=scene.segment_id,
                source_file=scene.source_range.source_file,
                source_in_ms=source_in_ms,
                source_out_ms=source_in_ms + source_duration_ms,
                timeline_start_frame=cursor,
                duration_frames=frames,
                roles=scene.roles,
                decision_reason=next(
                    decision.reasons[0]
                    for decision in decisions
                    if decision.scene_id == scene.scene_id
                ),
                focus_x=scene.focus_x,
                focus_y=scene.focus_y,
                crop_confidence=scene.crop_confidence,
                effect=effect,
            )
        )
        cursor += frames
    captions: list[TimelineCaptionItem] = []
    if intent.caption_preference != "none" and video_items:
        first_scene = next(
            scene for scene in graph.scenes if scene.scene_id == video_items[0].scene_id
        )
        captions.append(
            TimelineCaptionItem(
                item_id=f"{output_type}-caption-1",
                start_frame=0,
                duration_frames=min(video_items[0].duration_frames, fps * 2),
                text=first_scene.label,
                style="lower_third",
                animation="soft_fade",
                grounding=f"Observed scene label: {first_scene.label}",
            )
        )
        for before, item in pairwise(video_items):
            previous_scene = next(
                scene for scene in graph.scenes if scene.scene_id == before.scene_id
            )
            current_scene = next(
                scene for scene in graph.scenes if scene.scene_id == item.scene_id
            )
            if previous_scene.day_id == current_scene.day_id:
                continue
            day = next(day for day in graph.days if day.day_id == current_scene.day_id)
            captions.append(
                TimelineCaptionItem(
                    item_id=f"{output_type}-day-{current_scene.day_id}",
                    start_frame=item.timeline_start_frame,
                    duration_frames=min(item.duration_frames, fps * 2),
                    text=day.title,
                    style="title",
                    position="top",
                    animation="soft_fade",
                    grounding=f"Observed capture-date boundary: {day.date or day.title}",
                )
            )
    return EditTimeline(
        timeline_id=f"{graph.project}-{output_type}",
        project=graph.project,
        output_type=output_type,  # type: ignore[arg-type]
        width=1080 if output_type == "short" else 1920,
        height=1920 if output_type == "short" else 1080,
        target_duration_sec=target,
        duration_frames=cursor,
        creative_direction=intent.raw_prompt,
        hypothesis_version=THEORY_VERSION,
        video_items=video_items,
        captions=captions,
    )


def critique_timeline(
    timeline: EditTimeline, graph: TripGraph, intent: EditingIntent
) -> CriticReport:
    by_scene = {scene.scene_id: scene for scene in graph.scenes}
    issues: list[CriticIssue] = []
    items = timeline.video_items
    for before, after in pairwise(items):
        a = by_scene[before.scene_id]
        b = by_scene[after.scene_id]
        if a.redundancy_group and a.redundancy_group == b.redundancy_group:
            issues.append(
                CriticIssue(
                    code="semantic_repetition",
                    message=f"{a.scene_id} and {b.scene_id} repeat the same observed content.",
                    scene_ids=[a.scene_id, b.scene_id],
                    repair=RepairAction(action="drop_item", item_id=after.item_id),
                )
            )
        if a.event_id != b.event_id and EditorialRole.establishing not in b.roles:
            issues.append(
                CriticIssue(
                    code="missing_establishing",
                    message=f"{b.scene_id} starts a new event without an establishing scene.",
                    scene_ids=[a.scene_id, b.scene_id],
                    repair=RepairAction(
                        action="insert_scene",
                        scene_id=next(
                            (
                                scene.scene_id
                                for scene in graph.scenes
                                if scene.event_id == b.event_id
                                and EditorialRole.establishing in scene.roles
                            ),
                            b.scene_id,
                        ),
                        target_frame=after.timeline_start_frame,
                    ),
                )
            )
        if a.day_id != b.day_id:
            has_marker = any(
                caption.start_frame == after.timeline_start_frame
                for caption in timeline.captions
            )
            if not has_marker and EditorialRole.establishing not in b.roles:
                issues.append(
                    CriticIssue(
                        code="unexplained_time_change",
                        message=f"{a.day_id} changes to {b.day_id} without a visible time cue.",
                        scene_ids=[a.scene_id, b.scene_id],
                        repair=RepairAction(
                            action="add_time_marker",
                            item_id=after.item_id,
                            target_frame=after.timeline_start_frame,
                        ),
                    )
                )
    if intent.pacing == "calm":
        run: list[TimelineVideoItem] = []
        for item in items:
            run = [*run, item] if item.duration_frames < timeline.fps * 2 else []
            if len(run) >= 3:
                issues.append(
                    CriticIssue(
                        code="intent_pacing_conflict",
                        message="Calm pacing was requested, but three sub-two-second cuts are consecutive.",
                        scene_ids=[entry.scene_id for entry in run[-3:]],
                        repair=RepairAction(
                            action="adjust_duration",
                            item_id=run[-2].item_id,
                            duration_frames=timeline.fps * 3,
                        ),
                    )
                )
                break
    for item in items:
        if item.effect.effect_id != "cut" and not item.effect.reason.strip():
            issues.append(
                CriticIssue(
                    code="unsupported_effect",
                    message=f"{item.item_id} has an effect without a content or intent reason.",
                    scene_ids=[item.scene_id],
                    repair=RepairAction(action="remove_effect", item_id=item.item_id),
                )
            )
    return CriticReport(
        timeline_id=timeline.timeline_id,
        issues=issues,
        passed=not issues,
    )


def timeline_to_edl(timeline: EditTimeline, *, music_id: str | None = None) -> EDL:
    effect_ids = {
        "cut": "clean_cut",
        "micro_push_in": "micro_push_in",
        "micro_pull_out": "micro_pull_out",
        "reaction_punch_in": "reaction_punch_in",
        "soft_reveal": "soft_reveal",
        "ambient_outro": "ambient_outro",
    }
    clips = [
        TimelineClip(
            order=index,
            segment_id=item.segment_id,
            source_file=item.source_file,
            in_sec=item.source_in_ms / 1000,
            out_sec=item.source_out_ms / 1000,
            transition="cut",
            role=item.roles[0].value,
            fit_mode="subject_aware_cover",
            focus_x=item.focus_x,
            focus_y=item.focus_y,
            motion="static",
            motion_strength=0,
            crop_confidence=item.crop_confidence,
            entry_effect="clean_cut",
            primary_effect=effect_ids[item.effect.effect_id],
            exit_effect="clean_cut",
            effect_reason=item.effect.reason,
            selection_reasons=[item.decision_reason],
        )
        for index, item in enumerate(timeline.video_items, start=1)
    ]
    captions: list[Caption] = []
    for caption in timeline.captions:
        host = next(
            (
                item
                for item in timeline.video_items
                if item.timeline_start_frame
                <= caption.start_frame
                < item.timeline_start_frame + item.duration_frames
            ),
            None,
        )
        if not host:
            continue
        start = (caption.start_frame - host.timeline_start_frame) / timeline.fps
        end = min(
            host.duration_frames / timeline.fps,
            start + caption.duration_frames / timeline.fps,
        )
        captions.append(
            Caption(
                segment_id=host.segment_id,
                text=caption.text,
                style=caption.style,
                position=caption.position,
                start_offset_sec=start,
                end_offset_sec=end,
                grounding=caption.grounding,
            )
        )
    return EDL(
        project=timeline.project,
        version="2.0-adapter",
        generator="ai",
        canvas=Canvas(width=timeline.width, height=timeline.height),
        frame=Frame(
            aspect="9:16" if timeline.output_type == "short" else "16:9",
            width=timeline.width,
            height=timeline.height,
            y_offset=0,
        ),
        aesthetic=Aesthetic(lut="", grain=0, bloom=0),
        audio=Audio(
            bgm_id=music_id or "",
            start_sec=0,
            volume=0.35 if music_id else 0,
            enabled=bool(music_id),
        ),
        timeline=clips,
        captions=captions,
        signature=Signature(enabled=False, text="", duration=0),
        style_preset="clean_vlog",
        creative_execution_version=THEORY_VERSION,
    )


def run_editorial_intelligence(
    work_root: Path,
    project: str,
    *,
    user_prompt: str = "",
) -> EditorialPackage:
    project_dir = work_root / project
    graph = build_trip_graph(project_dir, project)
    intent = parse_editing_intent(user_prompt)
    long_highlights = select_highlights(graph, intent, "long")
    short_highlights = select_highlights(graph, intent, "short")
    long_timeline = build_timeline(graph, intent, long_highlights, "long")
    short_timeline = build_timeline(graph, intent, short_highlights, "short")
    package = EditorialPackage(
        project=project,
        trip_graph=graph,
        editing_intent=intent,
        highlights=[*long_highlights, *short_highlights],
        long_timeline=long_timeline,
        short_timeline=short_timeline,
        long_critic=critique_timeline(long_timeline, graph, intent),
        short_critic=critique_timeline(short_timeline, graph, intent),
    )
    artifacts = {
        "trip_graph.json": graph,
        "editing_intent.json": intent,
        "highlight_decisions.json": package.highlights,
        "timeline_long.json": long_timeline,
        "timeline_short.json": short_timeline,
        "critic_long.json": package.long_critic,
        "critic_short.json": package.short_critic,
        "editorial_package.json": package,
        "edl_long.json": timeline_to_edl(long_timeline),
        "edl_short.json": timeline_to_edl(short_timeline),
        "edl_ai.json": timeline_to_edl(short_timeline),
    }
    for name, artifact in artifacts.items():
        value = (
            [item.model_dump(mode="json") for item in artifact]
            if isinstance(artifact, list)
            else artifact.model_dump(mode="json")
        )
        (project_dir / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return package
