import {randomUUID} from 'node:crypto';

import type {NextRequest} from 'next/server';

import {resolvePosition} from '@/lib/taste/resolve';
import {appendFeedback, findTimeline, listFeedback} from '@/lib/taste/store';
import type {ClipFeedback} from '@/lib/taste/types';

type FeedbackBody = {
  timeline_id?: string;
  timestamp_sec?: number;
  verdict?: 'good' | 'bad';
  comment?: string | null;
};

export async function GET(request: NextRequest) {
  const timelineId = request.nextUrl.searchParams.get('timeline_id') ?? undefined;
  const rows = await listFeedback(timelineId);
  return Response.json({feedback: rows.slice(-500).reverse()});
}

/**
 * Save one spacebar mark.
 *
 * The request carries only a moment, a verdict, and an optional comment. Every
 * other column is derived here from the timeline JSON — the client is never
 * trusted to say what was on screen, and never asked to.
 */
export async function POST(request: NextRequest) {
  let body: FeedbackBody;
  try {
    body = (await request.json()) as FeedbackBody;
  } catch {
    return Response.json({error: 'body must be JSON'}, {status: 400});
  }

  const timelineId = body.timeline_id;
  const timestampSec = body.timestamp_sec;
  const verdict = body.verdict;

  if (!timelineId) return Response.json({error: 'timeline_id is required'}, {status: 400});
  if (typeof timestampSec !== 'number' || !Number.isFinite(timestampSec) || timestampSec < 0) {
    return Response.json({error: 'timestamp_sec must be a number >= 0'}, {status: 400});
  }
  if (verdict !== 'good' && verdict !== 'bad') {
    return Response.json({error: "verdict must be 'good' or 'bad'"}, {status: 400});
  }

  const found = await findTimeline(timelineId);
  if (!found) return Response.json({error: `no timeline ${timelineId}`}, {status: 404});

  const timeline = found.round.candidates[found.timelineIndex];
  const position = resolvePosition(timeline.edl, timestampSec);
  const comment = (body.comment ?? '').trim();

  const row: ClipFeedback = {
    id: randomUUID().replace(/-/g, ''),
    timeline_id: timelineId,
    timestamp_sec: Number(timestampSec.toFixed(3)),
    clip_id: position.clip_id,
    shot_type: position.shot_type,
    active_presets: position.active_presets,
    active_params: position.active_params,
    caption_active: position.caption_active,
    verdict,
    comment: comment.length > 0 ? comment : null,
    created_at: new Date().toISOString().replace(/\.\d{3}Z$/, '+00:00'),
  };

  await appendFeedback(row);
  return Response.json({feedback: row});
}
