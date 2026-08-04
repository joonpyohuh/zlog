import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import type {EffectProps} from './registry';

export const ReactionPunchIn: React.FC<EffectProps> = ({children}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const end = Math.max(4, Math.round(fps * 0.28));
  const scale = interpolate(frame, [0, end * 0.45, end], [1, 1.075, 1.035], {extrapolateRight: 'clamp'});
  return <div style={{width: '100%', height: '100%', transform: `scale(${scale})`}}>{children}</div>;
};
