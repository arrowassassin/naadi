"""Admin console (segment 19): draft/published section revisions, admin audit log, question revisions.

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-17

- `chapter_section_revisions` gains `status` ('draft' | 'published'), `parent_revision_id` (the published revision
  a draft was started from, for the editor's stale-draft check and the diff), `restored_from_id` (a rollback
  copies that revision), `updated_at` (autosave bumps it;
  the editor sends it back for optimistic concurrency), `published_at` / `published_by`. Rows written before
  this migration were live bodies once, so they are 'published' with `published_at = created_at`.
  - At most one draft per section (partial unique index): the editor autosaves into it.
  - A published revision is immutable (trigger `naadi_revision_guard`): only a draft can change, and only into
    'published'. Rollback is a new revision copying an old one.
  - The live body is the latest published revision: publishing a non-seed revision copies its body into
    `chapter_sections.body` / `body_html` in the same statement (trigger `naadi_publish_section_revision`).
    Seed rows keep their meaning (the body a seed import replaced) and never touch the live body.
  - The table stays backend-only (0001): drafts are never readable by a client.
- `admin_audit` (backend-only, RLS forced, no client privileges): one row per admin write: actor, action,
  ref_type, ref_id, sha256 of the row before and after, an optional note, at.
- `question_revisions` (backend-only): a full `questions` row snapshot per edit, numbered 1, 2, … per question
  (`version`, set by the insert trigger `naadi_question_revision_version`), from the admin editor, the authoring
  pipeline (author / review) or the seed; for audit and rollback.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
-- section revisions: draft / published
alter table public.chapter_section_revisions
  add column status text not null default 'published' check (status in ('draft','published')),
  add column parent_revision_id bigint references public.chapter_section_revisions(id) on delete set null,
  add column updated_at timestamptz not null default now(),
  add column published_at timestamptz,
  add column published_by uuid references auth.users(id) on delete set null,
  add column restored_from_id bigint references public.chapter_section_revisions(id) on delete set null;
update public.chapter_section_revisions set published_at = created_at, updated_at = created_at;
alter table public.chapter_section_revisions
  add constraint chapter_section_revisions_published_shape check ((status = 'published') = (published_at is not null)),
  add constraint chapter_section_revisions_body_object check (body is null or jsonb_typeof(body) = 'object'),
  add constraint chapter_section_revisions_note_length check (note is null or char_length(note) <= 500),
  add constraint chapter_section_revisions_draft_origin check (status = 'published' or origin <> 'seed');
create unique index chapter_section_revisions_one_draft on public.chapter_section_revisions (section_id)
  where status = 'draft';
create index chapter_section_revisions_published_idx on public.chapter_section_revisions (section_id, published_at desc)
  where status = 'published';
create index chapter_section_revisions_parent_idx on public.chapter_section_revisions (parent_revision_id)
  where parent_revision_id is not null;

create function public.naadi_revision_guard() returns trigger
language plpgsql set search_path = '' as $$
begin
  if tg_op = 'INSERT' then
    -- the seed importer and older writers insert without these columns
    if new.status = 'published' and new.published_at is null then
      new.published_at := coalesce(new.created_at, now());
    end if;
    return new;
  end if;
  if old.status = 'published' then
    -- only the foreign keys' ON DELETE SET NULL may touch a published row
    if row(new.section_id, new.body, new.body_html, new.origin, new.note, new.status, new.created_at,
           new.published_at, new.updated_at)
       is distinct from row(old.section_id, old.body, old.body_html, old.origin, old.note, old.status,
           old.created_at, old.published_at, old.updated_at)
       or (new.author_user_id is distinct from old.author_user_id and new.author_user_id is not null)
       or (new.published_by is distinct from old.published_by and new.published_by is not null)
       or (new.parent_revision_id is distinct from old.parent_revision_id and new.parent_revision_id is not null)
       or (new.restored_from_id is distinct from old.restored_from_id and new.restored_from_id is not null)
    then
      raise exception 'a published section revision is immutable (id %)', old.id using errcode = 'check_violation';
    end if;
    return new;
  end if;
  if new.section_id <> old.section_id or new.origin <> old.origin or new.created_at <> old.created_at then
    raise exception 'a revision keeps its section, origin and creation time' using errcode = 'check_violation';
  end if;
  new.updated_at := now();
  if new.status = 'published' and new.published_at is null then
    new.published_at := now();
  end if;
  return new;
end
$$;
create trigger chapter_section_revisions_guard before insert or update on public.chapter_section_revisions
  for each row execute function public.naadi_revision_guard();

create function public.naadi_publish_section_revision() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.status = 'published' and new.origin <> 'seed'
     and (tg_op = 'INSERT' or old.status is distinct from new.status) then
    update public.chapter_sections
       set body = new.body, body_html = new.body_html
     where id = new.section_id;
  end if;
  return null;
end
$$;
create trigger chapter_section_revisions_publish after insert or update of status on public.chapter_section_revisions
  for each row execute function public.naadi_publish_section_revision();

revoke all on function public.naadi_revision_guard() from public, anon, authenticated;
revoke all on function public.naadi_publish_section_revision() from public, anon, authenticated;

-- admin audit log (backend-only)
create table public.admin_audit (
  id bigint generated by default as identity primary key,
  actor_user_id uuid references auth.users(id) on delete set null,
  action text not null check (action ~ '^[a-z][a-z0-9_]*(\\.[a-z0-9_]+)+$' and char_length(action) <= 64),
  ref_type text not null check (ref_type ~ '^[a-z][a-z0-9_]{0,47}$'),
  ref_id text not null check (char_length(ref_id) between 1 and 200),
  before_hash text check (before_hash is null or before_hash ~ '^[0-9a-f]{64}$'),
  after_hash text check (after_hash is null or after_hash ~ '^[0-9a-f]{64}$'),
  note text check (note is null or char_length(note) <= 1000),
  at timestamptz not null default now()
);
create index admin_audit_ref_idx on public.admin_audit (ref_type, ref_id, at desc);
create index admin_audit_at_idx on public.admin_audit (at desc);
alter table public.admin_audit enable row level security;
alter table public.admin_audit force row level security;
revoke all on table public.admin_audit from anon, authenticated;
revoke all on sequence public.admin_audit_id_seq from anon, authenticated;

-- question revisions (backend-only)
create table public.question_revisions (
  id bigint generated by default as identity primary key,
  question_id text not null references public.questions(id) on delete cascade,
  version int not null default 0,
  snapshot jsonb not null check (jsonb_typeof(snapshot) = 'object'),
  author_user_id uuid references auth.users(id) on delete set null,
  origin text not null check (origin in ('admin','author','review','seed')),
  note text check (note is null or char_length(note) <= 500),
  created_at timestamptz not null default now(),
  unique (question_id, version)
);
-- version is numbered per question by the database, so every writer (admin editor, authoring review) agrees
create function public.naadi_question_revision_version() returns trigger
language plpgsql set search_path = '' as $$
begin
  perform pg_advisory_xact_lock(hashtextextended('naadi.question_revisions.' || new.question_id, 0));
  select coalesce(max(version), 0) + 1 into new.version
    from public.question_revisions where question_id = new.question_id;
  return new;
end
$$;
create trigger question_revisions_version before insert on public.question_revisions
  for each row execute function public.naadi_question_revision_version();
revoke all on function public.naadi_question_revision_version() from public, anon, authenticated;
alter table public.question_revisions enable row level security;
alter table public.question_revisions force row level security;
revoke all on table public.question_revisions from anon, authenticated;
revoke all on sequence public.question_revisions_id_seq from anon, authenticated;
"""

DOWNGRADE_TABLES = """
drop table if exists public.question_revisions;
drop function if exists public.naadi_question_revision_version();
drop table if exists public.admin_audit;
"""

DOWNGRADE = """
drop trigger if exists chapter_section_revisions_publish on public.chapter_section_revisions;
drop trigger if exists chapter_section_revisions_guard on public.chapter_section_revisions;
drop function if exists public.naadi_publish_section_revision();
drop function if exists public.naadi_revision_guard();
-- drafts have no meaning before 0024
delete from public.chapter_section_revisions where status = 'draft';
drop index if exists public.chapter_section_revisions_parent_idx;
drop index if exists public.chapter_section_revisions_published_idx;
drop index if exists public.chapter_section_revisions_one_draft;
alter table public.chapter_section_revisions
  drop constraint if exists chapter_section_revisions_draft_origin,
  drop constraint if exists chapter_section_revisions_note_length,
  drop constraint if exists chapter_section_revisions_body_object,
  drop constraint if exists chapter_section_revisions_published_shape,
  drop column if exists restored_from_id,
  drop column if exists published_by,
  drop column if exists published_at,
  drop column if exists updated_at,
  drop column if exists parent_revision_id,
  drop column if exists status;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE_TABLES)
    op.execute(DOWNGRADE)
