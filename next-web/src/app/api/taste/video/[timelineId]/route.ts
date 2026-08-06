import {createReadStream} from 'node:fs';
import {stat} from 'node:fs/promises';
import {Readable} from 'node:stream';

import type {NextRequest} from 'next/server';

import {videoPathFor} from '@/lib/taste/store';

/**
 * Stream a variant's rendered mp4.
 *
 * Range support is what lets four <video> elements seek and scrub on one page;
 * without it the browser refuses to seek and the review screen's spacebar
 * marking cannot resume from a paused position.
 */
export async function GET(request: NextRequest, ctx: RouteContext<'/api/taste/video/[timelineId]'>) {
  const {timelineId} = await ctx.params;

  const file = await videoPathFor(timelineId);
  if (!file) {
    return Response.json({error: `no rendered video for ${timelineId}`}, {status: 404});
  }

  const {size} = await stat(file);
  const range = request.headers.get('range');

  if (!range) {
    const stream = Readable.toWeb(createReadStream(file)) as ReadableStream;
    return new Response(stream, {
      status: 200,
      headers: {
        'Content-Type': 'video/mp4',
        'Content-Length': String(size),
        'Accept-Ranges': 'bytes',
        'Cache-Control': 'no-store',
      },
    });
  }

  const match = /bytes=(\d*)-(\d*)/.exec(range);
  const start = match?.[1] ? Number(match[1]) : 0;
  const end = match?.[2] ? Number(match[2]) : size - 1;
  if (Number.isNaN(start) || Number.isNaN(end) || start >= size || end >= size || start > end) {
    return new Response(null, {status: 416, headers: {'Content-Range': `bytes */${size}`}});
  }

  const stream = Readable.toWeb(createReadStream(file, {start, end})) as ReadableStream;
  return new Response(stream, {
    status: 206,
    headers: {
      'Content-Type': 'video/mp4',
      'Content-Length': String(end - start + 1),
      'Content-Range': `bytes ${start}-${end}/${size}`,
      'Accept-Ranges': 'bytes',
      'Cache-Control': 'no-store',
    },
  });
}
