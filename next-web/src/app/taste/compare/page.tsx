import Link from 'next/link';

import CompareBoard from '@/components/taste/CompareBoard';
import {nextUnresolvedRound} from '@/lib/taste/store';
import {toBlindVariants} from '@/lib/taste/types';

export const dynamic = 'force-dynamic';

export default async function ComparePage() {
  const round = await nextUnresolvedRound();

  if (!round) {
    return (
      <main style={{padding: '3rem 1.5rem', maxWidth: 720, margin: '0 auto'}}>
        <h1 style={{fontSize: '1.5rem', marginBottom: '1rem'}}>비교할 라운드가 없습니다</h1>
        <p style={{opacity: 0.75, lineHeight: 1.7}}>
          새 라운드를 만들려면 저장소 루트에서 실행하세요:
        </p>
        <pre
          style={{
            background: 'rgba(127,127,127,0.12)',
            padding: '0.9rem 1rem',
            borderRadius: 8,
            overflowX: 'auto',
            marginTop: '0.75rem',
          }}
        >
          python -m pipeline.taste_loop.cli start-round --axis avg_cut_duration
        </pre>
        <p style={{marginTop: '1.5rem'}}>
          <Link href="/taste/feedback">쌓인 구간 평가 보기 →</Link>
        </p>
      </main>
    );
  }

  // Only blind variants cross into the client: no axis, no axis_value, no
  // clip counts, no durations. The whole point of the round is that the pick
  // is made on what the video looks like.
  return (
    <CompareBoard
      roundId={round.id}
      variants={toBlindVariants(round)}
      mediaSetId={round.media_set_id}
    />
  );
}
