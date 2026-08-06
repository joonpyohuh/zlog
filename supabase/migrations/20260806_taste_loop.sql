-- Taste loop: four-way comparison rounds and per-moment clip feedback.
--
-- comparison_rounds is the important one: it is the training data for a
-- preference scorer later, so it stores the full candidate EditTimelines
-- (not just the axis values) and keeps unresolved / "none of these" rounds.
--
-- Both tables are founder-private. RLS is on with no anon policy: reads and
-- writes go through the service role only (pipeline/taste_loop/store.py).

create table if not exists public.comparison_rounds (
  id text primary key,
  media_set_id text not null,
  axis text not null,
  -- The four EditTimeline objects, in variant order.
  candidates jsonb not null default '[]'::jsonb,
  -- Per-variant render outcome, including failures (ok=false + error).
  renders jsonb not null default '[]'::jsonb,
  -- null both before a pick and when all four were rejected; `resolved`
  -- and `rejected_all` separate those two cases.
  winner_id text,
  resolved boolean not null default false,
  rejected_all boolean not null default false,
  style_profile_version integer,
  created_at timestamptz not null default now(),
  resolved_at timestamptz,
  constraint comparison_rounds_winner_xor_reject
    check (not (winner_id is not null and rejected_all))
);

create index if not exists comparison_rounds_axis_idx on public.comparison_rounds (axis);
create index if not exists comparison_rounds_media_set_idx on public.comparison_rounds (media_set_id);
create index if not exists comparison_rounds_created_idx on public.comparison_rounds (created_at desc);
create index if not exists comparison_rounds_resolved_idx on public.comparison_rounds (resolved);

create table if not exists public.clip_feedback (
  id text primary key,
  timeline_id text not null,
  -- Where the reviewer hit spacebar, in seconds into the rendered video.
  timestamp_sec double precision not null,
  -- Everything below is filled by code from the timeline JSON, never typed
  -- by a human. Unknown stays null rather than being guessed.
  clip_id text,
  shot_type text,
  active_presets jsonb not null default '[]'::jsonb,
  active_params jsonb not null default '{}'::jsonb,
  caption_active boolean not null default false,
  verdict text not null check (verdict in ('good', 'bad')),
  comment text,
  created_at timestamptz not null default now()
);

create index if not exists clip_feedback_timeline_idx on public.clip_feedback (timeline_id);
create index if not exists clip_feedback_verdict_idx on public.clip_feedback (verdict);
create index if not exists clip_feedback_created_idx on public.clip_feedback (created_at desc);

alter table public.comparison_rounds enable row level security;
alter table public.clip_feedback enable row level security;

-- No anon/authenticated policies on purpose: the service role bypasses RLS,
-- and nothing else should reach these tables.
