import {useEffect, useRef, useState, type KeyboardEvent} from 'react';
import './App.css';

type Attachment = {
  id: string;
  file: File;
  kind: 'image' | 'video' | 'other';
  previewUrl?: string;
};

type JobStatus = {
  id: string;
  status: 'queued' | 'running' | 'done' | 'error';
  stage?: string;
  error?: string;
  video_url?: string;
  generator?: string;
  duration_s?: number;
  progress_pct?: number;
  progress?: string;
};

type ReviewItem = {
  segment_id: string;
  suggested: 'keep' | 'drop';
  frame_url: string;
};

const STAGE_COPY: Record<string, string> = {
  queued: 'In queue',
  prepare: 'Preparing media',
  beats: 'Reading music',
  split: 'Finding scenes',
  evidence: 'Evidence frames',
  features: 'Features',
  filter: 'Cleaning shots',
  sheet: 'Contact sheets',
  analyze: 'Asset analysis',
  director: 'Story plan',
  plan: 'Timeline',
  evaluate: 'Evaluating plan',
  select: 'Baseline cut',
  render: 'Rendering film',
  grade: 'Color grade',
  audio: 'Audio mix',
  done: 'Ready',
};

function kindOf(file: File): Attachment['kind'] {
  if (file.type.startsWith('image/')) return 'image';
  if (file.type.startsWith('video/')) return 'video';
  return 'other';
}

function stageLabel(stage?: string) {
  if (!stage) return 'Working';
  return STAGE_COPY[stage] || stage;
}

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, '') ?? '';

function apiUrl(path: string) {
  if (!path) return path;
  if (/^https?:\/\//i.test(path)) return path;
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
}

export default function App() {
  const [text, setText] = useState('');
  const [files, setFiles] = useState<Attachment[]>([]);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [qualityMode, setQualityMode] = useState<'economy' | 'balanced' | 'premium'>(
    'balanced',
  );
  const [dragOver, setDragOver] = useState(false);
  const [reviewItems, setReviewItems] = useState<ReviewItem[]>([]);
  const [reviewChoices, setReviewChoices] = useState<Record<string, 'keep' | 'drop' | 'skip'>>({});
  const [reviewSaved, setReviewSaved] = useState(false);
  const [reviewSaving, setReviewSaving] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    return () => {
      for (const f of files) {
        if (f.previewUrl) URL.revokeObjectURL(f.previewUrl);
      }
    };
  }, [files]);

  useEffect(() => {
    const jobId = job?.id;
    const jobStatus = job?.status;
    if (!jobId || jobStatus === 'done' || jobStatus === 'error') return;
    let failedPolls = 0;
    const t = window.setInterval(async () => {
      try {
        const res = await fetch(apiUrl(`/api/jobs/${jobId}`));
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        failedPolls = 0;
        const next = (await res.json()) as JobStatus;
        setJob(next);
        if (next.status === 'done' || next.status === 'error') {
          setBusy(false);
          if (next.status === 'done') {
            setText('');
            setFiles((prev) => {
              for (const f of prev) if (f.previewUrl) URL.revokeObjectURL(f.previewUrl);
              return [];
            });
            void fetch(apiUrl(`/api/jobs/${next.id}/review-items`))
              .then((r) => (r.ok ? r.json() : null))
              .then((data) => {
                if (!data?.items) return;
                setReviewItems(data.items as ReviewItem[]);
                const init: Record<string, 'keep' | 'drop' | 'skip'> = {};
                for (const item of data.items as ReviewItem[]) {
                  init[item.segment_id] = item.suggested;
                }
                setReviewChoices(init);
                setReviewSaved(false);
                setReviewError(null);
              })
              .catch(() => undefined);
          }
        }
      } catch {
        failedPolls += 1;
        if (failedPolls < 5) return;
        setBusy(false);
        setJob((current) =>
          current?.id === jobId
            ? {...current, status: 'error', error: 'Connection lost while checking your film. Try again.'}
            : current,
        );
      }
    }, 1200);
    return () => window.clearInterval(t);
  }, [job?.id, job?.status]);

  function addFiles(list: FileList | File[]) {
    const next = Array.from(list)
      .filter((file) => !/\.heic$|\.heif$/i.test(file.name))
      .map((file) => {
        const kind = kindOf(file);
        return {
          id: `${file.name}-${file.size}-${file.lastModified}-${crypto.randomUUID()}`,
          file,
          kind,
          previewUrl:
            kind === 'image' || kind === 'video' ? URL.createObjectURL(file) : undefined,
        } satisfies Attachment;
      });
    setFiles((prev) => [...prev, ...next]);
  }

  function removeFile(id: string) {
    setFiles((prev) => {
      const target = prev.find((f) => f.id === id);
      if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl);
      return prev.filter((f) => f.id !== id);
    });
  }

  async function onSubmit() {
    if (busy) return;
    if (!text.trim() && files.length === 0) return;

    setBusy(true);
    setJob(null);

    const body = new FormData();
    body.append('note', text.trim());
    body.append('quality_mode', qualityMode);
    body.append('dev_mode', '0');
    for (const item of files) body.append('files', item.file);

    try {
      const res = await fetch(apiUrl('/api/jobs'), {method: 'POST', body});
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(detail || `HTTP ${res.status}`);
      }
      const created = (await res.json()) as JobStatus;
      setJob(created);
    } catch (err) {
      setBusy(false);
      setJob({
        id: 'local',
        status: 'error',
        error: err instanceof Error ? err.message : 'Request failed',
      });
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void onSubmit();
    }
  }

  async function saveReview() {
    if (!job || reviewSaving) return;
    setReviewSaving(true);
    setReviewError(null);
    const decisions = reviewItems
      .map((item) => ({
        segment_id: item.segment_id,
        decision: reviewChoices[item.segment_id],
        reason: `web ${reviewChoices[item.segment_id]}`,
      }))
      .filter((d) => d.decision === 'keep' || d.decision === 'drop');

    try {
      const response = await fetch(apiUrl(`/api/jobs/${job.id}/review`), {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({decisions}),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setReviewSaved(true);
    } catch {
      setReviewError('Could not save your judgments. Try again.');
    } finally {
      setReviewSaving(false);
    }
  }

  const canSend = !busy && (text.trim().length > 0 || files.length > 0);
  const showBrand = !job || (job.status !== 'done' && job.status !== 'running' && job.status !== 'queued');

  return (
    <div
      className={`app${dragOver ? ' app--drag' : ''}`}
      onDragEnter={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={(e) => {
        if (e.currentTarget === e.target) setDragOver(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files);
      }}
    >
      <main className="stage">
        {showBrand && (
          <>
            <h1 className="brand">zlog</h1>
            <p className="tag">Photo. Video. Words. Film.</p>
          </>
        )}

        {job && (job.status === 'queued' || job.status === 'running') && (
          <div className="progress" aria-live="polite">
            <div className="progress-pulse" />
            <p className="progress-title">
              {stageLabel(job.stage)}
              {job.stage === 'render' && typeof job.progress_pct === 'number'
                ? ` · ${job.progress_pct}%`
                : ''}
            </p>
            <p className="progress-sub">
              {job.generator === 'ai' ? 'Taste-informed cut' : 'Building your film'}
              {job.duration_s ? ` · ~${Math.round(job.duration_s)}s` : ''}
              {job.stage === 'render' && job.progress ? ` · frames ${job.progress}` : ''}
            </p>
            {job.stage === 'render' && (
              <p className="progress-sub">Local Remotion render — usually 1–3 min on this machine.</p>
            )}
          </div>
        )}

        {job?.status === 'done' && job.video_url && (
          <div className="result">
            <video
              className="result-video"
              src={apiUrl(job.video_url)}
              controls
              playsInline
              autoPlay
              muted
            />
            <div className="result-actions">
              <a className="result-link" href={apiUrl(job.video_url)} download="zlog.mp4">
                Download
              </a>
              <button
                type="button"
                className="result-again"
                onClick={() => {
                  setJob(null);
                  setBusy(false);
                  setReviewItems([]);
                  setReviewChoices({});
                  setReviewSaved(false);
                  setReviewError(null);
                }}
              >
                New film
              </button>
            </div>
            {job.generator && (
              <p className="result-meta">{job.generator === 'ai' ? 'AI select · taste profile' : 'Baseline cut'}</p>
            )}

            {reviewItems.length > 0 && !reviewSaved && (
              <div className="review">
                <p className="review-title">Train taste</p>
                <ul className="review-grid">
                  {reviewItems.map((item) => (
                    <li key={item.segment_id} className="review-card">
                      <img src={apiUrl(item.frame_url)} alt="" className="review-thumb" />
                      <div className="review-btns">
                        {(['keep', 'drop', 'skip'] as const).map((d) => (
                          <button
                            key={d}
                            type="button"
                            aria-pressed={reviewChoices[item.segment_id] === d}
                            className={`review-btn${reviewChoices[item.segment_id] === d ? ' review-btn--on' : ''}`}
                            onClick={() =>
                              setReviewChoices((prev) => ({...prev, [item.segment_id]: d}))
                            }
                          >
                            {d}
                          </button>
                        ))}
                      </div>
                    </li>
                  ))}
                </ul>
                <button
                  type="button"
                  className="review-save"
                  disabled={reviewSaving}
                  onClick={() => void saveReview()}
                >
                  {reviewSaving ? 'Saving…' : 'Save judgments'}
                </button>
                {reviewError && <p className="error-body" role="alert">{reviewError}</p>}
              </div>
            )}
            {reviewSaved && <p className="result-meta">Taste updated</p>}
          </div>
        )}

        {job?.status === 'error' && (
          <div className="error-box" aria-live="assertive">
            <p className="error-title">Couldn’t finish</p>
            <p className="error-body">{job.error || 'Something went wrong.'}</p>
            <button type="button" className="result-again" onClick={() => setJob(null)}>
              Try again
            </button>
          </div>
        )}
      </main>

      <footer className="dock">
        {files.length > 0 && (
          <ul className="attachments">
            {files.map((item) => (
              <li key={item.id} className="attachment">
                {item.kind === 'image' && item.previewUrl ? (
                  <img src={item.previewUrl} alt="" className="attachment-thumb" />
                ) : item.kind === 'video' && item.previewUrl ? (
                  <video src={item.previewUrl} className="attachment-thumb" muted />
                ) : (
                  <span className="attachment-icon">▶</span>
                )}
                <span className="attachment-name">{item.file.name}</span>
                <button
                  type="button"
                  className="attachment-remove"
                  onClick={() => removeFile(item.id)}
                  aria-label="Remove"
                  disabled={busy}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className="composer">
          <select
            aria-label="Quality mode"
            className="quality-select"
            value={qualityMode}
            disabled={busy}
            onChange={(e) =>
              setQualityMode(e.target.value as 'economy' | 'balanced' | 'premium')
            }
          >
            <option value="economy">economy</option>
            <option value="balanced">balanced</option>
            <option value="premium">premium</option>
          </select>
          <button
            type="button"
            className="icon-btn"
            aria-label="Attach"
            onClick={() => fileRef.current?.click()}
            disabled={busy}
          >
            <AttachIcon />
          </button>
          <textarea
            className="composer-input"
            placeholder="Drop photos, video, or write a note"
            rows={1}
            value={text}
            disabled={busy}
            onChange={(e) => {
              setText(e.target.value);
              const el = e.target;
              el.style.height = 'auto';
              el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
            }}
            onKeyDown={onKeyDown}
          />
          <button
            type="button"
            className={`send-btn${canSend ? ' send-btn--ready' : ''}`}
            aria-label="Create"
            disabled={!canSend}
            onClick={() => void onSubmit()}
          >
            <SendIcon />
          </button>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files?.length) addFiles(e.target.files);
            e.target.value = '';
          }}
        />
      </footer>
    </div>
  );
}

function AttachIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M21 12.5V17a5 5 0 0 1-10 0V7a3 3 0 1 1 6 0v9.5a1.5 1.5 0 1 1-3 0V8"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M12 19V5M12 5l-6 6M12 5l6 6"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
