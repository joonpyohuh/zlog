import type React from 'react';
import type {EffectId} from '../types';
import {AmbientOutro} from './AmbientOutro';
import {BlurCaptionFocus} from './BlurCaptionFocus';
import {CleanCut} from './CleanCut';
import {FreezeReactionHold} from './FreezeReactionHold';
import {MicroPush} from './MicroPush';
import {ReactionPunchIn} from './ReactionPunchIn';
import {SoftReveal} from './SoftReveal';

export type ClipRole =
  | 'hook'
  | 'orientation'
  | 'development'
  | 'zlog_moment'
  | 'release'
  | 'resonance';

export type EffectDefinition = {
  id: EffectId;
  intensity: 'subtle' | 'medium' | 'strong';
  allowedRoles: ClipRole[];
  maxPerVideo: number;
  requiresCaption?: boolean;
  supportsStill: boolean;
  supportsVideo: boolean;
};

export type EffectProps = {
  children: React.ReactNode;
  effectId: EffectId;
  durationInFrames: number;
};

const ALL_ROLES: ClipRole[] = [
  'hook',
  'orientation',
  'development',
  'zlog_moment',
  'release',
  'resonance',
];

export const EFFECT_REGISTRY: Record<EffectId, EffectDefinition> = {
  clean_cut: {id: 'clean_cut', intensity: 'subtle', allowedRoles: ALL_ROLES, maxPerVideo: 99, supportsStill: true, supportsVideo: true},
  micro_push_in: {id: 'micro_push_in', intensity: 'subtle', allowedRoles: ALL_ROLES.filter((r) => r !== 'resonance'), maxPerVideo: 99, supportsStill: true, supportsVideo: true},
  micro_pull_out: {id: 'micro_pull_out', intensity: 'subtle', allowedRoles: ['release', 'resonance'], maxPerVideo: 99, supportsStill: true, supportsVideo: true},
  reaction_punch_in: {id: 'reaction_punch_in', intensity: 'strong', allowedRoles: ['hook', 'zlog_moment'], maxPerVideo: 1, supportsStill: true, supportsVideo: true},
  blur_caption_focus: {id: 'blur_caption_focus', intensity: 'medium', allowedRoles: ['orientation', 'development'], maxPerVideo: 1, requiresCaption: true, supportsStill: true, supportsVideo: true},
  freeze_reaction_hold: {id: 'freeze_reaction_hold', intensity: 'strong', allowedRoles: ['zlog_moment'], maxPerVideo: 1, supportsStill: true, supportsVideo: true},
  soft_reveal: {id: 'soft_reveal', intensity: 'medium', allowedRoles: ['hook', 'orientation'], maxPerVideo: 1, supportsStill: true, supportsVideo: true},
  ambient_outro: {id: 'ambient_outro', intensity: 'subtle', allowedRoles: ['resonance'], maxPerVideo: 1, supportsStill: true, supportsVideo: true},
};

export function resolveEffectId(value: string | null | undefined): EffectId {
  return value && value in EFFECT_REGISTRY ? (value as EffectId) : 'clean_cut';
}

export function effectProgress(frame: number, start: number, duration: number): number {
  return Math.max(0, Math.min(1, (frame - start) / Math.max(1, duration)));
}

export const EFFECT_COMPONENTS: Record<EffectId, React.FC<EffectProps>> = {
  clean_cut: CleanCut,
  micro_push_in: MicroPush,
  micro_pull_out: MicroPush,
  reaction_punch_in: ReactionPunchIn,
  blur_caption_focus: BlurCaptionFocus,
  freeze_reaction_hold: FreezeReactionHold,
  soft_reveal: SoftReveal,
  ambient_outro: AmbientOutro,
};
