import React from 'react';
import {
  AbsoluteFill,
  Audio,
  interpolate,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import {CamcorderOverlay} from './CamcorderOverlay';
import {CaptionOverlay} from './Caption';
import {Clip} from './Clip';
import {EndingCredit} from './EndingCredit';
import {FilmLook} from './FilmLook';
import {ensureFontsLoaded} from './loadFonts';
import {resolveStyle} from './style';
import type {ZlogFilmProps} from './types';

const BGM_FADE_OUT_S = 1;
const FLASH_FRAMES = 5;

/** White pulse burned in where a clip enters with transition === 'flash'. */
const FlashIn: React.FC = () => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, FLASH_FRAMES], [0.85, 0], {
    extrapolateRight: 'clamp',
  });
  return <AbsoluteFill style={{backgroundColor: 'white', opacity}} />;
};

/** Root composition: timeline Sequences + style-gated overlays. */
export const ZlogFilm: React.FC<ZlogFilmProps> = ({edl}) => {
  const {fps, durationInFrames} = useVideoConfig();
  void ensureFontsLoaded();

  const style = resolveStyle(edl);

  const orderedClips = [...edl.timeline].sort((a, b) => a.order - b.order);
  let cursor = 0;
  const placedClips = orderedClips.map((clip) => {
    const clipDurationFrames = Math.max(1, Math.round((clip.out_sec - clip.in_sec) * fps));
    const from = cursor;
    cursor += clipDurationFrames;
    return {clip, from, durationInFrames: clipDurationFrames};
  });
  const clipsTotalFrames = cursor;

  const bySegment = new Map<string, (typeof placedClips)[number]>();
  for (const placed of placedClips) {
    if (!bySegment.has(placed.clip.segment_id)) {
      bySegment.set(placed.clip.segment_id, placed);
    }
  }

  // Sparse captions: keep at most 3 on screen timeline; require grounding.
  const groundedCaptions = (edl.captions ?? []).filter(
    (c) => (c.grounding || '').trim().length > 0 && (c.text || '').trim().length > 0,
  );
  const sparseCaptions = groundedCaptions.slice(0, 3);

  const placedCaptions = sparseCaptions.flatMap((caption, i) => {
    const host = bySegment.get(caption.segment_id);
    if (!host) return [];
    const clipDurSec = host.clip.out_sec - host.clip.in_sec;
    const startSec = Math.min(Math.max(0, caption.start_offset_sec), Math.max(0, clipDurSec - 1 / fps));
    const endSec =
      caption.end_offset_sec == null ? clipDurSec : Math.min(clipDurSec, caption.end_offset_sec);
    if (endSec <= startSec) return [];
    return [
      {
        key: `${caption.segment_id}-${i}`,
        caption,
        from: host.from + Math.round(startSec * fps),
        durationInFrames: Math.max(1, Math.round((endSec - startSec) * fps)),
      },
    ];
  });

  const signatureFrames =
    style.endingCredit && edl.signature.enabled ? Math.round(edl.signature.duration * fps) : 0;
  const fadeOutFrames = Math.round(BGM_FADE_OUT_S * fps);

  return (
    <AbsoluteFill style={{backgroundColor: 'black'}}>
      {placedClips.map(({clip, from, durationInFrames: clipDuration}) => (
        <Sequence
          key={`${clip.order}-${clip.segment_id}`}
          from={from}
          durationInFrames={clipDuration}
          name={`${clip.order}-${clip.segment_id}`}
        >
          <Clip clip={clip} frame={edl.frame} clipDurationInFrames={clipDuration} />
        </Sequence>
      ))}

      {style.filmLook && clipsTotalFrames > 0 && (
        <Sequence from={0} durationInFrames={clipsTotalFrames} name="film-look">
          <AbsoluteFill style={{pointerEvents: 'none'}}>
            <FilmLook frame={edl.frame} aesthetic={edl.aesthetic} />
          </AbsoluteFill>
        </Sequence>
      )}

      {style.camcorderOsd && clipsTotalFrames > 0 && (
        <Sequence from={0} durationInFrames={clipsTotalFrames} name="camcorder-osd">
          <AbsoluteFill style={{pointerEvents: 'none'}}>
            <CamcorderOverlay frame={edl.frame} shotDate={edl.shot_date} />
          </AbsoluteFill>
        </Sequence>
      )}

      {style.allowFlash &&
        placedClips
          .filter(({clip}) => clip.transition === 'flash')
          .map(({clip, from}) => (
            <Sequence
              key={`flash-${clip.order}`}
              from={from}
              durationInFrames={FLASH_FRAMES + 1}
              name={`flash-${clip.order}`}
            >
              <FlashIn />
            </Sequence>
          ))}

      {placedCaptions.map(({key, caption, from, durationInFrames: capDur}) => (
        <Sequence key={key} from={from} durationInFrames={capDur} name={`caption-${key}`}>
          <CaptionOverlay caption={caption} frame={edl.frame} />
        </Sequence>
      ))}

      {signatureFrames > 0 && (
        <Sequence
          from={durationInFrames - signatureFrames}
          durationInFrames={signatureFrames}
          name="signature"
        >
          <EndingCredit text={edl.signature.text} />
        </Sequence>
      )}

      {edl.audio.src && (
        <Audio
          src={staticFile(edl.audio.src)}
          startFrom={Math.round(edl.audio.start_sec * fps)}
          volume={(f) => {
            const fadeStart = durationInFrames - fadeOutFrames;
            if (f < fadeStart) return edl.audio.volume;
            const t = (f - fadeStart) / fadeOutFrames;
            return edl.audio.volume * Math.max(0, 1 - t);
          }}
        />
      )}
    </AbsoluteFill>
  );
};
