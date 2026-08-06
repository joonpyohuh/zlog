// Server-side reader/writer for the taste-loop store.
//
// Reads the same files pipeline/taste_loop/store.py's LocalTasteStore writes,
// straight off disk. That keeps the UI and the CLI on one source of truth
// without standing up a service in between, which matters because the loop is
// a single-operator tool running on the founder's own machine.
//
// Everything here is server-only: it touches the filesystem and must never be
// imported from a Client Component.

import {promises as fs} from 'node:fs';
import path from 'node:path';

import type {ClipFeedback, ComparisonRound} from './types';

/**
 * Repo root. next-web/ sits one level below it; ZLOG_REPO_ROOT overrides for
 * deployments where the Next app does not live next to work/.
 */
export function repoRoot(): string {
  return process.env.ZLOG_REPO_ROOT ?? path.resolve(process.cwd(), '..');
}

function roundsDir(): string {
  return path.join(repoRoot(), 'taste', 'taste_loop', 'rounds');
}

function feedbackPath(): string {
  return path.join(repoRoot(), 'taste', 'taste_loop', 'clip_feedback.jsonl');
}

async function readJson<T>(file: string): Promise<T | null> {
  try {
    return JSON.parse(await fs.readFile(file, 'utf8')) as T;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return null;
    throw error;
  }
}

export async function listRounds(): Promise<ComparisonRound[]> {
  let names: string[];
  try {
    names = await fs.readdir(roundsDir());
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return [];
    throw error;
  }

  const rounds: ComparisonRound[] = [];
  for (const name of names.filter((n) => n.endsWith('.json'))) {
    const round = await readJson<ComparisonRound>(path.join(roundsDir(), name));
    if (round) rounds.push(round);
  }
  rounds.sort((a, b) => a.created_at.localeCompare(b.created_at));
  return rounds;
}

export async function getRound(roundId: string): Promise<ComparisonRound | null> {
  // roundId comes from the URL — keep it to the hex ids the CLI generates so
  // it can never walk out of the rounds directory.
  if (!/^[a-f0-9]{8,64}$/i.test(roundId)) return null;
  return readJson<ComparisonRound>(path.join(roundsDir(), `${roundId}.json`));
}

/** The oldest round that has renders but no recorded decision yet. */
export async function nextUnresolvedRound(): Promise<ComparisonRound | null> {
  const rounds = await listRounds();
  return rounds.find((r) => !r.resolved) ?? null;
}

export async function findTimeline(
  timelineId: string,
): Promise<{round: ComparisonRound; timelineIndex: number} | null> {
  for (const round of await listRounds()) {
    const timelineIndex = round.candidates.findIndex((c) => c.timeline_id === timelineId);
    if (timelineIndex >= 0) return {round, timelineIndex};
  }
  return null;
}

export async function recordPick(
  roundId: string,
  winnerId: string | null,
  rejectedAll: boolean,
): Promise<ComparisonRound> {
  const round = await getRound(roundId);
  if (!round) throw new Error(`no round ${roundId}`);
  if (winnerId && rejectedAll) {
    throw new Error('a round cannot both have a winner and reject all four');
  }
  if (winnerId && !round.candidates.some((c) => c.timeline_id === winnerId)) {
    throw new Error(`${winnerId} is not a candidate of round ${roundId}`);
  }

  const updated: ComparisonRound = {
    ...round,
    winner_id: winnerId,
    rejected_all: rejectedAll,
    resolved: true,
    resolved_at: new Date().toISOString().replace(/\.\d{3}Z$/, '+00:00'),
  };
  await fs.writeFile(
    path.join(roundsDir(), `${roundId}.json`),
    `${JSON.stringify(updated, null, 2)}\n`,
    'utf8',
  );
  return updated;
}

export async function listFeedback(timelineId?: string): Promise<ClipFeedback[]> {
  let raw: string;
  try {
    raw = await fs.readFile(feedbackPath(), 'utf8');
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return [];
    throw error;
  }
  const rows = raw
    .split('\n')
    .filter((line) => line.trim().length > 0)
    .map((line) => JSON.parse(line) as ClipFeedback);
  return timelineId ? rows.filter((r) => r.timeline_id === timelineId) : rows;
}

export async function appendFeedback(row: ClipFeedback): Promise<ClipFeedback> {
  await fs.mkdir(path.dirname(feedbackPath()), {recursive: true});
  await fs.appendFile(feedbackPath(), `${JSON.stringify(row)}\n`, 'utf8');
  return row;
}

/** Absolute path of a variant's rendered mp4, or null when it never rendered. */
export async function videoPathFor(timelineId: string): Promise<string | null> {
  const found = await findTimeline(timelineId);
  if (!found) return null;
  const render = found.round.renders.find((r) => r.timeline_id === timelineId);
  if (!render?.ok || !render.video_path) return null;

  const absolute = path.resolve(repoRoot(), render.video_path);
  // video_path is written by our own renderer, but it is still a path from a
  // file — confine it to the repo before opening it.
  if (!absolute.startsWith(path.resolve(repoRoot()))) return null;
  try {
    await fs.access(absolute);
  } catch {
    return null;
  }
  return absolute;
}
