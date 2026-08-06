import Link from 'next/link';

import {listFeedback, listRounds} from '@/lib/taste/store';

export const dynamic = 'force-dynamic';

const cell: React.CSSProperties = {
  padding: '0.5rem 0.7rem',
  borderBottom: '1px solid rgba(127,127,127,0.22)',
  verticalAlign: 'top',
  fontSize: '0.86rem',
};

/**
 * Read-only list of what has been collected so far.
 *
 * No analysis here on purpose — this release only accumulates. The point of
 * the screen is to confirm rows are landing and that the auto-filled columns
 * (clip, presets, caption) look right.
 */
export default async function FeedbackPage({
  searchParams,
}: {
  searchParams: Promise<{timeline_id?: string}>;
}) {
  const {timeline_id: timelineId} = await searchParams;
  const [feedback, rounds] = await Promise.all([listFeedback(timelineId), listRounds()]);
  const rows = [...feedback].reverse();

  const resolved = rounds.filter((r) => r.resolved);
  const picked = resolved.filter((r) => !r.rejected_all);
  const failedRenders = rounds.flatMap((r) =>
    r.renders.filter((v) => !v.ok).map((v) => ({round: r.id, ...v})),
  );

  return (
    <main style={{padding: '2rem 1.25rem 4rem', maxWidth: 1200, margin: '0 auto'}}>
      <h1 style={{fontSize: '1.4rem', margin: 0}}>수집 현황</h1>
      <p style={{opacity: 0.65, fontSize: '0.9rem', marginTop: '0.4rem'}}>
        라운드 {rounds.length}개 · 결정됨 {resolved.length}개 · 선택 있음 {picked.length}개 ·
        넷 다 별로 {resolved.length - picked.length}개 · 구간 평가 {feedback.length}개
      </p>
      <p style={{marginTop: '0.75rem'}}>
        <Link href="/taste/compare">← 비교 화면</Link>
      </p>

      {failedRenders.length > 0 && (
        <section
          style={{
            marginTop: '1.5rem',
            border: '1px solid rgba(220,60,60,0.45)',
            background: 'rgba(220,60,60,0.07)',
            borderRadius: 8,
            padding: '0.85rem 1rem',
          }}
        >
          <strong style={{fontSize: '0.92rem'}}>렌더 실패 {failedRenders.length}건</strong>
          <ul style={{margin: '0.5rem 0 0', paddingLeft: '1.1rem', fontSize: '0.82rem'}}>
            {failedRenders.map((item) => (
              <li key={item.timeline_id} style={{marginBottom: '0.3rem'}}>
                <code>{item.timeline_id}</code> — {item.error}
              </li>
            ))}
          </ul>
        </section>
      )}

      <h2 style={{fontSize: '1.05rem', marginTop: '2rem'}}>
        구간 평가 {timelineId ? `— ${timelineId}` : ''}
      </h2>

      {rows.length === 0 ? (
        <p style={{opacity: 0.65, marginTop: '0.75rem'}}>아직 저장된 구간 평가가 없습니다.</p>
      ) : (
        <div style={{overflowX: 'auto', marginTop: '0.75rem'}}>
          <table style={{borderCollapse: 'collapse', width: '100%', minWidth: 900}}>
            <thead>
              <tr style={{textAlign: 'left'}}>
                <th style={cell}>시각</th>
                <th style={cell}>판정</th>
                <th style={cell}>clip_id</th>
                <th style={cell}>shot_type</th>
                <th style={cell}>자막</th>
                <th style={cell}>active_presets</th>
                <th style={cell}>코멘트</th>
                <th style={cell}>timeline</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td style={cell}>{row.timestamp_sec.toFixed(2)}s</td>
                  <td style={{...cell, color: row.verdict === 'good' ? '#3f9d5a' : '#d1584f'}}>
                    {row.verdict === 'good' ? '좋음' : '나쁨'}
                  </td>
                  <td style={cell}>
                    <code>{row.clip_id ?? '—'}</code>
                  </td>
                  <td style={cell}>{row.shot_type ?? '—'}</td>
                  <td style={cell}>{row.caption_active ? 'O' : '—'}</td>
                  <td style={{...cell, maxWidth: 280}}>
                    {row.active_presets.length > 0 ? row.active_presets.join(', ') : '—'}
                  </td>
                  <td style={cell}>{row.comment ?? '—'}</td>
                  <td style={cell}>
                    <Link href={`/taste/review/${row.timeline_id}`}>
                      <code>{row.timeline_id.slice(-8)}</code>
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
