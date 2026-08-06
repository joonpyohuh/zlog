'use client';

import {useRouter} from 'next/navigation';
import {useState} from 'react';

import type {BlindVariant} from '@/lib/taste/types';

type Props = {
  roundId: string;
  variants: BlindVariant[];
  mediaSetId: string;
};

/**
 * Four videos, side by side, labelled A–D and nothing else.
 *
 * There is deliberately no place in this component to render a setting value:
 * BlindVariant does not carry one. If a number were on screen the pick would
 * start being a judgement about the number instead of about the video, and the
 * whole log would quietly stop measuring taste.
 */
export default function CompareBoard({roundId, variants, mediaSetId}: Props) {
  const router = useRouter();
  const [selected, setSelected] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const playable = variants.filter((v) => v.ok);
  const failed = variants.filter((v) => !v.ok);

  async function submit(winnerId: string | null, rejectedAll: boolean) {
    setSaving(true);
    setError(null);
    try {
      const response = await fetch(`/api/taste/rounds/${roundId}/pick`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({winner_id: winnerId, rejected_all: rejectedAll}),
      });
      if (!response.ok) {
        const detail = (await response.json().catch(() => ({}))) as {error?: string};
        throw new Error(detail.error ?? `pick failed (${response.status})`);
      }
      if (winnerId) {
        setSelected(winnerId);
      } else {
        router.refresh();
      }
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <main style={{padding: '2rem 1.25rem 4rem', maxWidth: 1440, margin: '0 auto'}}>
      <header style={{marginBottom: '1.5rem'}}>
        <h1 style={{fontSize: '1.4rem', margin: 0}}>어떤 게 제일 마음에 드나요?</h1>
        <p style={{opacity: 0.6, marginTop: '0.4rem', fontSize: '0.9rem'}}>
          네 개를 다 본 뒤 하나를 고르세요 · media set: {mediaSetId}
        </p>
      </header>

      {failed.length > 0 && (
        <div
          role="alert"
          style={{
            border: '1px solid rgba(220,60,60,0.5)',
            background: 'rgba(220,60,60,0.08)',
            borderRadius: 8,
            padding: '0.85rem 1rem',
            marginBottom: '1.5rem',
            fontSize: '0.88rem',
          }}
        >
          <strong>{failed.length}개 변형이 렌더에 실패했습니다.</strong>
          <ul style={{margin: '0.5rem 0 0', paddingLeft: '1.1rem'}}>
            {failed.map((variant) => (
              <li key={variant.timeline_id} style={{marginBottom: '0.25rem'}}>
                <code>{variant.label}</code> — {variant.error}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
          gap: '1.25rem',
        }}
      >
        {variants.map((variant) => {
          const isSelected = selected === variant.timeline_id;
          return (
            <section
              key={variant.timeline_id}
              style={{
                border: isSelected ? '2px solid #4f8cff' : '1px solid rgba(127,127,127,0.35)',
                borderRadius: 12,
                overflow: 'hidden',
                opacity: variant.ok ? 1 : 0.45,
              }}
            >
              <div
                style={{
                  padding: '0.5rem 0.85rem',
                  fontWeight: 600,
                  borderBottom: '1px solid rgba(127,127,127,0.25)',
                }}
              >
                {variant.label}
              </div>

              {variant.videoUrl ? (
                <video
                  src={variant.videoUrl}
                  controls
                  preload="metadata"
                  playsInline
                  style={{width: '100%', display: 'block', background: '#000', aspectRatio: '9 / 16'}}
                />
              ) : (
                <div
                  style={{
                    aspectRatio: '9 / 16',
                    display: 'grid',
                    placeItems: 'center',
                    background: 'rgba(127,127,127,0.12)',
                    padding: '1rem',
                    textAlign: 'center',
                    fontSize: '0.85rem',
                  }}
                >
                  렌더 실패 — 고를 수 없습니다
                </div>
              )}

              <div style={{padding: '0.75rem'}}>
                <button
                  type="button"
                  disabled={!variant.ok || saving || selected !== null}
                  onClick={() => submit(variant.timeline_id, false)}
                  style={{
                    width: '100%',
                    padding: '0.6rem',
                    borderRadius: 8,
                    cursor: variant.ok && !saving && !selected ? 'pointer' : 'not-allowed',
                    border: '1px solid rgba(127,127,127,0.4)',
                    background: isSelected ? '#4f8cff' : 'transparent',
                    color: isSelected ? '#fff' : 'inherit',
                    fontWeight: 600,
                  }}
                >
                  {isSelected ? '선택함' : '이걸로'}
                </button>

                {isSelected && (
                  <a
                    href={`/taste/review/${variant.timeline_id}`}
                    style={{
                      display: 'block',
                      textAlign: 'center',
                      marginTop: '0.6rem',
                      fontSize: '0.88rem',
                    }}
                  >
                    이 영상 자세히 평가하기 →
                  </a>
                )}
              </div>
            </section>
          );
        })}
      </div>

      {error && (
        <p role="alert" style={{color: '#e05a5a', marginTop: '1.25rem'}}>
          {error}
        </p>
      )}

      <div style={{marginTop: '2rem', display: 'flex', gap: '0.85rem', flexWrap: 'wrap'}}>
        <button
          type="button"
          disabled={saving || selected !== null || playable.length === 0}
          onClick={() => submit(null, true)}
          style={{
            padding: '0.7rem 1.2rem',
            borderRadius: 8,
            border: '1px solid rgba(127,127,127,0.45)',
            background: 'transparent',
            cursor: saving || selected ? 'not-allowed' : 'pointer',
          }}
        >
          넷 다 별로
        </button>

        {selected && (
          <button
            type="button"
            onClick={() => router.refresh()}
            style={{
              padding: '0.7rem 1.2rem',
              borderRadius: 8,
              border: '1px solid rgba(127,127,127,0.45)',
              background: 'transparent',
              cursor: 'pointer',
            }}
          >
            다음 라운드로 →
          </button>
        )}

        <a href="/taste/feedback" style={{alignSelf: 'center', fontSize: '0.9rem'}}>
          쌓인 구간 평가 보기
        </a>
      </div>
    </main>
  );
}
