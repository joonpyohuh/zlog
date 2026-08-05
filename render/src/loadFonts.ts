import {continueRender, delayRender, staticFile} from 'remotion';

let fontsPromise: Promise<void> | null = null;
const FONT_LOAD_TIMEOUT_MS = 10000;

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

/** Load local fonts staged into public/fonts by resolve-props.mjs. Idempotent. */
export function ensureFontsLoaded(): Promise<void> {
  if (fontsPromise) return fontsPromise;

  const handle = delayRender('zlog-fonts', {timeoutInMilliseconds: 60000});
  fontsPromise = Promise.all(
    FACES.map(async ({family, file, weight, format}) => {
      const face = new FontFace(family, `url(${staticFile(file)}) format('${format}')`, {
        weight,
        style: 'normal',
        display: 'block',
      });
      let timeout: ReturnType<typeof setTimeout> | undefined;
      const loaded = await Promise.race([
        face.load(),
        new Promise<never>((_, reject) => {
          timeout = setTimeout(
            () => reject(new Error(`font load timed out: ${family}`)),
            FONT_LOAD_TIMEOUT_MS,
          );
        }),
      ]).finally(() => clearTimeout(timeout));
      // Older TS DOM libs type FontFaceSet without add(); runtime always has it.
      (document.fonts as unknown as {add: (f: FontFace) => void}).add(loaded);
    }),
  )
    .then(() => undefined)
    // A missing/broken font falls back to system fonts — never fail the render
    // (an unhandled rejection would) or leave the delayRender handle hanging.
    .catch((err) => {
      console.warn('zlog: font load failed, falling back to system fonts', err);
    })
    .finally(() => continueRender(handle));

  return fontsPromise;
}

export const FONT_BODY = 'Pretendard, "Noto Sans KR", sans-serif';
export const FONT_DISPLAY = '"Space Grotesk", Pretendard, sans-serif';

// Kick off loading at module-import time (same pattern as
// @remotion/google-fonts) so the delayRender handle exists before the first
// frame is captured — a useEffect-only trigger can race the screenshot.
if (typeof document !== 'undefined') {
  void ensureFontsLoaded();
}
