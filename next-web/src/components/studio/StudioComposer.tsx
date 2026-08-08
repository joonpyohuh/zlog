'use client';

import {useCallback, useEffect, useRef, useState, type KeyboardEvent} from 'react';

import {EditorialWorkspace} from './EditorialWorkspace';

type Attachment = {
  id: string;
  file: File;
  kind: 'image' | 'video' | 'other';
  previewUrl?: string;
};

type TelemetryRow = {
  stage: string;
  provider?: string | null;
  model?: string | null;
  latency_ms?: number;
  retry_count?: number;
  input_tokens?: number;
  output_tokens?: number;
  estimated_cost_usd?: number;
  escalated?: boolean;
};

type JobStatus = {
  id: string;
  status: 'queued' | 'running' | 'done' | 'error';
  stage?: string;
  error?: string;
  video_url?: string;
  long_video_url?: string;
  short_video_url?: string;
  generator?: string;
  duration_s?: number;
  progress_pct?: number;
  progress?: string;
  quality_mode?: string;
  estimated_cost_usd_total?: number;
  telemetry?: TelemetryRow[];
  dev_mode?: boolean;
};

type QualityMode = 'economy' | 'balanced' | 'premium';

const STAGE_COPY: Record<string, string> = {
  queued: 'In queue',
  prepare: 'Preparing media',
  beats: 'Reading music',
  split: 'Finding scenes',
  evidence: 'Evidence frames',
  features: 'Deterministic features',
  filter: 'Cleaning shots',
  sheet: 'Contact sheets',
  analyze: 'Asset analysis',
  director: 'Story plan',
  plan: 'Timeline plan',
  evaluate: 'Plan evaluation',
  select: 'Baseline cut',
  render: 'Rendering film',
  grade: 'Color grade',
  audio: 'Audio mix',
  render_long: 'Rendering long film',
  render_short: 'Rendering short film',
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

export function StudioComposer({
  apiBase,
  devModeDefault = false,
  initialJobId,
}: {
  apiBase: string;
  devModeDefault?: boolean;
  initialJobId?: string;
}) {
  const base = apiBase.replace(/\/$/, '');
  const apiUrl = useCallback((path: string) => {
    if (/^https?:\/\//i.test(path)) return path;
    return `${base}${path.startsWith('/') ? path : `/${path}`}`;
  }, [base]);

  const [text, setText] = useState('');
  const [files, setFiles] = useState<Attachment[]>([]);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<JobStatus | null>(
    initialJobId ? {id: initialJobId, status: 'running', stage: 'prepare'} : null,
  );
  const [quality, setQuality] = useState<QualityMode>('balanced');
  const [devMode, setDevMode] = useState(devModeDefault);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    return () => {
      for (const f of files) {
        if (f.previewUrl) URL.revokeObjectURL(f.previewUrl);
      }
    };
  }, [files]);

  useEffect(() => {
    if (!job || job.status === 'done' || job.status === 'error') return;
    const t = window.setInterval(async () => {
      const res = await fetch(apiUrl(`/api/jobs/${job.id}`));
      if (!res.ok) return;
      const next = (await res.json()) as JobStatus;
      setJob(next);
      if (next.status === 'done' || next.status === 'error') {
        setBusy(false);
      }
    }, 1200);
    return () => window.clearInterval(t);
  }, [job, apiUrl]);

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

  async function onSubmit() {
    if (busy) return;
    if (!text.trim() && files.length === 0) return;
    if (!base) {
      setJob({
        id: 'local',
        status: 'error',
        error: 'ZLOG_API_BASE is not configured for this Studio deploy.',
      });
      return;
    }

    setBusy(true);
    setJob(null);
    const body = new FormData();
    body.append('note', text.trim());
    body.append('quality_mode', quality);
    body.append('dev_mode', devMode ? '1' : '0');
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

  const canSend = !busy && (text.trim().length > 0 || files.length > 0);

  return (
    <div className="mt-8 space-y-6">
      <div className="flex flex-wrap items-end gap-4">
        <label className="block text-sm text-neutral-400">
          Quality
          <select
            className="mt-1 block rounded-lg border border-neutral-800 bg-black px-3 py-2 text-white"
            value={quality}
            disabled={busy}
            onChange={(e) => setQuality(e.target.value as QualityMode)}
          >
            <option value="economy">economy — Claude only, no GPT</option>
            <option value="balanced">balanced — Luna eval, no Sol</option>
            <option value="premium">premium — Luna + Sol if needed</option>
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm text-neutral-400">
          <input
            type="checkbox"
            checked={devMode}
            disabled={busy}
            onChange={(e) => setDevMode(e.target.checked)}
          />
          Dev telemetry
        </label>
      </div>

      {job && (job.status === 'queued' || job.status === 'running') && (
        <div className="rounded-2xl border border-neutral-800 bg-black/60 p-5" aria-live="polite">
          <p className="text-lg text-white">
            {stageLabel(job.stage)}
            {job.stage === 'render' && typeof job.progress_pct === 'number'
              ? ` · ${job.progress_pct}%`
              : ''}
          </p>
          <p className="mt-1 text-sm text-neutral-500">
            {job.quality_mode || quality} · {job.generator || 'editorial'}
            {job.duration_s ? ` · ~${Math.round(job.duration_s)}s` : ''}
            {typeof job.estimated_cost_usd_total === 'number'
              ? ` · ≈$${job.estimated_cost_usd_total.toFixed(4)}`
              : ''}
          </p>
        </div>
      )}

      {devMode && job?.telemetry && job.telemetry.length > 0 && (
        <div className="overflow-x-auto rounded-2xl border border-neutral-800">
          <table className="w-full text-left text-xs text-neutral-400">
            <thead className="border-b border-neutral-800 text-neutral-500">
              <tr>
                <th className="px-3 py-2">stage</th>
                <th className="px-3 py-2">provider</th>
                <th className="px-3 py-2">model</th>
                <th className="px-3 py-2">ms</th>
                <th className="px-3 py-2">retry</th>
                <th className="px-3 py-2">tokens</th>
                <th className="px-3 py-2">cost</th>
                <th className="px-3 py-2">esc</th>
              </tr>
            </thead>
            <tbody>
              {job.telemetry.map((row, i) => (
                <tr key={`${row.stage}-${i}`} className="border-b border-neutral-900">
                  <td className="px-3 py-2 text-neutral-200">{row.stage}</td>
                  <td className="px-3 py-2">{row.provider || '—'}</td>
                  <td className="px-3 py-2">{row.model || '—'}</td>
                  <td className="px-3 py-2">{row.latency_ms ?? '—'}</td>
                  <td className="px-3 py-2">{row.retry_count ?? 0}</td>
                  <td className="px-3 py-2">
                    {(row.input_tokens || 0) + (row.output_tokens || 0) || '—'}
                  </td>
                  <td className="px-3 py-2">
                    {typeof row.estimated_cost_usd === 'number'
                      ? `$${row.estimated_cost_usd.toFixed(4)}`
                      : '—'}
                  </td>
                  <td className="px-3 py-2">{row.escalated ? 'yes' : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="px-3 py-2 text-[11px] text-neutral-600">
            Dev view — no API keys or system prompts are shown.
          </p>
        </div>
      )}

      {job?.status === 'done' && job.video_url && (
        <div className="space-y-3">
          <video
            className="aspect-[9/16] w-full max-w-sm rounded-xl bg-black"
            src={apiUrl(job.video_url)}
            controls
            playsInline
          />
          <div className="flex flex-wrap gap-3">
            <a
              className="text-sm text-white underline"
              href={apiUrl(job.video_url)}
              download="zlog.mp4"
            >
              Download
            </a>
            <button
              type="button"
              className="text-sm text-neutral-400 underline"
              onClick={() => {
                setJob(null);
                setBusy(false);
              }}
            >
              New film
            </button>
          </div>
          <EditorialWorkspace
            apiBase={base}
            jobId={job.id}
            onRenderQueued={(output) => {
              setBusy(true);
              setJob((current) =>
                current
                  ? {...current, status: 'running', stage: `render_${output}`}
                  : current,
              );
            }}
          />
        </div>
      )}

      {job?.status === 'error' && (
        <div className="rounded-2xl border border-red-900/50 bg-red-950/30 p-4 text-sm text-red-200">
          {job.error || 'Something went wrong.'}
        </div>
      )}

      {files.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {files.map((item) => (
            <li
              key={item.id}
              className="relative h-16 w-16 overflow-hidden rounded-lg border border-neutral-800"
            >
              {item.previewUrl && item.kind === 'image' ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={item.previewUrl} alt="" className="h-full w-full object-cover" />
              ) : (
                <span className="flex h-full items-center justify-center text-xs text-neutral-500">
                  vid
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-end gap-2 rounded-2xl border border-neutral-800 bg-black/70 p-3">
        <button
          type="button"
          className="rounded-lg border border-neutral-700 px-3 py-2 text-sm text-neutral-300"
          onClick={() => fileRef.current?.click()}
          disabled={busy}
        >
          Attach
        </button>
        <textarea
          className="min-h-[44px] flex-1 resize-none bg-transparent text-sm text-white outline-none"
          placeholder="Drop photos, video, or write a note"
          rows={2}
          value={text}
          disabled={busy}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button
          type="button"
          className="rounded-lg bg-white px-4 py-2 text-sm font-medium text-black disabled:opacity-40"
          disabled={!canSend}
          onClick={() => void onSubmit()}
        >
          Create
        </button>
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
      </div>
    </div>
  );
}
