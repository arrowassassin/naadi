"""Seed content tables: pathway guides, plan templates, seed runs; source verification; media hashes; app_config notify.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-16

- `pathway_guides`: the long-form guides of data/pathway (`00-overview.md` = key `overview`, stage_key null;
  `NN-<stage>.md` = one per pathway stage). Shared content, read-only for clients (anon + authenticated).
- `plan_templates` + `plan_template_days`: study-plan templates (data/study_plan_step1_60d.csv is
  `step1-60d`), one row per day. Shared read-only; the plan generator (S11) reproduces a template for a
  student's inputs. Per-student state (status, score, notes) lives in `plan_items`, never here.
- `seed_runs`: status of admin seed jobs (POST /api/admin/seed). Admin-only: RLS forced, no client privileges.
- `sources.verified` / `verified_url`: whether the license terms were checked at a cited URL (sources.yaml).
- `media_assets.content_sha256`: hash of the stored (sanitised) object, so the importer skips unchanged uploads.
- `app_config.admin_edited_at` / `chapter_sections.admin_edited_at`: set when an admin (or the assistant, for
  sections) changes the row; the seed importer never overwrites such a row unless forced. A trigger sets it on
  sections whenever a non-seed revision is recorded.
- `naadi_app_config_notify()`: `pg_notify('naadi_app_config', key)` after every app_config write, so each API
  process drops its 60 s cache entry at once (the TTL still bounds staleness if a listener is down).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHARED_READ_ONLY = ("pathway_guides", "plan_templates", "plan_template_days")

UPGRADE = """
alter table public.sources
  add column verified boolean not null default false,
  add column verified_url text check (verified_url is null or verified_url ~ '^https://');

alter table public.app_config add column admin_edited_at timestamptz;
alter table public.chapter_sections add column admin_edited_at timestamptz;

create function public.naadi_mark_section_edited() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.origin <> 'seed' then
    update public.chapter_sections set admin_edited_at = coalesce(new.created_at, now()) where id = new.section_id;
  end if;
  return null;
end
$$;
revoke all on function public.naadi_mark_section_edited() from public, anon, authenticated;
create trigger chapter_section_revisions_mark_edited after insert on public.chapter_section_revisions
  for each row execute function public.naadi_mark_section_edited();

alter table public.media_assets
  add column content_sha256 text check (content_sha256 is null or content_sha256 ~ '^[0-9a-f]{64}$');

create table public.pathway_guides (
  key text primary key check (key ~ '^[a-z0-9_]{1,64}$'),
  stage_key text unique references public.pathway_stages(key) on delete cascade,
  sort smallint not null,
  title text not null check (char_length(title) between 1 and 200),
  body_md text not null,
  source_id text not null references public.sources(id) default 'naadi-original-v1',
  updated_at timestamptz not null default now()
);
create trigger pathway_guides_touch_updated_at before update on public.pathway_guides
  for each row execute function public.naadi_touch_updated_at();

create table public.plan_templates (
  id text primary key check (id ~ '^[a-z0-9-]{1,64}$'),
  exam text not null check (exam in ('step1','step2ck','step3')),
  title text not null,
  days smallint not null check (days between 1 and 366),
  source_id text not null references public.sources(id) default 'naadi-original-v1',
  updated_at timestamptz not null default now()
);
create trigger plan_templates_touch_updated_at before update on public.plan_templates
  for each row execute function public.naadi_touch_updated_at();

create table public.plan_template_days (
  template_id text not null references public.plan_templates(id) on delete cascade,
  day smallint not null check (day >= 1),
  example_date date,
  weekday text check (weekday in ('Mon','Tue','Wed','Thu','Fri','Sat','Sun')),
  week smallint check (week >= 1),
  phase text,
  chapter_ref text,
  chapter_ids text[] not null default '{}',
  topic text,
  chapter_questions text,
  qbank text,
  self_assessment text,
  admin_task text,
  extra_resource text,
  target_hours numeric(4,1) check (target_hours is null or target_hours between 0 and 24),
  primary key (template_id, day)
);

create table public.seed_runs (
  id uuid primary key default gen_random_uuid(),
  started_by uuid references auth.users(id) on delete set null,
  options jsonb not null default '{}' check (jsonb_typeof(options) = 'object'),
  status text not null default 'running' check (status in ('running','succeeded','failed')),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  report jsonb,
  error text check (error is null or char_length(error) <= 2000),
  check ((status = 'running') = (finished_at is null))
);
-- At most one seed job runs at a time (the importer also holds an advisory lock).
create unique index seed_runs_one_running_idx on public.seed_runs ((true)) where status = 'running';
create index seed_runs_started_idx on public.seed_runs (started_at desc);

create function public.naadi_app_config_notify() returns trigger
language plpgsql set search_path = '' as $$
begin
  perform pg_notify('naadi_app_config', coalesce(new.key, old.key));
  return null;
end
$$;
revoke all on function public.naadi_app_config_notify() from public, anon, authenticated;
create trigger app_config_notify after insert or update or delete on public.app_config
  for each row execute function public.naadi_app_config_notify();
"""


def _shared_read_only(table: str) -> str:
    t = f"public.{table}"
    return f"""
alter table {t} enable row level security;
revoke all on table {t} from anon, authenticated;
grant select on table {t} to anon, authenticated;
grant all on table {t} to service_role;
create policy {table}_read on {t} for select to anon, authenticated using (true);
"""


def upgrade() -> None:
    op.execute(UPGRADE)
    for table in SHARED_READ_ONLY:
        op.execute(_shared_read_only(table))
    op.execute("""
alter table public.seed_runs enable row level security;
alter table public.seed_runs force row level security;
revoke all on table public.seed_runs from anon, authenticated;
grant all on table public.seed_runs to service_role;
""")


def downgrade() -> None:
    op.execute("drop trigger if exists app_config_notify on public.app_config")
    op.execute("drop function if exists public.naadi_app_config_notify()")
    op.execute("drop trigger if exists chapter_section_revisions_mark_edited on public.chapter_section_revisions")
    op.execute("drop function if exists public.naadi_mark_section_edited()")
    op.execute("alter table public.chapter_sections drop column if exists admin_edited_at")
    op.execute("alter table public.app_config drop column if exists admin_edited_at")
    for table in ("seed_runs", "plan_template_days", "plan_templates", "pathway_guides"):
        op.execute(f"drop table if exists public.{table} cascade")
    op.execute("alter table public.media_assets drop column if exists content_sha256")
    op.execute("alter table public.sources drop column if exists verified_url, drop column if exists verified")
