import React from 'react';
import {AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';
import {FONT_BODY, FONT_DISPLAY} from './loadFonts';

/**
 * Last `signature.duration` seconds: black screen, fade in, white
 * sans-serif "directed by zlog" centered, small tagline underneath.
 * (Fonts load at module import — see loadFonts.ts.)
 */
export const EndingCredit: React.FC<{text: string}> = ({text}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();

  const fadeInFrames = Math.round(fps * 0.5);
  const opacity = interpolate(frame, [0, fadeInFrames], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: 'black',
        justifyContent: 'center',
        alignItems: 'center',
      }}
    >
      <div
        style={{
          opacity,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 14,
        }}
      >
        <div
          style={{
            color: 'white',
            fontFamily: FONT_DISPLAY,
            fontSize: 64,
            fontWeight: 600,
            letterSpacing: 2,
          }}
        >
          {text}
        </div>
        <div
          style={{
            color: 'white',
            fontFamily: FONT_BODY,
            fontSize: 20,
            letterSpacing: 4,
            opacity: 0.7,
          }}
        >
          YOUR DAY, YOUR FILM
        </div>
      </div>
    </AbsoluteFill>
  );
};
