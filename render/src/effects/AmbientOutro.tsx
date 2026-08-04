import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import type {EffectProps} from './registry';

export const AmbientOutro: React.FC<EffectProps> = ({children, durationInFrames}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const start = Math.max(0, durationInFrames - Math.round(fps * 0.8));
  const opacity = interpolate(frame, [start, durationInFrames], [1, 0.88], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return <div style={{width: '100%', height: '100%', opacity}}>{children}</div>;
};
