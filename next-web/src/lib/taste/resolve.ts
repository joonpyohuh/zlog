// Mirror of pipeline/taste_loop/feedback.py::resolve_position.
//
// The reviewer supplies a moment and a good/bad judgement. Everything about
// what was on screen at that moment is read back out of the timeline here —
// never typed by a person, never guessed. A value that cannot be read stays
// null, because a plausible-looking guess would corrupt exactly the dataset
// this exists to collect.

import type {Edl, TimelineClip} from './types';

export type ResolvedPosition = {
  clip_id: string | null;
  clip_order: number | null;
  shot_type: string | null;
  active_presets: string[];
  active_params: Record<string, unknown>;
  caption_active: boolean;
  clip_local_offset_sec: number | null;
};

function clipWindows(edl: Edl): {start: number; end: number; clip: TimelineClip}[] {
  let cursor = 0;
  return edl.timeline.map((clip) => {
    const duration = Math.max(0, clip.out_sec - clip.in_sec);
    const window = {start: cursor, end: cursor + duration, clip};
    cursor += duration;
    return window;
  });
}

function captionActiveAt(edl: Edl, clip: TimelineClip, localOffset: number): boolean {
  // Same gate the renderer applies (ZlogFilm.tsx): grounded + non-empty text,
  // capped at max_captions. Recording what the EDL lists rather than what was
  // visible would make the flag disagree with the video.
  const renderable = (edl.captions ?? []).filter(
    (c) => (c.grounding ?? '').trim().length > 0 && (c.text ?? '').trim().length > 0,
  );
  const cap = Math.max(0, Math.round(edl.max_captions ?? 3));
  const clipDuration = Math.max(0, clip.out_sec - clip.in_sec);

  return renderable.slice(0, cap).some((caption) => {
    if (caption.segment_id !== clip.segment_id) return false;
    const withOffsets = caption as typeof caption & {
      start_offset_sec?: number;
      end_offset_sec?: number | null;
    };
    const start = withOffsets.start_offset_sec ?? 0;
    const end = withOffsets.end_offset_sec ?? clipDuration;
    return start <= localOffset && localOffset <= end;
  });
}

export function resolvePosition(edl: Edl, timestampSec: number): ResolvedPosition {
  const empty: ResolvedPosition = {
    clip_id: null,
    clip_order: null,
    shot_type: null,
    active_presets: [],
    active_params: {},
    caption_active: false,
    clip_local_offset_sec: null,
  };
  if (!Number.isFinite(timestampSec) || timestampSec < 0) return empty;

  // A mark past the last clip lands on the ending credit, where no clip is
  // playing. That is a real position and stays unresolved rather than being
  // snapped onto the nearest clip.
  const match = clipWindows(edl).find((w) => timestampSec >= w.start && timestampSec < w.end);
  if (!match) return empty;

  const {clip, start} = match;
  const localOffset = Number((timestampSec - start).toFixed(3));
  const captionActive = captionActiveAt(edl, clip, localOffset);

  const presets: string[] = [];
  if (clip.transition && clip.transition !== 'cut') presets.push(`transition:${clip.transition}`);
  if (clip.motion && clip.motion !== 'none' && clip.motion !== 'static') {
    presets.push(`motion:${clip.motion}`);
  }
  if (clip.fit_mode) presets.push(`fit_mode:${clip.fit_mode}`);

  return {
    clip_id: clip.segment_id,
    clip_order: clip.order,
    // shot_type is an analyze_assets concept, not an EDL field; the planner's
    // role is the closest thing the timeline actually carries.
    shot_type: clip.role ?? null,
    active_presets: presets,
    active_params: {
      clip_order: clip.order,
      segment_id: clip.segment_id,
      source_file: clip.source_file,
      in_sec: clip.in_sec,
      out_sec: clip.out_sec,
      clip_duration_sec: Number((clip.out_sec - clip.in_sec).toFixed(3)),
      transition: clip.transition,
      motion: clip.motion ?? null,
      motion_strength: clip.motion_strength ?? null,
      fit_mode: clip.fit_mode ?? null,
      role: clip.role ?? null,
      caption_active: captionActive,
    },
    caption_active: captionActive,
    clip_local_offset_sec: localOffset,
  };
}
