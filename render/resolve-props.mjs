#!/usr/bin/env node
// Resolves an edl_baseline.json / edl_ai.json's `source_file`/`bgm_id`
// references into paths Remotion can actually load, writing a "resolved
// props" JSON that Root.tsx's <Composition> can consume as-is.
//
// Two things force this to be a separate plain-Node step instead of doing
// the resolution inside Root.tsx's calculateMetadata:
//   1. Root.tsx (and everything it imports) gets bundled for a *browser*
//      context — no fs/path there at all.
//   2. Remotion's OffthreadVideo/Audio frame-extraction only accepts
//      http(s) URLs or files under the project's `public/` dir (served via
//      staticFile()) — a bare local absolute path or file:// URI is
//      rejected by the compositor's download step.
// So: stage the exact files this EDL references into render/public/ (via
// symlink, falling back to a copy if symlinks aren't permitted), and point
// the resolved props at those staticFile()-relative paths.
//
// Usage: node resolve-props.mjs <edl.json> <resolved-props.json>

import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const [, , edlPathArg, outPathArg] = process.argv;
if (!edlPathArg || !outPathArg) {
  console.error('usage: node resolve-props.mjs <edl.json> <resolved-props.json>');
  process.exit(1);
}

const RENDER_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(RENDER_DIR, '..');
const PUBLIC_DIR = path.join(RENDER_DIR, 'public');

function stagePublicAsset(absSrcPath, relDestPath) {
  const destPath = path.join(PUBLIC_DIR, relDestPath);
  fs.mkdirSync(path.dirname(destPath), {recursive: true});
  if (!fs.existsSync(destPath)) {
    try {
      fs.symlinkSync(absSrcPath, destPath);
    } catch {
      // Windows without Developer Mode/admin rights can't create symlinks.
      fs.copyFileSync(absSrcPath, destPath);
    }
  }
  return relDestPath.replace(/\\/g, '/');
}

function resolveVideoSrc(project, sourceFile) {
  const abs = path.join(REPO_ROOT, 'footage', project, sourceFile);
  if (!fs.existsSync(abs)) {
    throw new Error(`source video not found: ${abs}`);
  }
  return stagePublicAsset(abs, path.join('footage', project, sourceFile));
}

function resolveBgmSrc(bgmId) {
  for (const ext of ['mp3', 'wav']) {
    const abs = path.join(REPO_ROOT, 'assets', 'bgm', `${bgmId}.${ext}`);
    if (fs.existsSync(abs)) {
      return stagePublicAsset(abs, path.join('bgm', `${bgmId}.${ext}`));
    }
  }
  throw new Error(`no BGM file found for bgm_id="${bgmId}" under assets/bgm/ (.mp3/.wav)`);
}

function stageFonts() {
  const fontsDir = path.join(REPO_ROOT, 'assets', 'fonts');
  if (!fs.existsSync(fontsDir)) return;
  for (const name of fs.readdirSync(fontsDir)) {
    if (!/\.(otf|ttf|woff2?)$/i.test(name)) continue;
    stagePublicAsset(path.join(fontsDir, name), path.join('fonts', name));
  }
}

const edl = JSON.parse(fs.readFileSync(path.resolve(edlPathArg), 'utf-8'));

stageFonts();

// Camcorder OSD date stamp — resolved here (plain Node, once per render)
// so every frame of a render shows the same stamp.
function shotDateStamp() {
  const now = new Date();
  const yyyy = now.getFullYear();
  const mm = String(now.getMonth() + 1).padStart(2, '0');
  const dd = String(now.getDate()).padStart(2, '0');
  const isPm = now.getHours() >= 12;
  const hour12 = now.getHours() % 12 || 12;
  const min = String(now.getMinutes()).padStart(2, '0');
  return `${yyyy}.${mm}.${dd} ${isPm ? 'PM' : 'AM'} ${hour12}:${min}`;
}

const resolvedEdl = {
  ...edl,
  captions: edl.captions ?? [],
  shot_date: shotDateStamp(),
  timeline: [...edl.timeline]
    .sort((a, b) => a.order - b.order)
    .map((clip) => ({...clip, src: resolveVideoSrc(edl.project, clip.source_file)})),
  audio: {...edl.audio, src: resolveBgmSrc(edl.audio.bgm_id)},
};

const outPath = path.resolve(outPathArg);
fs.mkdirSync(path.dirname(outPath), {recursive: true});
fs.writeFileSync(outPath, JSON.stringify({edl: resolvedEdl}, null, 2));
console.log(`wrote ${outPath}`);
