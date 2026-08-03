-- profiles (lightweight entitlement mirror)
create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  plan text not null default 'free',
  is_pro boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  paddle_customer_id text,
  paddle_subscription_id text unique,
  paddle_transaction_id text,
  paddle_price_id text,
  plan text not null default 'free',
  status text not null default 'inactive',
  currency_code text,
  current_period_start timestamptz,
  current_period_end timestamptz,
  scheduled_change jsonb,
  canceled_at timestamptz,
  last_event_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists subscriptions_user_id_uidx on public.subscriptions (user_id);
create index if not exists subscriptions_paddle_customer_idx on public.subscriptions (paddle_customer_id);
create index if not exists subscriptions_status_idx on public.subscriptions (status);

create table if not exists public.paddle_webhook_events (
  event_id text primary key,
  event_type text not null,
  occurred_at timestamptz,
  processed_at timestamptz not null default now(),
  payload jsonb,
  processing_error text
);

alter table public.profiles enable row level security;
alter table public.subscriptions enable row level security;
alter table public.paddle_webhook_events enable row level security;

create policy "Users can read own profile"
  on public.profiles for select
  using (auth.uid() = id);

create policy "Users can read own subscription"
  on public.subscriptions for select
  using (auth.uid() = user_id);

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (id, email)
  values (new.id, new.email)
  on conflict (id) do nothing;
  insert into public.subscriptions (user_id, plan, status)
  values (new.id, 'free', 'inactive')
  on conflict (user_id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
