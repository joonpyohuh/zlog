import React from 'react';
import {Easing, interpolate, useCurrentFrame} from 'remotion';
import type {EffectProps} from './registry';

export const MicroPush: React.FC<EffectProps> = ({children, effectId, durationInFrames}) => {
  const frame = useCurrentFrame();
  const progress = Easing.inOut(Easing.cubic)(Math.min(1, frame / Math.max(1, durationInFrames - 1)));
  const range = effectId === 'micro_pull_out' ? [1.035, 1] : [1, 1.035];
  return <div style={{width: '100%', height: '100%', transform: `scale(${interpolate(progress, [0, 1], range)})`}}>{children}</div>;
};
