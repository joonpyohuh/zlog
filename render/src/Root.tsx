import React from 'react';
import {Composition} from 'remotion';
import type {CalculateMetadataFunction} from 'remotion';
import {ZlogFilm} from './ZlogFilm';
import type {ResolvedEdl, ZlogFilmProps} from './types';

const FPS = 30;

// Studio-only placeholder — real runs always come in through --props via
// resolve-props.mjs (see render/README usage), never through this default.
const DEFAULT_EDL: ResolvedEdl = {
  project: 'placeholder',
  version: '1.0',
  generator: 'baseline',
  canvas: {width: 1080, height: 1920},
  frame: {aspect: '9:16', width: 1080, height: 1920, y_offset: 0},
  aesthetic: {
    lut: '',
    grain: 0,
    bloom: 0,
    scanlines: false,
    camcorder_osd: false,
    allow_flash: false,
  },
  audio: {bgm_id: 'placeholder', start_sec: 0, volume: 0.8, src: ''},
  timeline: [],
  captions: [],
  signature: {enabled: false, text: 'directed by zlog', duration: 2.0},
  style_preset: 'clean_vlog',
};

// Runs inside Remotion's bundled (browser) context — no Node built-ins
// (fs/path) here. All file-path resolution happens beforehand, in plain
// Node, via resolve-props.mjs; by the time props reach this composition
// every clip/audio `src` is already an absolute local path.
const calculateMetadata: CalculateMetadataFunction<ZlogFilmProps> = async ({props}) => {
  const {edl} = props;
  const timelineSec = edl.timeline.reduce((sum, c) => sum + (c.out_sec - c.in_sec), 0);
  const signatureSec = edl.signature.enabled ? edl.signature.duration : 0;
  const durationInFrames = Math.max(1, Math.round((timelineSec + signatureSec) * FPS));

  return {
    durationInFrames,
    fps: FPS,
    width: edl.canvas.width,
    height: edl.canvas.height,
  };
};

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="ZlogFilm"
      component={ZlogFilm}
      durationInFrames={FPS * 15}
      fps={FPS}
      width={1080}
      height={1920}
      defaultProps={{edl: DEFAULT_EDL}}
      calculateMetadata={calculateMetadata}
    />
  );
};
