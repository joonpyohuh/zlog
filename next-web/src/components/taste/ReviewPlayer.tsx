'use client';

import Link from 'next/link';
import {useCallback, useEffect, useRef, useState} from 'react';

type Props = {
  timelineId: string;
  videoUrl: string | null;
  renderError: string | null;
  existingCount: number;
};

type Mark = {
  timestampSec: number;
  verdict: 'good' | 'bad' | null;
  comment: string;
};

/**
 * Watch the chosen variant and mark moments.
 *
 * Spacebar pauses and captures the current playhead; the reviewer then says
 * good or bad and optionally leaves one line. Nothing numeric is ever typed —
 * what was on screen at that moment is resolved server-side from the timeline
 * JSON (see lib/taste/resolve.ts). Saving resumes playback.
 */
export default function ReviewPlayer({
  timelineId,
  videoUrl,
  renderError,
  existingCount,
}: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const commentRef = useRef<HTMLInputElement>(null);
  const [mark, setMark] = useState<Mark | null>(null);
  const [savedCount, setSavedCount] = useState(existingCount);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const capture = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    video.pause();
    setMark({timestampSec: Number(video.currentTime.toFixed(3)), verdict: null, comment: ''});
    setError(null);
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.code !== 'Space') return;
      const target = event.target as HTMLElement | null;
      // Let space work normally while the reviewer is typing a comment.
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return;
      event.preventDefault();
      if (!mark) capture();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [capture, mark]);

  async function save() {
    if (!mark?.verdict) return;
    setSaving(true);
    setError(null);
    try {
      const response = await fetch('/api/taste/feedback', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          timeline_id: timelineId,
          timestamp_sec: mark.timestampSec,
          verdict: mark.verdict,
          comment: mark.comment,
        }),
      });
      if (!response.ok) {
        const detail = (await response.json().catch(() => ({}))) as {error?: string};
        throw new Error(detail.error ?? `save failed (${response.status})`);
      }
      setSavedCount((n) => n + 1);
      setMark(null);
      // Saving resumes where the reviewer left off, so a pass through the
      // video stays one continuous watch rather than a series of restarts.
      void videoRef.current?.play();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setSaving(false);
    }
  }

  function cancel() {
    setMark(null);
    void videoRef.current?.play();
  }

  if (!videoUrl) {
    return (
      <main style={{padding: '3rem 1.5rem', maxWidth: 720, margin: '0 auto'}}>
        <h1 style={{fontSize: '1.4rem'}}>이 변형은 렌더에 실패했습니다</h1>
        <p style={{opacity: 0.75, marginTop: '0.75rem'}}>{renderError}</p>
        <p style={{marginTop: '1.5rem'}}>
          <Link href="/taste/compare">← 비교 화면으로</Link>
        </p>
      </main>
    );
  }

  return (
    <main style={{padding: '2rem 1.25rem 4rem', maxWidth: 860, margin: '0 auto'}}>
      <header style={{marginBottom: '1.25rem'}}>
        <h1 style={{fontSize: '1.3rem', margin: 0}}>구간 평가</h1>
        <p style={{opacity: 0.65, fontSize: '0.9rem', marginTop: '0.4rem'}}>
          재생 중 <kbd>스페이스바</kbd>를 누르면 그 지점이 찍히고 멈춥니다 · 저장한 평가{' '}
          {savedCount}개
        </p>
      </header>

      <video
        ref={videoRef}
        src={videoUrl}
        controls
        playsInline
        preload="metadata"
        style={{
          width: '100%',
          maxWidth: 420,
          display: 'block',
          margin: '0 auto',
          background: '#000',
          borderRadius: 12,
          aspectRatio: '9 / 16',
        }}
      />

      {!mark && (
        <div style={{textAlign: 'center', marginTop: '1.25rem'}}>
          <button
            type="button"
            onClick={capture}
            style={{
              padding: '0.7rem 1.4rem',
              borderRadius: 8,
              border: '1px solid rgba(127,127,127,0.45)',
              background: 'transparent',
              cursor: 'pointer',
            }}
          >
            지금 지점 찍기 (Space)
          </button>
        </div>
      )}

      {mark && (
        <section
          style={{
            marginTop: '1.5rem',
            border: '1px solid rgba(127,127,127,0.4)',
            borderRadius: 12,
            padding: '1.1rem 1.25rem',
          }}
        >
          <p style={{margin: 0, fontWeight: 600}}>{mark.timestampSec.toFixed(2)}초 지점</p>

          <div style={{display: 'flex', gap: '0.75rem', marginTop: '0.9rem'}}>
            {(['good', 'bad'] as const).map((verdict) => (
              <button
                key={verdict}
                type="button"
                onClick={() => setMark({...mark, verdict})}
                style={{
                  flex: 1,
                  padding: '0.7rem',
                  borderRadius: 8,
                  cursor: 'pointer',
                  fontWeight: 600,
                  border:
                    mark.verdict === verdict
                      ? '2px solid #4f8cff'
                      : '1px solid rgba(127,127,127,0.45)',
                  background: mark.verdict === verdict ? 'rgba(79,140,255,0.14)' : 'transparent',
                  color: 'inherit',
                }}
              >
                {verdict === 'good' ? '좋음' : '나쁨'}
              </button>
            ))}
          </div>

          <input
            ref={commentRef}
            type="text"
            value={mark.comment}
            onChange={(event) => setMark({...mark, comment: event.target.value})}
            placeholder="한 줄 코멘트 (선택)"
            style={{
              width: '100%',
              marginTop: '0.9rem',
              padding: '0.65rem 0.8rem',
              borderRadius: 8,
              border: '1px solid rgba(127,127,127,0.4)',
              background: 'transparent',
              color: 'inherit',
            }}
          />

          <div style={{display: 'flex', gap: '0.75rem', marginTop: '1rem'}}>
            <button
              type="button"
              disabled={!mark.verdict || saving}
              onClick={save}
              style={{
                flex: 1,
                padding: '0.7rem',
                borderRadius: 8,
                border: 'none',
                background: mark.verdict ? '#4f8cff' : 'rgba(127,127,127,0.3)',
                color: '#fff',
                fontWeight: 600,
                cursor: mark.verdict && !saving ? 'pointer' : 'not-allowed',
              }}
            >
              {saving ? '저장 중…' : '저장하고 이어보기'}
            </button>
            <button
              type="button"
              onClick={cancel}
              style={{
                padding: '0.7rem 1.1rem',
                borderRadius: 8,
                border: '1px solid rgba(127,127,127,0.45)',
                background: 'transparent',
                cursor: 'pointer',
              }}
            >
              취소
            </button>
          </div>
        </section>
      )}

      {error && (
        <p role="alert" style={{color: '#e05a5a', marginTop: '1rem'}}>
          {error}
        </p>
      )}

      <div style={{marginTop: '2rem', display: 'flex', gap: '1rem'}}>
        <Link href="/taste/compare">끝 (비교 화면으로) →</Link>
        <Link href={`/taste/feedback?timeline_id=${timelineId}`}>이 영상의 평가 목록</Link>
      </div>
    </main>
  );
}
