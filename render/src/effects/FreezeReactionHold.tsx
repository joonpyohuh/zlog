import React from 'react';
import {AbsoluteFill, Freeze, Sequence, useVideoConfig} from 'remotion';
import type {EffectProps} from './registry';

export const FreezeReactionHold: React.FC<EffectProps> = ({children, durationInFrames}) => {
  const {fps} = useVideoConfig();
  const holdFrames = Math.min(Math.round(fps * 0.3), Math.max(1, Math.floor(durationInFrames / 4)));
  const holdAt = Math.max(1, Math.floor(durationInFrames * 0.56));
  return <>{children}<Sequence from={holdAt} durationInFrames={holdFrames}><AbsoluteFill><Freeze frame={holdAt}>{children}</Freeze></AbsoluteFill></Sequence></>;
};
