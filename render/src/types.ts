// Mirrors the EDL schema in pipeline/edl.py. Field names/casing match the
// JSON exactly (edl_baseline.json / edl_ai.json) so props can be loaded
// straight from disk with no transformation beyond what calculateMetadata
// in Root.tsx does to resolve asset paths.

export type Canvas = {
  width: number;
  height: number;
};

export type Frame = {
  aspect: string;
  width: number;
  height: number;
  y_offset: number;
};

export type Aesthetic = {
  lut: string;
  grain: number;
  bloom: number;
  scanlines?: boolean;
  camcorder_osd?: boolean;
  allow_flash?: boolean;
};

export type Audio = {
  bgm_id: string;
  start_sec: number;
  volume: number;
};

export type TimelineClip = {
  order: number;
  segment_id: string;
  source_file: string;
  in_sec: number;
  out_sec: number;
  // 'flash' = renderer burns a short white flash as this clip enters
  // (section boundaries). Timing still comes from the beat grid.
  transition: 'cut' | 'flash';
  role?: string | null;
  evidence_frame_ids?: string[];
  fit_mode?: string;
  focus_x?: number;
  focus_y?: number;
  motion?: string;
  motion_strength?: number;
  overlay?: string | null;
  reuse_reason?: string | null;
  crop_confidence?: number | null;
};

export type Caption = {
  segment_id: string;
  text: string;
  style: 'title' | 'subtitle' | 'lower_third';
  font: 'body' | 'display';
  position: 'top' | 'center' | 'bottom';
  start_offset_sec: number;
  end_offset_sec: number | null;
  /** Empty/missing → Remotion hides the caption (PROMPT 7). */
  grounding?: string;
};

export type Signature = {
  enabled: boolean;
  text: string;
  duration: number;
};

export type Edl = {
  project: string;
  version: string;
  generator: 'baseline' | 'ai';
  canvas: Canvas;
  frame: Frame;
  aesthetic: Aesthetic;
  audio: Audio;
  timeline: TimelineClip[];
  captions?: Caption[];
  signature: Signature;
  style_preset?: string;
  /**
   * How many grounded captions may reach the screen. Omitted → 3, the sparse
   * default every hand-authored EDL assumes. The taste loop raises it so its
   * caption-frequency variants render the count they logged.
   */
  max_captions?: number;
};

// Resolved-in-Node variants: resolve-props.mjs stages each clip's video and
// the BGM track into render/public/ and fills in the staticFile()-relative
// path here, before the props JSON ever reaches this bundle. The component
// tree just wraps `src` in staticFile() — it never touches the filesystem
// itself.

export type ResolvedTimelineClip = TimelineClip & {src: string};

export type ResolvedAudio = Audio & {src: string};

export type ResolvedEdl = Omit<Edl, 'timeline' | 'audio'> & {
  timeline: ResolvedTimelineClip[];
  audio: ResolvedAudio;
  // Stamped by resolve-props.mjs at render time (plain Node) so the
  // camcorder date OSD stays deterministic for a given render.
  shot_date?: string;
};

export type ZlogFilmProps = {
  edl: ResolvedEdl;
};
