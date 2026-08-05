import React from 'react';
import {
  AbsoluteFill,
  Freeze,
  interpolate,
  Sequence,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import type {EffectProps} from './registry';

export const FreezeReactionHold: React.FC<EffectProps> = ({children, durationInFrames}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const holdFrames = Math.min(Math.round(fps * 0.3), Math.max(1, Math.floor(durationInFrames / 4)));
  const holdAt = Math.max(1, Math.floor(durationInFrames * 0.56));
  const local = frame - holdAt;
  const pulse = interpolate(local, [0, Math.max(2, holdFrames * 0.45), holdFrames], [0.38, 0.08, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  return (
    <>
      {children}
      <Sequence from={holdAt} durationInFrames={holdFrames}>
        <AbsoluteFill
          style={{transform: 'scale(1.045)', filter: 'saturate(1.08) contrast(1.04)'}}
        >
          <Freeze frame={holdAt}>{children}</Freeze>
        </AbsoluteFill>
        <AbsoluteFill
          style={{
            border: '10px solid rgba(255,255,255,0.72)',
            boxSizing: 'border-box',
            opacity: pulse,
          }}
        />
        <AbsoluteFill style={{backgroundColor: 'white', opacity: pulse * 0.42}} />
      </Sequence>
    </>
  );
};
