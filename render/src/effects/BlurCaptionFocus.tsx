import React from 'react';
import {interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import type {EffectProps} from './registry';

export const BlurCaptionFocus: React.FC<EffectProps> = ({children, durationInFrames}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const ramp = Math.max(4, Math.round(fps * 0.18));
  const blur = interpolate(frame, [0, ramp, Math.max(ramp + 1, durationInFrames - ramp), durationInFrames], [0, 2.2, 2.2, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return <div style={{width: '100%', height: '100%', filter: `blur(${blur}px)`, transform: 'scale(1.015)'}}>{children}</div>;
};
