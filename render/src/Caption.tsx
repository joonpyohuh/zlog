import React from 'react';
import {interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {FONT_BODY, FONT_DISPLAY} from './loadFonts';
import type {Caption as CaptionData, Frame} from './types';

/**
 * Sparse vlog captions with vertical safe-area padding.
 * Captions without grounding metadata are not rendered (PROMPT 7).
 */
export const CaptionOverlay: React.FC<{caption: CaptionData; frame: Frame}> = ({
  caption,
  frame,
}) => {
  const current = useCurrentFrame();
  const {fps, durationInFrames, width, height} = useVideoConfig();

  const grounding = (caption.grounding || '').trim();
  if (!grounding) {
    return null;
  }

  // Reject raw edit-brief leakage heuristics (common Korean/English phrases).
  const text = (caption.text || '').trim();
  if (!text || looksLikeEditBrief(text)) {
    return null;
  }

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

  // Safe area ~8% of the shorter canvas edge for vertical shorts.
  const safe = Math.round(Math.min(width, height) * 0.08);

  const positionStyle: React.CSSProperties =
    caption.position === 'top'
      ? {justifyContent: 'flex-start', paddingTop: Math.max(safe, frame.height * 0.1)}
      : caption.position === 'center'
        ? {justifyContent: 'center'}
        : {justifyContent: 'flex-end', paddingBottom: Math.max(safe, frame.height * 0.1)};

  let inner: React.ReactNode;
  if (caption.style === 'title') {
    const scale = interpolate(pop, [0, 1], [0.96, 1]);
    inner = (
      <div
        style={{
          transform: `scale(${scale})`,
          color: 'white',
          fontFamily: caption.font === 'display' ? FONT_DISPLAY : FONT_BODY,
          fontSize: 52,
          fontWeight: 700,
          letterSpacing: 2,
          textAlign: 'center',
          lineHeight: 1.25,
          maxWidth: '82%',
          whiteSpace: 'pre-wrap',
          textShadow: '0 2px 16px rgba(0,0,0,0.55)',
        }}
      >
        {text}
      </div>
    );
  } else {
    const lift = interpolate(pop, [0, 1], [12, 0]);
    const small = caption.style === 'lower_third';
    inner = (
      <div
        style={{
          transform: `translateY(${lift}px)`,
          color: 'white',
          fontFamily: FONT_BODY,
          fontSize: small ? 30 : 36,
          fontWeight: 650,
          letterSpacing: 0.3,
          textAlign: 'center',
          lineHeight: 1.35,
          maxWidth: '78%',
          whiteSpace: 'pre-wrap',
          backgroundColor: small ? 'rgba(0,0,0,0.4)' : 'rgba(0,0,0,0.5)',
          borderRadius: 14,
          padding: small ? '10px 22px' : '12px 28px',
          boxShadow: '0 4px 16px rgba(0,0,0,0.3)',
        }}
      >
        {text}
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
        paddingLeft: safe,
        paddingRight: safe,
        boxSizing: 'border-box',
        ...positionStyle,
      }}
    >
      {inner}
    </div>
  );
};

export function looksLikeEditBrief(text: string): boolean {
  const t = text.trim().toLowerCase();
  if (t.length > 48 && (t.includes('만들어') || t.includes('브이로그로'))) return true;
  if (t.includes('편집 지시') || t.includes('editing brief')) return true;
  if (/^(please |make me |만들어줘)/i.test(t)) return true;
  return false;
}
