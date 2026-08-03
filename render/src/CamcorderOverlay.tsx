import React from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';
import type {Frame} from './types';

// Classic CCD on-screen-display: blocky mono glyphs, amber date stamp,
// blinking REC dot, running timecode. All values derive from the current
// frame — fully deterministic.

const OSD_FONT = '"Courier New", "Space Grotesk", monospace';
const AMBER = '#ffb000';
const OSD_SHADOW = '0 0 6px rgba(0,0,0,0.8), 1px 1px 0 rgba(0,0,0,0.9)';

const pad = (n: number) => String(n).padStart(2, '0');

const Bracket: React.FC<{corner: 'tl' | 'tr' | 'bl' | 'br'}> = ({corner}) => {
  const size = 46;
  const thick = 4;
  const style: React.CSSProperties = {
    position: 'absolute',
    width: size,
    height: size,
    opacity: 0.55,
  };
  const edge = `${thick}px solid white`;
  if (corner === 'tl') Object.assign(style, {top: 24, left: 24, borderTop: edge, borderLeft: edge});
  if (corner === 'tr') Object.assign(style, {top: 24, right: 24, borderTop: edge, borderRight: edge});
  if (corner === 'bl') Object.assign(style, {bottom: 24, left: 24, borderBottom: edge, borderLeft: edge});
  if (corner === 'br') Object.assign(style, {bottom: 24, right: 24, borderBottom: edge, borderRight: edge});
  return <div style={style} />;
};

export const CamcorderOverlay: React.FC<{frame: Frame; shotDate?: string}> = ({
  frame,
  shotDate,
}) => {
  const current = useCurrentFrame();
  const {fps, width} = useVideoConfig();

  const totalSec = current / fps;
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = Math.floor(totalSec % 60);
  const ff = Math.floor(current % fps);
  const timecode = `${pad(h)}:${pad(m)}:${pad(s)}.${pad(ff)}`;

  // REC dot blinks at ~1.2Hz like the real thing.
  const recOn = Math.floor(totalSec / 0.8) % 2 === 0;

  return (
    <div
      style={{
        position: 'absolute',
        top: frame.y_offset,
        left: (width - frame.width) / 2,
        width: frame.width,
        height: frame.height,
        pointerEvents: 'none',
        fontFamily: OSD_FONT,
        fontWeight: 700,
        color: 'white',
      }}
    >
      <Bracket corner="tl" />
      <Bracket corner="tr" />
      <Bracket corner="bl" />
      <Bracket corner="br" />

      {/* REC + blinking dot, top-left */}
      <div
        style={{
          position: 'absolute',
          top: 40,
          left: 90,
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          fontSize: 30,
          letterSpacing: 4,
          textShadow: OSD_SHADOW,
        }}
      >
        <div
          style={{
            width: 18,
            height: 18,
            borderRadius: '50%',
            backgroundColor: '#ff2b2b',
            opacity: recOn ? 1 : 0.15,
            boxShadow: recOn ? '0 0 10px rgba(255,43,43,0.9)' : 'none',
          }}
        />
        REC
      </div>

      {/* running timecode, top-right */}
      <div
        style={{
          position: 'absolute',
          top: 42,
          right: 90,
          fontSize: 28,
          letterSpacing: 2,
          textShadow: OSD_SHADOW,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {timecode}
      </div>

      {/* amber date stamp, bottom-left — the CCD signature */}
      <div
        style={{
          position: 'absolute',
          bottom: 38,
          left: 90,
          fontSize: 28,
          letterSpacing: 3,
          color: AMBER,
          textShadow: OSD_SHADOW,
        }}
      >
        {shotDate ?? 'PM 00:00'}
      </div>

      {/* AUTO + battery, bottom-right */}
      <div
        style={{
          position: 'absolute',
          bottom: 38,
          right: 90,
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          fontSize: 24,
          letterSpacing: 3,
          textShadow: OSD_SHADOW,
        }}
      >
        AUTO
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 2,
          }}
        >
          <div
            style={{
              width: 40,
              height: 20,
              border: '3px solid white',
              borderRadius: 2,
              padding: 2,
              display: 'flex',
              gap: 2,
            }}
          >
            <div style={{flex: 1, backgroundColor: 'white'}} />
            <div style={{flex: 1, backgroundColor: 'white'}} />
            <div style={{flex: 1, backgroundColor: 'rgba(255,255,255,0.25)'}} />
          </div>
          <div style={{width: 4, height: 10, backgroundColor: 'white'}} />
        </div>
      </div>
    </div>
  );
};
