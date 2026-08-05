/**
 * Pure-JS mirror of render/src/style.ts focus/fit helpers for CI without vitest.
 * Keep formulas in sync with objectPositionCss / objectFitForMode / effectiveFitMode.
 */
function clamp01(v) {
  return Math.max(0, Math.min(1, v));
}

function objectPositionCss(focusX, focusY) {
  return `${clamp01(focusX) * 100}% ${clamp01(focusY) * 100}%`;
}

function objectFitForMode(fit) {
  return fit === 'contain' || fit === 'blurred_background_contain' ? 'contain' : 'cover';
}

function effectiveFitMode(fitMode, cropConfidence) {
  const conf = cropConfidence ?? 1;
  if (conf < 0.35) return 'blurred_background_contain';
  return fitMode || 'subject_aware_cover';
}

const left = objectPositionCss(0.2, 0.5);
const right = objectPositionCss(0.8, 0.5);
if (left === right) {
  console.error('focus_x 0.2 and 0.8 must yield different object-position');
  process.exit(1);
}
if (left !== '20% 50%' || right !== '80% 50%') {
  console.error('unexpected positions', left, right);
  process.exit(1);
}
if (objectFitForMode('subject_aware_cover') !== 'cover') process.exit(1);
if (objectFitForMode('blurred_background_contain') !== 'contain') process.exit(1);
if (effectiveFitMode('subject_aware_cover', 0.2) !== 'blurred_background_contain') {
  process.exit(1);
}
if (effectiveFitMode('subject_aware_cover', 0.7) !== 'subject_aware_cover') {
  process.exit(1);
}
console.log('ok');
