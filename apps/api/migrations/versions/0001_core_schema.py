"""Core schema: every Naadi table, its indexes, triggers, grants and RLS policies.

Revision ID: 0001
Revises:
Create Date: 2026-11-16

Access model (see db/schema.sql for the documented contract):
- Default privileges: tables, sequences and functions that `postgres` creates in `public` from now on
  (this migration's, Aegra's, any later one's) grant nothing to `anon` / `authenticated`; every grant to a
  client role below is explicit.
- User-owned tables: RLS enabled and forced; `select/insert/update/delete` policies `to authenticated` with
  `(select auth.uid()) = user_id`; `anon` has no privileges at all. Insert/update checks also require every
  referenced shared row (question, flashcard, pathway task/stage) to be visible to the caller, so a foreign
  key cannot be used as an oracle for hidden content. Grading, moderation and pipeline columns
  (`attempts.correct`, `blocks.score/finished_at`, `uploads.status/processed_chapter_id`,
  `generated_media.approved_for_publish/ai_generated`, `report_issues.status`) are backend-only.
- Shared content: RLS on; `select` to `anon, authenticated` only for published / approved rows, or the
  caller's own private rows (`owner_user_id`). Writes by the service role only (the backend), except that a
  student may write their own private flashcards with `origin = 'user'`.
- `questions`: clients get column-level `select` only; answers, explanations, educational objective,
  review metadata and answer statistics are not readable by `anon` / `authenticated` at all.
- `embeddings` and admin-only tables: RLS enabled and forced, no client privileges; service role only
  (retrieval runs server-side; chunks of questions and drafts would otherwise leak answers).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import NamedTuple

from alembic import op
from sqlalchemy import text

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UID = "(select auth.uid())"


class UserTable(NamedTuple):
    """Columns `authenticated` may insert / update (None = all) and extra insert/update checks."""

    insert: tuple[str, ...] | None = None
    update: tuple[str, ...] | None = None
    # Referenced shared rows must be visible to the caller (the sub-select runs under the caller's RLS).
    refs: str | None = None


USER_TABLES: dict[str, UserTable] = {
    "profiles": UserTable(
        refs="pathway_stage is null"
        " or exists (select 1 from public.pathway_stages p where p.key = profiles.pathway_stage)"
    ),
    # The API grades: `correct` is backend-only.
    "attempts": UserTable(
        insert=("user_id", "question_id", "block_id", "chosen", "seconds", "flagged", "mode"),
        update=("flagged",),
        refs="exists (select 1 from public.questions p where p.id = attempts.question_id)",
    ),
    # `score` and `finished_at` are set by the API when it grades the block.
    "blocks": UserTable(
        insert=("id", "user_id", "config", "question_ids", "started_at"),
        update=("started_at",),
    ),
    "card_reviews": UserTable(refs="exists (select 1 from public.flashcards p where p.id = card_reviews.card_id)"),
    "notes": UserTable(),
    "notebooks": UserTable(),
    "ink_layers": UserTable(),
    # `status` and `processed_chapter_id` belong to the ingest pipeline.
    "uploads": UserTable(
        insert=("id", "user_id", "kind", "storage_path", "original_name", "bytes"),
        update=("original_name",),
    ),
    "plan_items": UserTable(),
    "user_pathway_tasks": UserTable(
        refs="exists (select 1 from public.pathway_tasks p where p.id = user_pathway_tasks.task_id)"
    ),
    "vault_documents": UserTable(),
    "costs": UserTable(
        refs="task_id is null or exists (select 1 from public.pathway_tasks p where p.id = costs.task_id)"
    ),
    "assistant_threads": UserTable(),
    "kb_sources": UserTable(),
    "ingest_items": UserTable(),
    "generated_media": UserTable(
        insert=("id", "user_id", "kind", "title", "prompt", "model", "storage_path", "width", "height", "duration_s"),
        update=("title",),
    ),
    "report_issues": UserTable(insert=("id", "user_id", "ref_type", "ref_id", "body"), update=("body",)),
}

# Shared content and the row filter for client reads: one expression for `anon, authenticated`, or a
# per-role mapping when anon must see less (and cannot reference user tables it has no privileges on).
SHARED_TABLES: dict[str, str | dict[str, str]] = {
    "sources": "true",
    "taxonomy_nodes": "true",
    "pathway_stages": "true",
    "pathway_tasks": "true",
    "chapters": f"(owner_user_id is null and status = 'published') or owner_user_id = {UID}",
    # The sub-selects run with the caller's privileges, so chapters' own policy applies to them.
    "chapter_sections": "exists (select 1 from public.chapters c where c.id = chapter_sections.chapter_id)",
    "media_assets": "review_status = 'approved'",
    "section_media": (
        "exists (select 1 from public.chapter_sections s where s.id = section_media.section_id)"
        " and exists (select 1 from public.media_assets m where m.id = section_media.media_id)"
    ),
    "questions": (
        "status = 'published' and (chapter_id is null"
        " or exists (select 1 from public.chapters c where c.id = questions.chapter_id))"
    ),
    # A shared card linked to a question restates its answer, so it becomes visible only after the caller has
    # attempted that question (the attempts sub-select runs under attempts' own-rows RLS); never to anon.
    "flashcards": {
        "anon": "owner_user_id is null and question_id is null",
        "authenticated": (
            f"owner_user_id = {UID} or (owner_user_id is null and (question_id is null or exists"  # noqa: S608 - module constants only
            f" (select 1 from public.attempts a where a.user_id = {UID} and a.question_id = flashcards.question_id)))"
        ),
    },
}

ADMIN_TABLES = (
    "app_config",
    "app_config_history",
    "chapter_section_revisions",
    "search_cache",
    "llm_usage",
    "embeddings",
)

CLIENT_ROLES = "anon, authenticated"
DEFAULT_PRIVILEGE_OBJECTS = ("tables", "sequences", "functions")

# Columns of `questions` that clients may select. Everything else is served by the API after an attempt.
QUESTION_CLIENT_COLUMNS = (
    "id",
    "chapter_id",
    "exam",
    "block",
    "stem_html",
    "options",
    "difficulty",
    "taxonomy_ids",
    "tags",
    "source_id",
    "status",
    "created_at",
)

UPDATED_AT_TABLES = (
    "profiles",
    "chapters",
    "chapter_sections",
    "notes",
    "ink_layers",
    "uploads",
    "kb_sources",
    "ingest_items",
)

# Creation order (drop order is the reverse).
ALL_TABLES = (
    "sources",
    "taxonomy_nodes",
    "pathway_stages",
    "pathway_tasks",
    "profiles",
    "chapters",
    "chapter_sections",
    "chapter_section_revisions",
    "media_assets",
    "section_media",
    "questions",
    "flashcards",
    "embeddings",
    "blocks",
    "attempts",
    "card_reviews",
    "notebooks",
    "notes",
    "ink_layers",
    "uploads",
    "kb_sources",
    "ingest_items",
    "plan_items",
    "user_pathway_tasks",
    "vault_documents",
    "costs",
    "assistant_threads",
    "generated_media",
    "report_issues",
    "app_config",
    "app_config_history",
    "search_cache",
    "llm_usage",
)


def _schema_sql(vector_schema: str) -> str:
    v = vector_schema  # quoted identifier read from pg_extension, not user input
    return f"""
-- ───────────── Functions ─────────────
create function public.naadi_touch_updated_at() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.updated_at := now();
  return new;
end
$$;

-- ───────────── Content sources, taxonomy, pathway ─────────────
create table public.sources (
  id text primary key,
  name text not null,
  url text,
  license text not null,
  allow_copy boolean not null default false,
  allow_modify boolean not null default false,
  allow_commercial boolean not null default false,
  allow_ai_ingest boolean not null default false,
  attribution_template text,
  notes text
);

create table public.taxonomy_nodes (
  id text primary key,
  kind text not null check (kind in ('exam','system','discipline','competency','clinical_science','topic')),
  parent_id text references public.taxonomy_nodes(id) on delete cascade,
  label text not null,
  exam text[] not null,
  weight_min numeric,
  weight_max numeric,
  sort smallint not null default 0,
  check (weight_min is null or weight_max is null or weight_min <= weight_max)
);
create index taxonomy_nodes_parent_idx on public.taxonomy_nodes (parent_id);

create table public.pathway_stages (
  key text primary key,
  sort smallint,
  title text,
  summary_md text,
  official_links jsonb,
  last_verified date
);

create table public.pathway_tasks (
  id text primary key,
  stage_key text not null references public.pathway_stages(key) on delete cascade,
  sort smallint,
  title text,
  detail_md text,
  due_rule text,
  cost_usd numeric,
  official_url text,
  last_verified date
);
create index pathway_tasks_stage_idx on public.pathway_tasks (stage_key, sort);

-- ───────────── Users ─────────────
create table public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  display_name text check (char_length(display_name) between 1 and 120),
  exam_target text not null default 'step1' check (exam_target in ('step1','step2ck','step3')),
  exam_date date,
  daily_hours numeric(4,1) not null default 8 check (daily_hours between 0 and 24),
  study_days smallint[] not null default '{{1,2,3,4,5,6}}' check (study_days <@ '{{1,2,3,4,5,6,7}}'::smallint[]),
  timezone text not null default 'Asia/Kolkata' check (char_length(timezone) between 1 and 64),
  pathway_stage text references public.pathway_stages(key) on delete set null,
  settings jsonb not null default '{{}}' check (jsonb_typeof(settings) = 'object'),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ───────────── Content library ─────────────
create table public.chapters (
  id text primary key,
  exam text not null,
  kind text not null check (kind in ('chapter','mock_exam','pathway','user_upload','user_kb')),
  title text not null,
  meta text,
  sort smallint,
  status text not null default 'published' check (status in ('draft','in_review','published','retired')),
  owner_user_id uuid references auth.users(id) on delete cascade,
  source_id text references public.sources(id) default 'naadi-original-v1',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  -- private chapters (uploads, knowledge-base documents) always have an owner; shared ones never do
  check ((kind in ('user_upload','user_kb')) = (owner_user_id is not null))
);
create index chapters_owner_idx on public.chapters (owner_user_id) where owner_user_id is not null;

create table public.chapter_sections (
  id text primary key,
  chapter_id text not null references public.chapters(id) on delete cascade,
  sort smallint not null,
  heading text not null,
  body jsonb,
  body_html text,
  reading_minutes smallint,
  taxonomy_ids text[] not null default '{{}}',
  updated_at timestamptz not null default now()
);
create index chapter_sections_chapter_idx on public.chapter_sections (chapter_id, sort);

create table public.chapter_section_revisions (
  id bigint generated by default as identity primary key,
  section_id text not null references public.chapter_sections(id) on delete cascade,
  body jsonb,
  body_html text,
  author_user_id uuid references auth.users(id) on delete set null,
  origin text not null check (origin in ('admin','assistant','seed')),
  note text,
  created_at timestamptz not null default now()
);
create index chapter_section_revisions_section_idx on public.chapter_section_revisions (section_id, created_at desc);

create table public.media_assets (
  id text primary key,
  kind text not null check (kind in ('svg','image','video_own','video_youtube','audio','pdf','link')),
  source_id text not null references public.sources(id),
  title text,
  caption text,
  url text,
  youtube_id text,
  channel_id text,
  duration_s int check (duration_s is null or duration_s >= 0),
  embeddable boolean,
  metadata_fetched_at timestamptz,
  attribution text,
  taxonomy_ids text[] not null default '{{}}',
  review_status text not null default 'unreviewed' check (review_status in ('unreviewed','approved','rejected'))
);

create table public.section_media (
  section_id text not null references public.chapter_sections(id) on delete cascade,
  media_id text not null references public.media_assets(id) on delete cascade,
  sort smallint,
  start_s int check (start_s is null or start_s >= 0),
  end_s int check (end_s is null or start_s is null or end_s > start_s),
  primary key (section_id, media_id)
);
create index section_media_media_idx on public.section_media (media_id);

create table public.questions (
  id text primary key,
  chapter_id text references public.chapters(id),
  exam text not null,
  block smallint,
  stem_html text not null,
  options jsonb not null check (jsonb_typeof(options) = 'array'),
  answer char(1) not null check (answer between 'A' and 'J'),
  answer_html text,
  explanation_html text,
  why_not_html text,
  educational_objective text,
  difficulty text check (difficulty in ('easy','medium','hard')),
  taxonomy_ids text[] not null default '{{}}',
  tags text[] not null default '{{}}',
  source_id text references public.sources(id) default 'naadi-original-v1',
  status text not null default 'published'
    check (status in ('draft','auto_checked','in_review','published','retired')),
  review jsonb not null default '{{}}',
  stats jsonb not null default '{{}}',
  created_at timestamptz not null default now()
);
create index questions_chapter_idx on public.questions (chapter_id);
create index questions_taxonomy_idx on public.questions using gin (taxonomy_ids);

create table public.flashcards (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid references auth.users(id) on delete cascade,
  deck text not null default 'Naadi core',
  front_html text not null,
  back_html text not null,
  question_id text references public.questions(id) on delete set null,
  section_id text references public.chapter_sections(id) on delete set null,
  taxonomy_ids text[] not null default '{{}}',
  origin text not null default 'user' check (origin in ('seed','user','assistant','anki_import')),
  created_at timestamptz not null default now()
);
create index flashcards_owner_idx on public.flashcards (owner_user_id);

-- Retrieval index (RAG). Only rows whose source allows AI ingest; vault content never enters it.
create table public.embeddings (
  id bigint generated by default as identity primary key,
  owner_user_id uuid references auth.users(id) on delete cascade,
  ref_type text not null
    check (ref_type in ('section','question','note','upload','pathway','url','ink','kb_doc')),
  ref_id text not null,
  chunk_index int not null default 0 check (chunk_index >= 0),
  content text not null,
  embedding {v}.halfvec(384),
  tsv tsvector generated always as (to_tsvector('english'::regconfig, content)) stored,
  created_at timestamptz not null default now(),
  unique nulls not distinct (owner_user_id, ref_type, ref_id, chunk_index)
);
create index embeddings_hnsw_idx on public.embeddings using hnsw (embedding {v}.halfvec_cosine_ops);
create index embeddings_tsv_idx on public.embeddings using gin (tsv);
create index embeddings_ref_idx on public.embeddings (ref_type, ref_id);

-- ───────────── Student activity ─────────────
create table public.blocks (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  config jsonb not null check (jsonb_typeof(config) = 'object'),
  question_ids text[] not null,
  started_at timestamptz,
  finished_at timestamptz,
  score numeric,
  created_at timestamptz not null default now(),
  unique (id, user_id)
);
create index blocks_user_idx on public.blocks (user_id, created_at desc);

create table public.attempts (
  id bigint generated by default as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  question_id text not null references public.questions(id),
  block_id uuid,
  chosen char(1) check (chosen between 'A' and 'J'),
  correct boolean,
  seconds int check (seconds is null or seconds >= 0),
  flagged boolean not null default false,
  mode text check (mode in ('tutor','timed','exam')),
  created_at timestamptz not null default now(),
  -- a block can only be referenced by its own user's attempts
  foreign key (block_id, user_id) references public.blocks (id, user_id) on delete set null (block_id)
);
create index attempts_user_question_idx on public.attempts (user_id, question_id, created_at desc);
create index attempts_user_created_idx on public.attempts (user_id, created_at desc);
create index attempts_block_idx on public.attempts (block_id) where block_id is not null;

create table public.card_reviews (
  user_id uuid not null references auth.users(id) on delete cascade,
  card_id uuid not null references public.flashcards(id) on delete cascade,
  due timestamptz not null,
  stability double precision,
  difficulty double precision,
  elapsed_days int,
  scheduled_days int,
  reps int,
  lapses int,
  state smallint,
  last_review timestamptz,
  primary key (user_id, card_id)
);
create index card_reviews_user_due_idx on public.card_reviews (user_id, due);
create index card_reviews_card_idx on public.card_reviews (card_id);

create table public.notebooks (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  title text not null,
  cover text,
  paper text not null default 'ruled' check (paper in ('ruled','dotted','grid','cornell','blank')),
  created_at timestamptz not null default now(),
  unique (id, user_id)
);
create index notebooks_user_idx on public.notebooks (user_id);

create table public.notes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  notebook_id uuid,
  anchor_type text check (anchor_type in ('section','question','video','pdf','free')),
  anchor_id text,
  anchor_pos jsonb,
  highlight_color text check (highlight_color in ('lime','violet','amber')),
  body jsonb,
  ocr_text text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (notebook_id, user_id) references public.notebooks (id, user_id) on delete set null (notebook_id)
);
create index notes_user_anchor_idx on public.notes (user_id, anchor_type, anchor_id);
create index notes_notebook_idx on public.notes (notebook_id) where notebook_id is not null;

create table public.ink_layers (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  surface_type text not null
    check (surface_type in ('section','question','flashcard','pdf_page','notebook_page','exam_scratch')),
  surface_id text not null,
  strokes jsonb not null default '[]',
  recognized_text text,
  updated_at timestamptz not null default now(),
  unique (user_id, surface_type, surface_id)
);

create table public.uploads (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  kind text not null check (kind in ('pdf','docx','pptx','xlsx','csv','txt','md','html','epub','rtf',
                                     'image','audio','video','link','anki','zip','paste')),
  storage_path text,
  original_name text,
  bytes bigint check (bytes is null or bytes >= 0),
  processed_chapter_id text references public.chapters(id) on delete set null,
  status text not null default 'uploaded' check (status in ('uploaded','processing','ready','failed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, user_id)
);
create index uploads_user_idx on public.uploads (user_id, created_at desc);

create table public.kb_sources (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  kind text not null check (kind in ('url','file','paste')),
  url text,
  title text,
  upload_id uuid,
  status text not null default 'processing' check (status in ('processing','ready','failed','needs_confirmation')),
  error text,
  content_hash text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (id, user_id),
  foreign key (upload_id, user_id) references public.uploads (id, user_id) on delete set null (upload_id),
  check (kind <> 'url' or url is not null)
);
create index kb_sources_user_idx on public.kb_sources (user_id, created_at desc);
create index kb_sources_hash_idx on public.kb_sources (user_id, content_hash) where content_hash is not null;

create table public.ingest_items (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  upload_id uuid,
  kb_source_id uuid,
  detected_kind text,
  sensitivity text not null default 'normal' check (sensitivity in ('normal','personal')),
  proposed_placement jsonb,
  confirmed_placement jsonb,
  status text not null default 'received'
    check (status in ('received','extracting','needs_confirmation','placing','ready','failed','vaulted')),
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (upload_id, user_id) references public.uploads (id, user_id) on delete cascade,
  foreign key (kb_source_id, user_id) references public.kb_sources (id, user_id) on delete cascade
);
create index ingest_items_user_status_idx on public.ingest_items (user_id, status);
create index ingest_items_upload_idx on public.ingest_items (upload_id) where upload_id is not null;
create index ingest_items_kb_source_idx on public.ingest_items (kb_source_id) where kb_source_id is not null;

create table public.plan_items (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  day date not null,
  kind text check (kind in ('read','questions','assessment','cards','video','admin','rest')),
  title text not null,
  ref_type text,
  ref_id text,
  target_minutes int check (target_minutes is null or target_minutes >= 0),
  target_count int check (target_count is null or target_count >= 0),
  status text not null default 'planned' check (status in ('planned','now','done','skipped','behind')),
  origin text not null default 'generator' check (origin in ('generator','user','assistant','seed')),
  sort smallint
);
create index plan_items_user_day_idx on public.plan_items (user_id, day, sort);

create table public.user_pathway_tasks (
  user_id uuid not null references auth.users(id) on delete cascade,
  task_id text not null references public.pathway_tasks(id) on delete cascade,
  status text not null default 'todo' check (status in ('todo','doing','done','na')),
  due date,
  notes text,
  primary key (user_id, task_id)
);
create index user_pathway_tasks_task_idx on public.user_pathway_tasks (task_id);

create table public.vault_documents (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  doc_type text,
  title text,
  storage_path text,
  expires_on date,
  created_at timestamptz not null default now()
);
create index vault_documents_user_idx on public.vault_documents (user_id);

create table public.costs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  task_id text references public.pathway_tasks(id) on delete set null,
  label text,
  amount numeric(12,2) check (amount is null or amount >= 0),
  currency text not null default 'USD' check (currency ~ '^[A-Z]{{3}}$'),
  paid_on date,
  created_at timestamptz not null default now()
);
create index costs_user_idx on public.costs (user_id);
create index costs_task_idx on public.costs (task_id) where task_id is not null;

-- ───────────── Assistant ─────────────
-- LangGraph / Aegra checkpoint tables are created by Aegra's own migrations (segment 04).
create table public.assistant_threads (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  title text,
  created_at timestamptz not null default now()
);
create index assistant_threads_user_idx on public.assistant_threads (user_id, created_at desc);

create table public.generated_media (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  kind text not null check (kind in ('image','video')),
  title text,
  prompt text not null,
  model text not null,
  storage_path text,
  width int check (width is null or width > 0),
  height int check (height is null or height > 0),
  duration_s numeric(5,2) check (duration_s is null or duration_s > 0),
  ai_generated boolean not null default true check (ai_generated),
  approved_for_publish boolean not null default false,
  created_at timestamptz not null default now()
);
create index generated_media_user_idx on public.generated_media (user_id, created_at desc);

create table public.report_issues (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  ref_type text not null
    check (ref_type in ('section','question','flashcard','media','chapter','pathway_task','assistant_message','other')),
  ref_id text,
  body text not null check (char_length(body) between 1 and 4000),
  status text not null default 'open' check (status in ('open','triaged','resolved','dismissed')),
  created_at timestamptz not null default now()
);
create index report_issues_user_idx on public.report_issues (user_id, created_at desc);
create index report_issues_open_idx on public.report_issues (status, created_at) where status in ('open','triaged');

-- ───────────── Runtime configuration (admin only) ─────────────
create table public.app_config (
  key text primary key,
  value jsonb not null,
  updated_by uuid references auth.users(id) on delete set null,
  updated_at timestamptz not null default now()
);

create table public.app_config_history (
  id bigint generated by default as identity primary key,
  key text not null,
  value jsonb not null,
  updated_by uuid,
  updated_at timestamptz,
  op text not null check (op in ('update','delete')),
  replaced_at timestamptz not null default now()
);
create index app_config_history_key_idx on public.app_config_history (key, replaced_at desc);

create function public.naadi_app_config_history() returns trigger
language plpgsql set search_path = '' as $$
begin
  insert into public.app_config_history (key, value, updated_by, updated_at, op)
  values (old.key, old.value, old.updated_by, old.updated_at, lower(tg_op));
  if tg_op = 'UPDATE' then
    new.updated_at := now();
    return new;
  end if;
  return old;
end
$$;
create trigger app_config_history_update before update on public.app_config
  for each row when (old.* is distinct from new.*) execute function public.naadi_app_config_history();
create trigger app_config_history_delete before delete on public.app_config
  for each row execute function public.naadi_app_config_history();

-- ───────────── Free-tier budget guards (admin only) ─────────────
create table public.search_cache (
  query_norm text primary key,
  results jsonb not null,
  credits smallint not null default 1,
  created_at timestamptz not null default now()
);
create index search_cache_created_idx on public.search_cache (created_at);

create table public.llm_usage (
  id bigint generated by default as identity primary key,
  user_id uuid references auth.users(id) on delete set null,
  provider text not null check (provider in ('nvidia','openrouter')),
  role text,
  model text,
  run_id text,
  ok boolean,
  status int,
  prompt_tokens int,
  completion_tokens int,
  latency_ms int check (latency_ms is null or latency_ms >= 0),
  created_at timestamptz not null default now()
);
create index llm_usage_created_idx on public.llm_usage (created_at);
create index llm_usage_provider_created_idx on public.llm_usage (provider, created_at);

-- ───────────── Function privileges ─────────────
revoke all on function public.naadi_touch_updated_at() from public, anon, authenticated;
revoke all on function public.naadi_app_config_history() from public, anon, authenticated;
"""  # noqa: S608 - identifiers are module constants and a quoted schema name from pg_extension


def _updated_at_triggers() -> str:
    return "\n".join(
        f"create trigger {t}_touch_updated_at before update on public.{t}"
        " for each row execute function public.naadi_touch_updated_at();"
        for t in UPDATED_AT_TABLES
    )


def _cols(cols: tuple[str, ...] | None) -> str:
    return "" if cols is None else " (" + ", ".join(cols) + ")"


def _user_table_rls(table: str, spec: UserTable) -> str:
    t = f"public.{table}"
    own = f"{UID} = user_id"
    check = own if spec.refs is None else f"{own} and ({spec.refs})"
    insert_cols, update_cols = spec.insert, spec.update
    return f"""
alter table {t} enable row level security;
alter table {t} force row level security;
revoke all on table {t} from anon, authenticated;
grant select, delete on table {t} to authenticated;
grant insert{_cols(insert_cols)} on table {t} to authenticated;
grant update{_cols(update_cols)} on table {t} to authenticated;
grant all on table {t} to service_role;
create policy {table}_select_own on {t} for select to authenticated using ({own});
create policy {table}_insert_own on {t} for insert to authenticated with check ({check});
create policy {table}_update_own on {t} for update to authenticated using ({own}) with check ({check});
create policy {table}_delete_own on {t} for delete to authenticated using ({own});
"""


def _shared_table_rls(table: str, using: str | dict[str, str]) -> str:
    t = f"public.{table}"
    if table == "questions":
        select_grant = f"grant select ({', '.join(QUESTION_CLIENT_COLUMNS)}) on table {t} to anon, authenticated;"
    else:
        select_grant = f"grant select on table {t} to anon, authenticated;"
    if isinstance(using, str):
        policies = f"create policy {table}_read on {t} for select to anon, authenticated using ({using});"
    else:
        policies = "\n".join(
            f"create policy {table}_read_{role} on {t} for select to {role} using ({expr});"
            for role, expr in using.items()
        )
    return f"""
alter table {t} enable row level security;
revoke all on table {t} from anon, authenticated;
{select_grant}
grant all on table {t} to service_role;
{policies}
"""


def _flashcard_owner_writes() -> str:
    own = f"owner_user_id = {UID}"
    # Students write only their own `origin = 'user'` cards; seed, assistant and Anki-import rows are backend
    # writes. A linked question / section must be visible to the caller.
    check = (
        f"{own} and origin = 'user'"  # noqa: S608 - module constants only
        " and (question_id is null or exists (select 1 from public.questions p where p.id = flashcards.question_id))"
        " and (section_id is null"
        " or exists (select 1 from public.chapter_sections p where p.id = flashcards.section_id))"
    )
    return f"""
grant insert, update, delete on table public.flashcards to authenticated;
create policy flashcards_insert_own on public.flashcards for insert to authenticated
  with check ({check});
create policy flashcards_update_own on public.flashcards for update to authenticated
  using ({own} and origin = 'user') with check ({check});
create policy flashcards_delete_own on public.flashcards for delete to authenticated
  using ({own} and origin = 'user');
"""


def _admin_table_rls(table: str) -> str:
    t = f"public.{table}"
    return f"""
alter table {t} enable row level security;
alter table {t} force row level security;
revoke all on table {t} from anon, authenticated;
grant all on table {t} to service_role;
"""


def _sequence_privileges() -> str:
    # Identity columns need no sequence privileges to insert; strip the Supabase default grants anyway.
    return """
do $$
declare s text;
begin
  for s in
    select format('%I.%I', sn.nspname, sc.relname)
    from pg_depend d
    join pg_class sc on sc.oid = d.objid and sc.relkind = 'S'
    join pg_namespace sn on sn.oid = sc.relnamespace
    join pg_class tc on tc.oid = d.refobjid
    join pg_namespace tn on tn.oid = tc.relnamespace
    where d.deptype in ('a','i') and tn.nspname = 'public' and tc.relname = any(%(tables)s)
  loop
    execute format('revoke all on sequence %s from anon, authenticated', s);
    execute format('grant all on sequence %s to service_role', s);
  end loop;
end
$$;
""".replace("%(tables)s", "array[" + ",".join(f"'{t}'" for t in ALL_TABLES) + "]")


def _extensions() -> str:
    bind = op.get_bind()
    op.execute("create schema if not exists extensions")
    # Supabase installs extensions into `extensions`; an extension that already exists elsewhere is reused.
    op.execute("create extension if not exists vector with schema extensions")
    op.execute("create extension if not exists pg_trgm with schema extensions")
    schema = bind.execute(
        text(
            "select n.nspname from pg_extension e join pg_namespace n on n.oid = e.extnamespace"
            " where e.extname = 'vector'"
        )
    ).scalar_one()
    return bind.dialect.identifier_preparer.quote_identifier(schema)


def _default_privileges(action: str) -> str:
    # `for role postgres`: the role that runs migrations (Naadi's and Aegra's). Supabase's schema-level
    # defaults grant everything to anon/authenticated; Naadi closes them and grants explicitly instead.
    verb = "revoke all on {obj} from" if action == "revoke" else "grant all on {obj} to"
    return "\n".join(
        f"alter default privileges for role postgres in schema public {verb.format(obj=o)} {CLIENT_ROLES};"
        for o in DEFAULT_PRIVILEGE_OBJECTS
    )


def upgrade() -> None:
    op.execute(_default_privileges("revoke"))
    vector_schema = _extensions()
    op.execute(_schema_sql(vector_schema))
    op.execute(_updated_at_triggers())
    for table, spec in USER_TABLES.items():
        op.execute(_user_table_rls(table, spec))
    for table, using in SHARED_TABLES.items():
        op.execute(_shared_table_rls(table, using))
    op.execute(_flashcard_owner_writes())
    for table in ADMIN_TABLES:
        op.execute(_admin_table_rls(table))
    op.execute(_sequence_privileges())
    # Alembic created its version table before running this migration; keep it out of the Data API too.
    op.execute(_admin_table_rls("naadi_alembic_version"))


def downgrade() -> None:
    # Extensions stay: other schemas (Aegra, Supabase) may use them.
    for table in reversed(ALL_TABLES):
        op.execute(f"drop table if exists public.{table} cascade")
    op.execute("drop function if exists public.naadi_app_config_history()")
    op.execute("drop function if exists public.naadi_touch_updated_at()")
    op.execute(_default_privileges("grant"))  # back to the Supabase defaults
