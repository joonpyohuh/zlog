import type {Aesthetic, Edl, Frame} from './types';

export type StylePresetName = 'clean_vlog' | 'y2k_camcorder' | string;

export type ResolvedStyle = {
  preset: StylePresetName;
  letterbox: boolean;
  filmLook: boolean;
  camcorderOsd: boolean;
  allowFlash: boolean;
  endingCredit: boolean;
  naturalColor: boolean;
};

/** Resolve render style from EDL.style_preset + aesthetic toggles. */
export function resolveStyle(edl: Pick<Edl, 'style_preset' | 'aesthetic' | 'signature' | 'frame'>): ResolvedStyle {
  const preset = (edl.style_preset || 'clean_vlog').toLowerCase();
  const isY2k =
    preset === 'y2k_camcorder' ||
    preset === 'y2k_4x3_letterbox' ||
    edl.frame.aspect === '4:3';

  if (isY2k && preset !== 'clean_vlog') {
    return {
      preset: 'y2k_camcorder',
      letterbox: true,
      filmLook: (edl.aesthetic.grain ?? 0) > 0.001 || Boolean(edl.aesthetic.scanlines),
      camcorderOsd: edl.aesthetic.camcorder_osd !== false,
      allowFlash: edl.aesthetic.allow_flash !== false,
      endingCredit: edl.signature.enabled,
      naturalColor: false,
    };
  }

  // Default: clean_vlog — full-bleed vertical, no forced Y2K kit.
  return {
    preset: 'clean_vlog',
    letterbox: false,
    filmLook: (edl.aesthetic.grain ?? 0) > 0.02,
    camcorderOsd: Boolean(edl.aesthetic.camcorder_osd),
    allowFlash: Boolean(edl.aesthetic.allow_flash),
    endingCredit: edl.signature.enabled,
    naturalColor: true,
  };
}

export function pictureRect(
  frame: Frame,
  canvasWidth: number,
): {top: number; left: number; width: number; height: number} {
  return {
    top: frame.y_offset,
    left: (canvasWidth - frame.width) / 2,
    width: frame.width,
    height: frame.height,
  };
}

export function effectiveFitMode(
  fitMode: string | undefined,
  cropConfidence: number | null | undefined,
): string {
  const conf = cropConfidence ?? 1;
  if (conf < 0.35) {
    return 'blurred_background_contain';
  }
  return fitMode || 'subject_aware_cover';
}

function clamp01(v: number): number {
  return Math.max(0, Math.min(1, v));
}

/** CSS object-position from normalized focus (subject-aware cover). */
export function objectPositionCss(focusX: number, focusY: number): string {
  return `${clamp01(focusX) * 100}% ${clamp01(focusY) * 100}%`;
}

/** object-fit for PlannedClip.fit_mode (blurred contain shows full frame in front). */
export function objectFitForMode(fit: string): 'contain' | 'cover' {
  return fit === 'contain' || fit === 'blurred_background_contain' ? 'contain' : 'cover';
}

export function aestheticDefaults(preset: StylePresetName): Aesthetic {
  if (preset === 'y2k_camcorder' || preset === 'y2k_4x3_letterbox') {
    return {
      lut: 'ccd_cool_01.cube',
      grain: 0.15,
      bloom: 0.2,
      scanlines: true,
      camcorder_osd: true,
      allow_flash: true,
    };
  }
  return {
    lut: '',
    grain: 0,
    bloom: 0,
    scanlines: false,
    camcorder_osd: false,
    allow_flash: false,
  };
}
