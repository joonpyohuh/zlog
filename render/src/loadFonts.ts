import {staticFile} from 'remotion';

let fontsRegistered = false;

const FACES: Array<{family: string; file: string; weight: string; format: string}> = [
  {family: 'Pretendard', file: 'fonts/Pretendard-Regular.otf', weight: '400', format: 'opentype'},
  {family: 'Pretendard', file: 'fonts/Pretendard-SemiBold.otf', weight: '600', format: 'opentype'},
  {family: 'Pretendard', file: 'fonts/Pretendard-Bold.otf', weight: '700', format: 'opentype'},
  {
    family: 'Space Grotesk',
    file: 'fonts/SpaceGrotesk-Variable.ttf',
    weight: '300 700',
    format: 'truetype',
  },
];

/** Register local fonts without making typography a render-blocking resource. */
export function ensureFontsLoaded(): void {
  if (fontsRegistered || typeof document === 'undefined') return;
  fontsRegistered = true;
  for (const {family, file, weight, format} of FACES) {
    const face = new FontFace(family, `url(${staticFile(file)}) format('${format}')`, {
      weight,
      style: 'normal',
      display: 'swap',
    });
    // Older TS DOM libs type FontFaceSet without add(); runtime always has it.
    (document.fonts as unknown as {add: (font: FontFace) => void}).add(face);
    void face.load().catch((error: unknown) => {
      console.warn(`zlog: ${family} font unavailable; using system fallback`, error);
    });
  }
}

export const FONT_BODY = 'Pretendard, "Noto Sans KR", sans-serif';
export const FONT_DISPLAY = '"Space Grotesk", Pretendard, sans-serif';

// Register at module import so the browser can begin loading before frame one.
if (typeof document !== 'undefined') {
  ensureFontsLoaded();
}
