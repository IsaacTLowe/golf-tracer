-- Golf Tracer: initial schema.
-- Tables: profiles, upload_batches, clips, tracers, jobs. RLS on all of them; clients read
-- their own rows, and every write goes through the API/worker using the service role.

create type public.clip_status as enum (
  'uploading', 'queued', 'processing', 'review', 'needs_input', 'approved', 'rendering', 'failed'
);
create type public.tracer_source as enum ('auto', 'manual', 'edited');
create type public.job_kind as enum ('process', 'retrace', 'render');
create type public.job_status as enum ('queued', 'running', 'succeeded', 'failed');

-- ---------------------------------------------------------------------------------------------
-- profiles
-- ---------------------------------------------------------------------------------------------
create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  display_name text,
  default_tracer_style jsonb,
  created_at timestamptz not null default now()
);

create function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, display_name)
  values (
    new.id,
    coalesce(new.raw_user_meta_data ->> 'full_name', new.raw_user_meta_data ->> 'name', split_part(new.email, '@', 1))
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------------------------------------
-- upload_batches
-- ---------------------------------------------------------------------------------------------
create table public.upload_batches (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  clip_count integer not null check (clip_count between 1 and 50),
  created_at timestamptz not null default now()
);
create index upload_batches_user_idx on public.upload_batches (user_id, created_at desc);

-- ---------------------------------------------------------------------------------------------
-- clips
-- ---------------------------------------------------------------------------------------------
create table public.clips (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  batch_id uuid references public.upload_batches (id) on delete set null,
  original_filename text not null,
  status public.clip_status not null default 'uploading',
  error_code text,
  error_message text,
  raw_path text,
  normalized_path text,
  thumbnail_path text,
  render_path text,
  duration_s double precision,
  fps double precision,
  width integer,
  height integer,
  size_bytes bigint,
  -- Lowest-confidence auto tracers get a "check this tracer" badge in the library.
  needs_check boolean not null default false,
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index clips_user_created_idx on public.clips (user_id, created_at desc, id desc);
create index clips_user_status_idx on public.clips (user_id, status);
create index clips_batch_idx on public.clips (batch_id);

create function public.touch_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger clips_touch_updated_at
  before update on public.clips
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------------------------
-- tracers (versioned per clip)
-- ---------------------------------------------------------------------------------------------
create table public.tracers (
  id uuid primary key default gen_random_uuid(),
  clip_id uuid not null references public.clips (id) on delete cascade,
  version integer not null check (version >= 1),
  source public.tracer_source not null,
  data jsonb not null,
  confidence numeric(4, 3),
  is_current boolean not null default false,
  created_at timestamptz not null default now(),
  unique (clip_id, version)
);
create unique index tracers_one_current_per_clip on public.tracers (clip_id) where is_current;

-- ---------------------------------------------------------------------------------------------
-- jobs (work queue; no user-facing policies)
-- ---------------------------------------------------------------------------------------------
create table public.jobs (
  id uuid primary key default gen_random_uuid(),
  clip_id uuid not null references public.clips (id) on delete cascade,
  kind public.job_kind not null,
  status public.job_status not null default 'queued',
  attempts integer not null default 0,
  max_attempts integer not null default 3,
  payload jsonb not null default '{}'::jsonb,
  run_after timestamptz not null default now(),
  locked_at timestamptz,
  locked_by text,
  last_error text,
  -- clock_timestamp so jobs queued in one transaction keep their order (claims are oldest first).
  created_at timestamptz not null default clock_timestamp(),
  finished_at timestamptz
);
create index jobs_claim_idx on public.jobs (run_after, created_at) where status = 'queued';
create index jobs_running_idx on public.jobs (locked_at) where status = 'running';
create index jobs_clip_idx on public.jobs (clip_id);

-- Wake idle workers immediately when a job is queued.
create function public.notify_job_queued()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.status = 'queued' then
    perform pg_notify('jobs_queued', new.kind::text);
  end if;
  return new;
end;
$$;

create trigger jobs_notify_queued
  after insert or update of status on public.jobs
  for each row execute function public.notify_job_queued();

-- ---------------------------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------------------------
alter table public.profiles enable row level security;
alter table public.upload_batches enable row level security;
alter table public.clips enable row level security;
alter table public.tracers enable row level security;
alter table public.jobs enable row level security;

create policy "read own profile" on public.profiles
  for select to authenticated using (id = (select auth.uid()));

create policy "read own batches" on public.upload_batches
  for select to authenticated using (user_id = (select auth.uid()));

create policy "read own clips" on public.clips
  for select to authenticated using (user_id = (select auth.uid()));

create policy "read own tracers" on public.tracers
  for select to authenticated using (
    exists (select 1 from public.clips c where c.id = tracers.clip_id and c.user_id = (select auth.uid()))
  );

-- Clients never write these tables directly; the API and worker use the service role.
revoke insert, update, delete on public.profiles, public.upload_batches, public.clips, public.tracers
  from anon, authenticated;
revoke all on public.jobs from anon, authenticated;

-- ---------------------------------------------------------------------------------------------
-- Realtime: live clip status in the library
-- ---------------------------------------------------------------------------------------------
alter publication supabase_realtime add table public.clips;

-- ---------------------------------------------------------------------------------------------
-- Storage: private buckets; users may only upload into raw/{their user id}/...
-- ---------------------------------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values
  ('raw', 'raw', false, 524288000, array['video/mp4', 'video/quicktime', 'video/webm']),
  ('media', 'media', false, 524288000, null),
  ('renders', 'renders', false, 524288000, null)
on conflict (id) do update
  set public = excluded.public,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

create policy "upload own raw clips" on storage.objects
  for insert to authenticated
  with check (bucket_id = 'raw' and (storage.foldername(name))[1] = (select auth.uid())::text);

-- TUS resumable uploads read upload state and may overwrite a partially uploaded object.
create policy "read own raw clips" on storage.objects
  for select to authenticated
  using (bucket_id = 'raw' and (storage.foldername(name))[1] = (select auth.uid())::text);

create policy "update own raw clips" on storage.objects
  for update to authenticated
  using (bucket_id = 'raw' and (storage.foldername(name))[1] = (select auth.uid())::text)
  with check (bucket_id = 'raw' and (storage.foldername(name))[1] = (select auth.uid())::text);
