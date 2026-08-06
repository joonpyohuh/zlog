import Link from 'next/link';

import ReviewPlayer from '@/components/taste/ReviewPlayer';
import {findTimeline, listFeedback} from '@/lib/taste/store';

export const dynamic = 'force-dynamic';

export default async function ReviewPage({
  params,
}: {
  params: Promise<{timelineId: string}>;
}) {
  const {timelineId} = await params;
  const found = await findTimeline(timelineId);

  if (!found) {
    return (
      <main style={{padding: '3rem 1.5rem', maxWidth: 720, margin: '0 auto'}}>
        <h1 style={{fontSize: '1.4rem'}}>이 timeline을 찾을 수 없습니다</h1>
        <p style={{opacity: 0.7}}>
          <code>{timelineId}</code>
        </p>
        <p style={{marginTop: '1.5rem'}}>
          <Link href="/taste/compare">← 비교 화면으로</Link>
        </p>
      </main>
    );
  }

  const render = found.round.renders.find((r) => r.timeline_id === timelineId);
  const existing = await listFeedback(timelineId);

  return (
    <ReviewPlayer
      timelineId={timelineId}
      videoUrl={render?.ok ? `/api/taste/video/${timelineId}` : null}
      renderError={render?.ok ? null : (render?.error ?? 'not rendered')}
      existingCount={existing.length}
    />
  );
}
