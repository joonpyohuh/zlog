-- Durable project/editorial state. Large media remains in Supabase Storage.
create table public.projects (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  title text not null default 'Untitled trip',
  prompt text not null default '',
  status text not null default 'draft' check (status in ('draft', 'processing', 'ready', 'error')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.source_assets (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  storage_path text not null,
  upload_order integer not null check (upload_order >= 0),
  media_type text not null check (media_type in ('video', 'image')),
  duration_ms integer check (duration_ms is null or duration_ms > 0),
  capture_time timestamptz,
  created_at timestamptz not null default now(),
  unique (project_id, upload_order)
);

create table public.editorial_versions (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  schema_version text not null,
  theory_version text not null,
  trip_graph jsonb not null,
  editing_intent jsonb not null,
  highlight_decisions jsonb not null,
  created_at timestamptz not null default now()
);

create table public.outputs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  output_type text not null check (output_type in ('long', 'short')),
  status text not null default 'planned' check (status in ('planned', 'rendering', 'ready', 'error')),
  rendered_storage_path text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (project_id, output_type)
);

create table public.timeline_revisions (
  id uuid primary key default gen_random_uuid(),
  output_id uuid not null references public.outputs(id) on delete cascade,
  revision integer not null check (revision > 0),
  timeline jsonb not null,
  critic_report jsonb not null,
  created_at timestamptz not null default now(),
  unique (output_id, revision)
);

create table public.render_jobs (
  id uuid primary key default gen_random_uuid(),
  output_id uuid not null references public.outputs(id) on delete cascade,
  timeline_revision integer not null check (timeline_revision > 0),
  status text not null default 'queued' check (status in ('queued', 'running', 'ready', 'error')),
  error text,
  created_at timestamptz not null default now(),
  finished_at timestamptz
);

create table public.editing_feedback (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  output_type text not null check (output_type in ('long', 'short')),
  action text not null,
  before_value jsonb,
  after_value jsonb,
  created_at timestamptz not null default now()
);

create index projects_user_id_idx on public.projects(user_id, updated_at desc);
create index source_assets_project_id_idx on public.source_assets(project_id, upload_order);
create index editorial_versions_project_id_idx on public.editorial_versions(project_id, created_at desc);
create index timeline_revisions_output_id_idx on public.timeline_revisions(output_id, revision desc);
create index render_jobs_output_id_idx on public.render_jobs(output_id, created_at desc);
create index editing_feedback_project_id_idx on public.editing_feedback(project_id, created_at desc);

alter table public.projects enable row level security;
alter table public.source_assets enable row level security;
alter table public.editorial_versions enable row level security;
alter table public.outputs enable row level security;
alter table public.timeline_revisions enable row level security;
alter table public.render_jobs enable row level security;
alter table public.editing_feedback enable row level security;

create policy "Users manage own projects" on public.projects
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy "Users manage own source assets" on public.source_assets
  for all using (exists (select 1 from public.projects p where p.id = project_id and p.user_id = auth.uid()))
  with check (exists (select 1 from public.projects p where p.id = project_id and p.user_id = auth.uid()));

create policy "Users manage own editorial versions" on public.editorial_versions
  for all using (exists (select 1 from public.projects p where p.id = project_id and p.user_id = auth.uid()))
  with check (exists (select 1 from public.projects p where p.id = project_id and p.user_id = auth.uid()));

create policy "Users manage own outputs" on public.outputs
  for all using (exists (select 1 from public.projects p where p.id = project_id and p.user_id = auth.uid()))
  with check (exists (select 1 from public.projects p where p.id = project_id and p.user_id = auth.uid()));

create policy "Users manage own timeline revisions" on public.timeline_revisions
  for all using (exists (
    select 1 from public.outputs o join public.projects p on p.id = o.project_id
    where o.id = output_id and p.user_id = auth.uid()
  )) with check (exists (
    select 1 from public.outputs o join public.projects p on p.id = o.project_id
    where o.id = output_id and p.user_id = auth.uid()
  ));

create policy "Users manage own render jobs" on public.render_jobs
  for all using (exists (
    select 1 from public.outputs o join public.projects p on p.id = o.project_id
    where o.id = output_id and p.user_id = auth.uid()
  )) with check (exists (
    select 1 from public.outputs o join public.projects p on p.id = o.project_id
    where o.id = output_id and p.user_id = auth.uid()
  ));

create policy "Users manage own editing feedback" on public.editing_feedback
  for all using (exists (select 1 from public.projects p where p.id = project_id and p.user_id = auth.uid()))
  with check (exists (select 1 from public.projects p where p.id = project_id and p.user_id = auth.uid()));

insert into storage.buckets (id, name, public)
values ('project-media', 'project-media', false)
on conflict (id) do nothing;

create policy "Users manage own project media" on storage.objects
  for all using (bucket_id = 'project-media' and (storage.foldername(name))[1] = auth.uid()::text)
  with check (bucket_id = 'project-media' and (storage.foldername(name))[1] = auth.uid()::text);
