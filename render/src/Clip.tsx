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
import {effectiveFitMode, objectFitForMode, objectPositionCss} from './style';
import type {Frame, ResolvedTimelineClip} from './types';

type MotionKind =
  | 'none'
  | 'static'
  | 'ken_burns_in'
  | 'ken_burns_out'
  | 'pan_left'
  | 'pan_right';

/**
 * Content-aware clip renderer (PROMPT 7).
 *
 * Motion comes from PlannedClip.motion / motion_strength + focus_x/y —
 * never from clip.order % N. Fit modes include subject-aware cover and
 * blurred-background contain when crop confidence is low.
 */
export const Clip: React.FC<{
  clip: ResolvedTimelineClip;
  frame: Frame;
  clipDurationInFrames: number;
}> = ({clip, frame, clipDurationInFrames}) => {
  const {width} = useVideoConfig();
  const currentFrame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const startFromFrames = Math.round(clip.in_sec * fps);

  const progress = Math.min(1, currentFrame / Math.max(1, clipDurationInFrames));
  // Smooth ease — focus drifts gently, never frame-to-frame jitter.
  const eased = Easing.inOut(Easing.cubic)(progress);

  const focusX = clamp01(clip.focus_x ?? 0.5);
  const focusY = clamp01(clip.focus_y ?? 0.45);
  const strength = clamp01(clip.motion_strength ?? 0.35);
  const motion = (clip.motion || 'ken_burns_in') as MotionKind;
  const fit = effectiveFitMode(clip.fit_mode, clip.crop_confidence);

  const {scale, tx, ty, origin} = motionTransform({
    motion,
    strength,
    eased,
    focusX,
    focusY,
    frameW: frame.width,
    frameH: frame.height,
  });

  const pictureLeft = (width - frame.width) / 2;
  const position = objectPositionCss(focusX, focusY);
  const videoStyle: React.CSSProperties = {
    width: '100%',
    height: '100%',
    objectFit: objectFitForMode(fit),
    objectPosition: position,
    transformOrigin: origin,
    transform: `translate(${tx}px, ${ty}px) scale(${scale})`,
  };

  return (
    <AbsoluteFill style={{backgroundColor: 'black'}}>
      <div
        style={{
          position: 'absolute',
          top: frame.y_offset,
          left: pictureLeft,
          width: frame.width,
          height: frame.height,
          overflow: 'hidden',
          backgroundColor: 'black',
        }}
      >
        {fit === 'blurred_background_contain' && (
          <Sequence from={-startFromFrames}>
            <OffthreadVideo
              src={staticFile(clip.src)}
              style={{
                position: 'absolute',
                inset: 0,
                width: '100%',
                height: '100%',
                objectFit: 'cover',
                objectPosition: position,
                filter: 'blur(28px) brightness(0.55) saturate(1.05)',
                transform: 'scale(1.12)',
              }}
            />
          </Sequence>
        )}
        <Sequence from={-startFromFrames}>
          <OffthreadVideo src={staticFile(clip.src)} style={videoStyle} />
        </Sequence>
      </div>
    </AbsoluteFill>
  );
};

function clamp01(v: number): number {
  return Math.max(0, Math.min(1, v));
}

function motionTransform(args: {
  motion: MotionKind;
  strength: number;
  eased: number;
  focusX: number;
  focusY: number;
  frameW: number;
  frameH: number;
}): {scale: number; tx: number; ty: number; origin: string} {
  const {motion, strength, eased, focusX, focusY, frameW, frameH} = args;
  const origin = `${focusX * 100}% ${focusY * 100}%`;

  // Already-moving footage / explicit static → no extra Ken Burns.
  if (motion === 'none' || motion === 'static' || strength < 0.05) {
    return {scale: 1, tx: 0, ty: 0, origin};
  }

  // Soft push-in toward subject (people / pets style).
  if (motion === 'ken_burns_in') {
    const s0 = 1.0;
    const s1 = 1.0 + 0.08 * strength;
    const scale = interpolate(eased, [0, 1], [s0, s1]);
    // Subtle settle toward focus (max ~1.2% of frame).
    const tx = interpolate(eased, [0, 1], [(0.5 - focusX) * frameW * 0.012, 0]);
    const ty = interpolate(eased, [0, 1], [(0.5 - focusY) * frameH * 0.012, 0]);
    return {scale, tx, ty, origin};
  }

  // Slow pull-out for landscapes.
  if (motion === 'ken_burns_out') {
    const s0 = 1.0 + 0.1 * strength;
    const s1 = 1.0;
    const scale = interpolate(eased, [0, 1], [s0, s1]);
    return {scale, tx: 0, ty: 0, origin};
  }

  // Slow horizontal pan for wide scenes.
  if (motion === 'pan_left' || motion === 'pan_right') {
    const dir = motion === 'pan_left' ? -1 : 1;
    const travel = frameW * 0.035 * strength * dir;
    const tx = interpolate(eased, [0, 1], [0, travel]);
    const scale = 1.04 + 0.02 * strength;
    return {scale, tx, ty: 0, origin};
  }

  return {scale: 1, tx: 0, ty: 0, origin};
}

/** Pure helper exported for unit tests (no Remotion runtime). */
export function __testMotionTransform(
  motion: string,
  strength: number,
  eased: number,
  focusX: number,
  focusY: number,
): {scale: number; tx: number; ty: number} {
  const r = motionTransform({
    motion: motion as MotionKind,
    strength,
    eased,
    focusX,
    focusY,
    frameW: 1080,
    frameH: 1920,
  });
  return {scale: r.scale, tx: r.tx, ty: r.ty};
}
