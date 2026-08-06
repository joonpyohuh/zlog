import {listRounds} from '@/lib/taste/store';

/**
 * Round index for the feedback/history screen.
 *
 * Returns axis values here on purpose — this endpoint feeds the *review*
 * screens, which run after a pick is already recorded. The comparison screen
 * uses toBlindVariants() and never sees them.
 */
export async function GET() {
  const rounds = await listRounds();
  return Response.json({
    rounds: rounds.map((round) => ({
      id: round.id,
      media_set_id: round.media_set_id,
      axis: round.axis,
      resolved: round.resolved,
      rejected_all: round.rejected_all,
      winner_id: round.winner_id,
      created_at: round.created_at,
      resolved_at: round.resolved_at,
      render_ok_count: round.renders.filter((r) => r.ok).length,
      render_total: round.renders.length,
      failed: round.renders
        .filter((r) => !r.ok)
        .map((r) => ({timeline_id: r.timeline_id, error: r.error})),
    })),
  });
}
