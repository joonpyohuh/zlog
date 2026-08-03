import React from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import type {Aesthetic, Frame} from './types';

// Animated grain + scanlines + vignette confined to the 4:3 picture area.
// The grain tile is a static SVG turbulence texture whose background
// position jumps deterministically each frame — reads as live sensor noise
// without any per-frame texture generation.

const GRAIN_TILE = 160;
const GRAIN_SVG = `<svg xmlns='http://www.w3.org/2000/svg' width='${GRAIN_TILE}' height='${GRAIN_TILE}'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix type='saturate' values='0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>`;
const GRAIN_URI = `data:image/svg+xml;utf8,${encodeURIComponent(GRAIN_SVG)}`;

export const FilmLook: React.FC<{frame: Frame; aesthetic: Aesthetic}> = ({frame, aesthetic}) => {
  const current = useCurrentFrame();
  const {width} = useVideoConfig();

  const jitterX = (current * 37) % GRAIN_TILE;
  const jitterY = (current * 53) % GRAIN_TILE;

  const rect: React.CSSProperties = {
    position: 'absolute',
    top: frame.y_offset,
    left: (width - frame.width) / 2,
    width: frame.width,
    height: frame.height,
    pointerEvents: 'none',
  };

  return (
    <>
      {/* grain */}
      <div
        style={{
          ...rect,
          backgroundImage: `url("${GRAIN_URI}")`,
          backgroundPosition: `${jitterX}px ${jitterY}px`,
          opacity: Math.min(0.5, aesthetic.grain),
          mixBlendMode: 'overlay',
        }}
      />
      {/* scanlines */}
      <div
        style={{
          ...rect,
          backgroundImage:
            'repeating-linear-gradient(to bottom, rgba(0,0,0,0.5) 0px, rgba(0,0,0,0.5) 1px, transparent 1px, transparent 4px)',
          opacity: 0.10,
        }}
      />
      {/* vignette */}
      <div
        style={{
          ...rect,
          background:
            'radial-gradient(ellipse at center, transparent 52%, rgba(0,10,25,0.38) 100%)',
        }}
      />
    </>
  );
};
