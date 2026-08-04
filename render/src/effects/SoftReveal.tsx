import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import type {EffectProps} from './registry';

export const SoftReveal: React.FC<EffectProps> = ({children}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const end = Math.max(5, Math.round(fps * 0.38));
  const opacity = interpolate(frame, [0, end], [0.25, 1], {extrapolateRight: 'clamp'});
  const blur = interpolate(frame, [0, end], [7, 0], {extrapolateRight: 'clamp'});
  const scale = interpolate(frame, [0, end], [1.025, 1], {extrapolateRight: 'clamp'});
  return <div style={{width: '100%', height: '100%', opacity, filter: `blur(${blur}px)`, transform: `scale(${scale})`}}>{children}</div>;
};
