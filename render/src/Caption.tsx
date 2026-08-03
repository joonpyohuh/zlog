import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {FONT_BODY, FONT_DISPLAY} from './loadFonts';
import type {Caption as CaptionData, Frame} from './types';

/**
 * Vlog-style burned-in captions, positioned relative to the 4:3 picture
 * area (not the letterboxed canvas):
 *  - title:       big Y2K display type with a chromatic split shadow,
 *                 scale-pop entrance — the film's opening card
 *  - subtitle:    Korean vlog box caption (white bold on a dark pill),
 *                 slides up with a spring
 *  - lower_third: smaller, quieter pill for asides / the closing line
 */
export const CaptionOverlay: React.FC<{caption: CaptionData; frame: Frame}> = ({
  caption,
  frame,
}) => {
  const current = useCurrentFrame();
  const {fps, durationInFrames, width} = useVideoConfig();

  const pop = spring({frame: current, fps, config: {damping: 200, stiffness: 240}});
  const fadeFrames = Math.max(1, Math.round(fps * 0.2));
  const fadeOut =
    durationInFrames <= fadeFrames + 1
      ? 1
      : interpolate(current, [durationInFrames - fadeFrames, durationInFrames], [1, 0], {
          extrapolateLeft: 'clamp',
          extrapolateRight: 'clamp',
        });
  const opacity = Math.min(pop, 1) * fadeOut;

  const positionStyle: React.CSSProperties =
    caption.position === 'top'
      ? {justifyContent: 'flex-start', paddingTop: frame.height * 0.12}
      : caption.position === 'center'
        ? {justifyContent: 'center'}
        : {justifyContent: 'flex-end', paddingBottom: frame.height * 0.09};

  let inner: React.ReactNode;
  if (caption.style === 'title') {
    const scale = interpolate(pop, [0, 1], [0.92, 1]);
    inner = (
      <div
        style={{
          transform: `scale(${scale})`,
          color: 'white',
          fontFamily: caption.font === 'display' ? FONT_DISPLAY : FONT_BODY,
          fontSize: 68,
          fontWeight: 700,
          letterSpacing: 6,
          textAlign: 'center',
          lineHeight: 1.2,
          maxWidth: '86%',
          whiteSpace: 'pre-wrap',
          textShadow:
            '3px 0 rgba(255,60,90,0.45), -3px 0 rgba(60,220,255,0.45), 0 4px 24px rgba(0,0,0,0.6)',
        }}
      >
        {caption.text}
        <div
          style={{
            marginTop: 18,
            height: 3,
            width: '38%',
            marginLeft: 'auto',
            marginRight: 'auto',
            backgroundColor: 'rgba(255,255,255,0.85)',
            boxShadow: '0 0 12px rgba(255,255,255,0.5)',
          }}
        />
      </div>
    );
  } else {
    const lift = interpolate(pop, [0, 1], [18, 0]);
    const small = caption.style === 'lower_third';
    inner = (
      <div
        style={{
          transform: `translateY(${lift}px)`,
          color: 'white',
          fontFamily: FONT_BODY,
          fontSize: small ? 32 : 40,
          fontWeight: 700,
          letterSpacing: 0.5,
          textAlign: 'center',
          lineHeight: 1.35,
          maxWidth: '84%',
          whiteSpace: 'pre-wrap',
          backgroundColor: small ? 'rgba(0,0,0,0.45)' : 'rgba(0,0,0,0.58)',
          borderRadius: 16,
          padding: small ? '10px 26px' : '14px 32px',
          boxShadow: '0 4px 18px rgba(0,0,0,0.35)',
        }}
      >
        {caption.text}
      </div>
    );
  }

  return (
    <div
      style={{
        position: 'absolute',
        top: frame.y_offset,
        left: (width - frame.width) / 2,
        width: frame.width,
        height: frame.height,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        pointerEvents: 'none',
        opacity,
        ...positionStyle,
      }}
    >
      {inner}
    </div>
  );
};
