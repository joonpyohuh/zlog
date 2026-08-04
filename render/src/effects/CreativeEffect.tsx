import React from 'react';
import type {ResolvedTimelineClip} from '../types';
import {EFFECT_COMPONENTS, resolveEffectId} from './registry';

export const CreativeEffect: React.FC<{
  clip: ResolvedTimelineClip;
  durationInFrames: number;
  children: React.ReactNode;
}> = ({clip, durationInFrames, children}) => {
  const effects = [clip.entry_effect, clip.primary_effect, clip.exit_effect]
    .map(resolveEffectId)
    .filter((effect, index, all) => effect !== 'clean_cut' && all.indexOf(effect) === index);
  return effects.reduceRight<React.ReactNode>((nested, effectId) => {
    const Component = EFFECT_COMPONENTS[effectId];
    return <Component effectId={effectId} durationInFrames={durationInFrames}>{nested}</Component>;
  }, children);
};
