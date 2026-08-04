/**
 * Mirrors pipeline/ai/schemas.py — keep enums/fields in sync when contracts change.
 * Not yet consumed by the Remotion/render path (PROMPT 1 contracts only).
 */

export type MediaType = 'image' | 'video' | 'still_video';
export type ShotType = 'wide' | 'medium' | 'close' | 'detail' | 'unknown';
export type SceneKind =
  | 'indoor'
  | 'outdoor'
  | 'transit'
  | 'food'
  | 'portrait'
  | 'activity'
  | 'other';
export type Mood =
  | 'bright'
  | 'calm'
  | 'lively'
  | 'moody'
  | 'nostalgic'
  | 'romantic'
  | 'energetic';
export type StylePreset =
  | 'clean_vlog'
  | 'y2k_camcorder'
  | 'y2k_4x3_letterbox'
  | 'vertical_full'
  | 'cinematic_16x9'
  | 'soft_vlog'
  | 'punchy_short';
export type TargetPlatform =
  | 'youtube'
  | 'youtube_shorts'
  | 'instagram_reels'
  | 'tiktok'
  | 'generic';
export type CaptionMode = 'none' | 'sparse' | 'dense' | 'hook_only';
export type ClipRole =
  | 'hook'
  | 'orientation'
  | 'development'
  | 'zlog_moment'
  | 'release'
  | 'resonance'
  | 'opening'
  | 'body'
  | 'peak'
  | 'closing'
  | 'bridge';
export type VideoPurpose =
  | 'daily_vlog'
  | 'travel_vlog'
  | 'comedy_vlog'
  | 'emotional_vlog'
  | 'product_brand'
  | 'generic';
export type CaptionStrategy = 'none' | 'sparse' | 'contextual' | 'resonance';
export type AudioStrategy = {
  preserve_source_audio: boolean;
  bgm_duck: boolean;
  reason: string;
};
export type EffectStrategy = {
  effect: string;
  reason: string;
};
export type FitMode =
  | 'cover'
  | 'contain'
  | 'smart_crop'
  | 'subject_aware_cover'
  | 'blurred_background_contain';
export type MotionKind =
  | 'none'
  | 'ken_burns_in'
  | 'ken_burns_out'
  | 'pan_left'
  | 'pan_right'
  | 'static';
export type TransitionKind = 'cut' | 'flash' | 'dissolve';
export type PreferredMoment = 'start' | 'middle' | 'end' | 'peak_action';
export type ProviderName = 'anthropic' | 'openai';

export type AssetAnalysis = {
  asset_id: string;
  segment_id: string;
  source_file: string;
  media_type: MediaType;
  upload_index: number;
  capture_time: string | null;
  evidence_frame_ids: string[];
  subjects: string[];
  scene: SceneKind;
  shot_type: ShotType;
  action_progression: string;
  mood: Mood;
  technical_quality: number;
  aesthetic_value: number;
  emotional_value: number;
  narrative_value: number;
  hook_potential: number;
  motion_quality: number;
  novelty: number;
  redundancy_group: string | null;
  focus_x: number;
  focus_y: number;
  focus_width: number;
  focus_height: number;
  crop_confidence: number;
  visually_grounded_facts: string[];
  uncertainty: number;
  analysis_provider: ProviderName;
  analysis_model: string;
};

export type PlannedClip = {
  segment_id: string;
  role: ClipRole;
  evidence_frame_ids: string[];
  preferred_moment: PreferredMoment;
  target_duration_sec: number;
  fit_mode: FitMode;
  focus_x: number;
  focus_y: number;
  motion: MotionKind;
  motion_strength: number;
  transition: TransitionKind;
  caption: string;
  caption_grounding: string;
  overlay: string | null;
  reuse_reason: string | null;
  selection_reasons?: string[];
  cut_reason?: string;
  caption_strategy?: CaptionStrategy;
  caption_reason?: string;
  audio_strategy?: AudioStrategy;
  effect_strategy?: EffectStrategy;
};

export type StoryPlan = {
  user_intent: string;
  concept: string;
  tone: Mood;
  target_platform: TargetPlatform;
  target_duration_sec: number;
  style_preset: StylePreset;
  video_purpose?: VideoPurpose;
  hook_segment_id: string;
  ending_segment_id: string;
  selected_segment_ids: string[];
  narrative_arc: string[];
  caption_mode: CaptionMode;
  allow_asset_reuse: boolean;
  music_requirements: string;
  reasoning_summary: string;
  confidence: number;
  provider: ProviderName;
  model: string;
};

export type TimelinePlan = {
  project: string;
  story_plan_version: string;
  clips: PlannedClip[];
  total_target_duration_sec: number;
  style_preset: StylePreset;
};

export type EvaluationFailure = {
  code: string;
  message: string;
  segment_id: string | null;
};

export type RecommendedChange = {
  action:
    | 'drop_segment'
    | 'add_segment'
    | 'reorder'
    | 'rewrite_caption'
    | 'change_style'
    | 'shorten'
    | 'lengthen'
    | 'other';
  segment_id: string | null;
  detail: string;
};

export type CreativeQualityScores = {
  narrative_coherence: number;
  hook_strength: number;
  zlog_moment_strength: number;
  flow_naturalness: number;
  effect_relevance: number;
  effect_restraint: number;
  caption_restraint: number;
  natural_audio_preservation: number;
  human_imperfection_value: number;
  opening_ending_resonance: number;
  template_visibility: number;
  emotional_aftertaste: number;
};

export type PlanEvaluation = {
  overall_score: number;
  narrative_coherence: number;
  hook_strength: number;
  redundancy_score: number;
  chronology_score: number;
  caption_grounding: number;
  prompt_leakage: number;
  crop_safety: number;
  visual_variety: number;
  duration_suitability: number;
  creative?: CreativeQualityScores;
  failures: EvaluationFailure[];
  recommended_changes: RecommendedChange[];
  requires_revision: boolean;
  evaluator_provider: ProviderName;
  evaluator_model: string;
};

export type CallUsage = {
  provider: ProviderName;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_tokens: number;
  latency_ms: number;
  retry_count: number;
  estimated_cost_usd: number;
  operation: string;
  ok: boolean;
  error: string | null;
};
