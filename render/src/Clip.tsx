import React from 'react';
import {
  AbsoluteFill,
  Easing,
  interpolate,
  OffthreadVideo,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import type {Frame, ResolvedTimelineClip} from './types';

// Ken Burns pan directions cycled by clip order — deterministic, so the
// same EDL always renders the same film. Values are fractions of the
// frame size travelled over the whole clip.
const PAN_DIRS: Array<[number, number]> = [
  [1, 0.4],
  [-1, -0.3],
  [0.5, -0.6],
  [-0.7, 0.5],
];

/**
 * One EDL timeline clip: 4:3 center-crop (object-fit: cover handles any
 * source aspect, 9:16 or 16:9 alike) inside the fixed 4:3 frame, letterboxed
 * on a black canvas. Trimming to [in_sec, out_sec) uses Remotion's
 * negative-offset-Sequence pattern rather than startFrom/endAt so it keeps
 * working across Remotion API versions.
 *
 * Every clip gets deterministic motion so stills read as footage, not a
 * slideshow: a slow Ken Burns zoom/pan over the clip's own duration plus a
 * short punch-in "hit" on the cut itself (lands on the beat, since cuts are
 * beat-snapped upstream).
 */
export const Clip: React.FC<{
  clip: ResolvedTimelineClip;
  frame: Frame;
  clipDurationInFrames: number;
}> = ({clip, frame, clipDurationInFrames}) => {
  const {width, fps} = useVideoConfig();
  const currentFrame = useCurrentFrame();
  const startFromFrames = Math.round(clip.in_sec * fps);

  const progress = Math.min(1, currentFrame / Math.max(1, clipDurationInFrames));
  const zoomIn = clip.order % 2 === 1;
  const drift = interpolate(progress, [0, 1], zoomIn ? [1.06, 1.14] : [1.14, 1.06]);
  const [dx, dy] = PAN_DIRS[clip.order % PAN_DIRS.length];
  const panX = interpolate(progress, [0, 1], [0, dx * frame.width * 0.02]);
  const panY = interpolate(progress, [0, 1], [0, dy * frame.height * 0.02]);
  const punch = interpolate(currentFrame, [0, Math.min(7, clipDurationInFrames)], [1.05, 1], {
    extrapolateRight: 'clamp',
    easing: Easing.out(Easing.quad),
  });

  return (
    <AbsoluteFill style={{backgroundColor: 'black'}}>
      <div
        style={{
          position: 'absolute',
          top: frame.y_offset,
          left: (width - frame.width) / 2,
          width: frame.width,
          height: frame.height,
          overflow: 'hidden',
        }}
      >
        <Sequence from={-startFromFrames}>
          <OffthreadVideo
            src={staticFile(clip.src)}
            style={{
              width: '100%',
              height: '100%',
              objectFit: 'cover',
              transform: `scale(${drift * punch}) translate(${panX}px, ${panY}px)`,
            }}
          />
        </Sequence>
      </div>
    </AbsoluteFill>
  );
};
