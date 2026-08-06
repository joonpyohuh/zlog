import type {NextRequest} from 'next/server';

import {recordPick} from '@/lib/taste/store';

type PickBody = {
  winner_id?: string | null;
  rejected_all?: boolean;
};

/** Record which variant won, or that none of the four did. */
export async function POST(request: NextRequest, ctx: RouteContext<'/api/taste/rounds/[roundId]/pick'>) {
  const {roundId} = await ctx.params;

  let body: PickBody;
  try {
    body = (await request.json()) as PickBody;
  } catch {
    return Response.json({error: 'body must be JSON'}, {status: 400});
  }

  const rejectedAll = Boolean(body.rejected_all);
  const winnerId = rejectedAll ? null : (body.winner_id ?? null);
  if (!rejectedAll && !winnerId) {
    return Response.json(
      {error: 'pass winner_id, or rejected_all: true'},
      {status: 400},
    );
  }

  try {
    const round = await recordPick(roundId, winnerId, rejectedAll);
    return Response.json({
      id: round.id,
      winner_id: round.winner_id,
      rejected_all: round.rejected_all,
      resolved_at: round.resolved_at,
    });
  } catch (error) {
    return Response.json({error: (error as Error).message}, {status: 400});
  }
}
