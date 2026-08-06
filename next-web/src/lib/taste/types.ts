// Mirrors the pydantic models in pipeline/taste_loop/. Field names and casing
// match the JSON on disk exactly so a round file can be parsed with no
// transformation. Keep these in step with store.py / variants.py.

export const TASTE_AXES = [
  'avg_cut_duration',
  'caption_frequency',
  'push_in_strength',
  'non_hard_cut_ratio',
] as const;

export type TasteAxis = (typeof TASTE_AXES)[number];

export type TimelineClip = {
  order: number;
  segment_id: string;
  source_file: string;
  in_sec: number;
  out_sec: number;
  transition: 'cut' | 'flash';
  role?: string | null;
  motion?: string;
  motion_strength?: number;
  fit_mode?: string;
};

export type Edl = {
  project: string;
  timeline: TimelineClip[];
  captions?: {segment_id: string; text: string; grounding?: string}[];
  max_captions?: number;
  signature?: {enabled: boolean; text: string; duration: number};
};

export type EditTimeline = {
  timeline_id: string;
  round_id: string;
  media_set_id: string;
  project: string;
  axis: TasteAxis;
  /** Never shown in the comparison UI — see the note in CompareBoard. */
  axis_value: number;
  variant_index: number;
  edl: Edl;
  created_at: string;
};

export type RenderedVariant = {
  timeline_id: string;
  variant_index: number;
  axis: TasteAxis;
  axis_value: number;
  video_path: string | null;
  ok: boolean;
  error: string | null;
  attempts?: number;
  render_seconds: number;
};

export type ComparisonRound = {
  id: string;
  media_set_id: string;
  axis: TasteAxis;
  candidates: EditTimeline[];
  renders: RenderedVariant[];
  winner_id: string | null;
  resolved: boolean;
  rejected_all: boolean;
  style_profile_version: number | null;
  created_at: string;
  resolved_at: string | null;
};

export type ClipFeedback = {
  id: string;
  timeline_id: string;
  timestamp_sec: number;
  clip_id: string | null;
  shot_type: string | null;
  active_presets: string[];
  active_params: Record<string, unknown>;
  caption_active: boolean;
  verdict: 'good' | 'bad';
  comment: string | null;
  created_at: string;
};

/**
 * What the comparison screen is allowed to know about a variant.
 *
 * axis_value is deliberately absent: if the number reaches the page it will
 * eventually reach the eye, and then the pick records a preference about
 * digits instead of about video. The number stays server-side and in the log.
 */
export type BlindVariant = {
  timeline_id: string;
  /** Display label only — A/B/C/D, carrying no information about the setting. */
  label: string;
  videoUrl: string | null;
  ok: boolean;
  error: string | null;
};

export const VARIANT_LABELS = ['A', 'B', 'C', 'D'] as const;

export function toBlindVariants(round: ComparisonRound): BlindVariant[] {
  return round.candidates.map((candidate, index) => {
    const render = round.renders.find((r) => r.timeline_id === candidate.timeline_id);
    return {
      timeline_id: candidate.timeline_id,
      label: VARIANT_LABELS[index] ?? String(index + 1),
      videoUrl: render?.ok ? `/api/taste/video/${candidate.timeline_id}` : null,
      ok: Boolean(render?.ok),
      error: render?.ok ? null : (render?.error ?? 'not rendered'),
    };
  });
}
