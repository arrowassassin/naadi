-- Naadi — Postgres 17 schema (Supabase). DOCUMENTATION of the data contract.
-- The database is created ONLY by the Alembic migrations in apps/api/migrations/versions/
-- (0001_core_schema.py, 0002_storage_buckets.py, 0003_reading_positions.py, 0004_seed_content.py, 0005_agent_platform.py, 0006, 0007, 0008, 0009, 0010, 0011, 0012_card_review_logs.py, 0013, 0014;
-- version table public.naadi_alembic_version).
-- tests/db/test_schema_doc.py fails if a table or column here differs from the migrated database.
-- Extensions (schema `extensions`, as Supabase expects): vector (halfvec), pg_trgm.
--
-- Default privileges (first statement of 0001): for role postgres in schema public, tables, sequences and
-- functions created from now on grant nothing to anon/authenticated (Supabase's defaults grant all). Every
-- client grant below is explicit. Functions keep PostgreSQL's global PUBLIC execute; Naadi's revoke it.

-- ───────────── Functions ─────────────
create function public.naadi_touch_updated_at() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.updated_at := now();
  return new;
end
$$;

-- ───────────── Content sources, taxonomy, pathway ─────────────
-- pathway_stages.key: the 16 keys of data/pathway/pathway_stages.json (eligibility, step1, oet_pathway, step2ck,
-- usce, specialty_choice, eras_application, interviews_ranking, match_day, visa_j1, pre_departure,
-- arrival_first_30_days, residency_life, step3_and_licensure, post_residency, california_bay_area).
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
  notes text,
  -- 0004: license terms checked at a cited URL (config/sources.yaml `verified` / `verified_url`)
  verified boolean not null default false,
  verified_url text check (verified_url is null or verified_url ~ '^https://')
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

-- 0004: long-form pathway guides (data/pathway/*.md): key 'overview' (stage_key null) + one per stage.
create table public.pathway_guides (
  key text primary key check (key ~ '^[a-z0-9_]{1,64}$'),
  stage_key text unique references public.pathway_stages(key) on delete cascade,
  sort smallint not null,
  title text not null check (char_length(title) between 1 and 200),
  body_md text not null,
  source_id text not null references public.sources(id) default 'naadi-original-v1',
  updated_at timestamptz not null default now()
);

-- 0004: study-plan templates (data/study_plan_step1_60d.csv = 'step1-60d'), one row per day. Per-student
-- status/score/notes live in plan_items, never here.
create table public.plan_templates (
  id text primary key check (id ~ '^[a-z0-9-]{1,64}$'),
  exam text not null check (exam in ('step1','step2ck','step3')),
  title text not null,
  days smallint not null check (days between 1 and 366),
  source_id text not null references public.sources(id) default 'naadi-original-v1',
  updated_at timestamptz not null default now()
);

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

-- ───────────── Users ─────────────
create table public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  display_name text check (char_length(display_name) between 1 and 120),
  exam_target text not null default 'step1' check (exam_target in ('step1','step2ck','step3')),
  exam_date date,
  daily_hours numeric(4,1) not null default 8 check (daily_hours between 0 and 24),
  study_days smallint[] not null default '{1,2,3,4,5,6}' check (study_days <@ '{1,2,3,4,5,6,7}'::smallint[]),
  timezone text not null default 'Asia/Kolkata' check (char_length(timezone) between 1 and 64),
  pathway_stage text references public.pathway_stages(key) on delete set null,
  settings jsonb not null default '{}' check (jsonb_typeof(settings) = 'object'),
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
  taxonomy_ids text[] not null default '{}',
  updated_at timestamptz not null default now(),
  -- 0004: set when an admin or the assistant edits the section (trigger on non-seed revisions); the seed
  -- importer keeps such sections unless forced
  admin_edited_at timestamptz
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
  taxonomy_ids text[] not null default '{}',
  review_status text not null default 'unreviewed' check (review_status in ('unreviewed','approved','rejected')),
  -- 0003: intrinsic size in px (an SVG's viewBox) so the reader reserves the space of a lazy figure
  width int check (width is null or width > 0),
  height int check (height is null or height > 0),
  -- 0004: sha256 of the stored (sanitised) object; the seed importer skips unchanged uploads
  content_sha256 text check (content_sha256 is null or content_sha256 ~ '^[0-9a-f]{64}$')
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
  taxonomy_ids text[] not null default '{}',
  tags text[] not null default '{}',
  source_id text references public.sources(id) default 'naadi-original-v1',
  status text not null default 'published'
    check (status in ('draft','auto_checked','in_review','published','retired')),
  review jsonb not null default '{}',
  stats jsonb not null default '{}',
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
  taxonomy_ids text[] not null default '{}',
  origin text not null default 'user' check (origin in ('seed','user','assistant','anki_import')),
  created_at timestamptz not null default now(),
  -- 0012: the faces as TipTap documents for ContentRenderer (front_html/back_html stay the interchange form)
  front_doc jsonb check (front_doc is null or jsonb_typeof(front_doc) = 'object'),
  back_doc jsonb check (back_doc is null or jsonb_typeof(back_doc) = 'object'),
  updated_at timestamptz not null default now()
);
create index flashcards_owner_idx on public.flashcards (owner_user_id);
create index flashcards_question_idx on public.flashcards (question_id) where question_id is not null;

-- Retrieval index (RAG). Only rows whose source allows AI ingest; vault content never enters it.
create table public.embeddings (
  id bigint generated by default as identity primary key,
  owner_user_id uuid references auth.users(id) on delete cascade,
  ref_type text not null
    check (ref_type in ('section','question','note','upload','pathway','url','ink','kb_doc')),
  ref_id text not null,
  chunk_index int not null default 0 check (chunk_index >= 0),
  content text not null,
  embedding extensions.halfvec(384),
  tsv tsvector generated always as (to_tsvector('english'::regconfig, content)) stored,
  created_at timestamptz not null default now(),
  unique nulls not distinct (owner_user_id, ref_type, ref_id, chunk_index)
);
create index embeddings_hnsw_idx on public.embeddings using hnsw (embedding extensions.halfvec_cosine_ops);
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
  -- 0011 (backend-only): break time already spent from a timed/exam session's shared pool
  break_used_ms bigint not null default 0 check (break_used_ms >= 0),
  unique (id, user_id)
);
create index blocks_user_idx on public.blocks (user_id, created_at desc);
-- 0014: at most one unfinished onboarding diagnostic per student (concurrent POST /api/diagnostic returns it)
create unique index blocks_one_open_diagnostic on public.blocks (user_id)
  where config->>'purpose' = 'diagnostic' and finished_at is null;

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
  -- 0011 (backend-only): timed/exam answer history; `correct` stays null until the whole block is finished
  first_chosen char(1) check (first_chosen between 'A' and 'J'),
  changes smallint not null default 0 check (changes >= 0),
  answered_at timestamptz,
  -- a block can only be referenced by its own user's attempts
  foreign key (block_id, user_id) references public.blocks (id, user_id) on delete set null (block_id)
);
create index attempts_user_question_idx on public.attempts (user_id, question_id, created_at desc);
create index attempts_user_created_idx on public.attempts (user_id, created_at desc);
create index attempts_block_idx on public.attempts (block_id) where block_id is not null;
-- 0006: one row per (block, question), so answering is idempotent and the flag shares the answer's row
create unique index attempts_block_question_key on public.attempts (block_id, question_id) where block_id is not null;

-- 0011: the server-authoritative clock of a timed/exam block, one row per section. Clients: SELECT own rows only
-- (the four _own policies exist; INSERT/UPDATE/DELETE are not granted). A missing row counts as expired.
create table public.block_sections (
  block_id uuid not null,
  user_id uuid not null references auth.users(id) on delete cascade,
  n smallint not null check (n between 1 and 12),
  first_index smallint not null check (first_index >= 0),
  count smallint not null check (count between 1 and 80),
  time_limit_s int not null check (time_limit_s between 1 and 86400),
  started_at timestamptz,
  deadline_at timestamptz,
  paused_at timestamptz,
  paused_total_ms bigint not null default 0 check (paused_total_ms >= 0),
  ended_at timestamptz,
  ended_reason text check (ended_reason in ('submitted','expired')),
  created_at timestamptz not null default now(),
  primary key (block_id, n),
  foreign key (block_id, user_id) references public.blocks (id, user_id) on delete cascade,
  check ((ended_at is null) = (ended_reason is null)),
  check ((started_at is null) = (deadline_at is null)),
  check (paused_at is null or (started_at is not null and ended_at is null))
);
create index block_sections_user_open_idx on public.block_sections (user_id, block_id) where ended_at is null;

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
  -- 0012: learning / relearning step of the Python fsrs scheduler (null in Review)
  step smallint check (step is null or step >= 0),
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
  recognized_text text,
  updated_at timestamptz not null default now(),
  -- 0009: written by the API only; rev = max(ink_items.seq), item_count = live items, content_hash = sha256 over
  -- every item's id:updatedAt:deleted (sorted by id), purged_rev = highest seq of a purged tombstone;
  -- 0016: purged_before_ms = highest updatedAt of a purged tombstone (absent items at or below it are stale)
  rev bigint not null default 0 check (rev >= 0),
  item_count int not null default 0 check (item_count >= 0),
  content_hash text,
  purged_rev bigint not null default 0 check (purged_rev >= 0),
  purged_before_ms bigint not null default 0 check (purged_before_ms >= 0),
  unique (user_id, surface_type, surface_id),
  unique (id, user_id)
);

-- 0009: one row per InkItem (packages/shared/src/ink.ts), merged by the API with mergeInkItems' order
-- (higher updated_at_ms, then a tombstone, then the stored row stays). seq comes from public.ink_items_seq and
-- is bumped on every write (incremental sync cursor). Tombstones older than 90 days are purged daily.
create table public.ink_items (
  user_id uuid not null references auth.users(id) on delete cascade,
  layer_id uuid not null,
  item_id uuid not null,
  updated_at_ms bigint not null check (updated_at_ms >= 0),
  deleted boolean not null default false,
  item jsonb not null,
  content_rev text,
  seq bigint not null default nextval('public.ink_items_seq'),
  written_at timestamptz not null default now(),
  primary key (layer_id, item_id),
  foreign key (layer_id, user_id) references public.ink_layers (id, user_id) on delete cascade,
  check (item ->> 'id' = item_id::text and (item ->> 'updatedAt')::bigint = updated_at_ms
         and coalesce((item ->> 'deleted')::boolean, false) = deleted)
);
create index ink_items_layer_seq_idx on public.ink_items (layer_id, seq);
create index ink_items_user_idx on public.ink_items (user_id);
create index ink_items_tombstones_idx on public.ink_items (written_at) where deleted;

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


-- 0013: generated study plans (one active per student), their dated rows in the template CSV's shape, and the
-- nightly coach's suggestions. Written by the API; students update only status (+ a plan day's score_notes).
create table public.study_plans (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  template_id text not null references public.plan_templates(id),
  inputs jsonb not null check (jsonb_typeof(inputs) = 'object'),
  generator_version smallint not null check (generator_version >= 1),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  last_coached_at timestamptz,  -- 0014: the nightly coach takes the least recently coached first
  unique (id, user_id)
);
create unique index study_plans_one_active_idx on public.study_plans (user_id) where active;
create index study_plans_coach_rotation on public.study_plans (last_coached_at nulls first) where active;  -- 0014
create index study_plans_template_idx on public.study_plans (template_id);

create table public.study_plan_days (
  user_id uuid not null references auth.users(id) on delete cascade,
  plan_id uuid not null,
  day_no smallint not null check (day_no >= 1),
  date date not null,
  weekday text not null check (weekday in ('Mon','Tue','Wed','Thu','Fri','Sat','Sun')),
  week smallint not null check (week >= 1),
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
  template_day smallint not null check (template_day >= 1),
  merged_days smallint[] not null default '{}',
  extra boolean not null default false,
  status text not null default 'Not started' check (status in ('Not started','In progress','Done','Skipped')),
  score_notes text not null default '' check (char_length(score_notes) <= 2000),
  primary key (plan_id, day_no),
  unique (plan_id, date),
  foreign key (plan_id, user_id) references public.study_plans (id, user_id) on delete cascade
);
create index study_plan_days_user_date_idx on public.study_plan_days (user_id, date);

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
  sort smallint,
  -- 0013: generator items point at their plan and day (composite FK: only the student's own plan)
  plan_id uuid,
  plan_day smallint check (plan_day is null or plan_day >= 1),
  constraint plan_items_plan_fkey foreign key (plan_id, user_id)
    references public.study_plans (id, user_id) on delete cascade,
  constraint plan_items_plan_link_check check ((plan_id is null) = (plan_day is null))
);
create index plan_items_user_day_idx on public.plan_items (user_id, day, sort);
create index plan_items_plan_idx on public.plan_items (plan_id, plan_day) where plan_id is not null;

create table public.coach_suggestions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  for_date date not null,
  kind text not null check (kind in ('catch_up_block','review_misses','cards_catchup','reread_section')),
  title text check (title is null or char_length(title) between 1 and 120),
  body text check (body is null or char_length(body) between 1 and 600),
  facts jsonb not null default '{}' check (jsonb_typeof(facts) = 'object'),
  action jsonb not null default '{}' check (jsonb_typeof(action) = 'object'),
  source text not null check (source in ('llm','rules')),
  status text not null default 'new' check (status in ('new','accepted','dismissed')),
  created_at timestamptz not null default now(),
  check ((source = 'llm') = (title is not null and body is not null))
);
create index coach_suggestions_user_date_idx on public.coach_suggestions (user_id, for_date desc, created_at);
-- 0014: trigger coach_suggestions_status_guard (naadi_coach_status_guard()): status leaves 'new' once and never
-- changes again; a client may only dismiss (accepting goes through the API, which adds the plan items).

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
  currency text not null default 'USD' check (currency ~ '^[A-Z]{3}$'),
  paid_on date,
  created_at timestamptz not null default now()
);
create index costs_user_idx on public.costs (user_id);
create index costs_task_idx on public.costs (task_id) where task_id is not null;

-- ───────────── Assistant ─────────────
-- Aegra / LangGraph tables (assistant, assistant_versions, thread, runs, crons, thread_ttl, checkpoints,
-- checkpoint_blobs, checkpoint_writes, checkpoint_migrations, store, store_migrations, alembic_version) are
-- created by Aegra's own migrations at container start (naadi_api/agent/migrate_all.py) and are NOT documented
-- here; 0005's public.naadi_secure_aegra_tables() enables RLS on them and revokes every client privilege.
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

-- 0003: one row per (user, section) opened in the reader; synced across devices. scroll_position = index of
-- the top-most visible top-level block + the fraction of it scrolled past (layout independent).
-- client_updated_at (clamped by the API to the server clock) orders writes: an older write never wins.
create table public.reading_positions (
  user_id uuid not null references auth.users(id) on delete cascade,
  chapter_id text not null references public.chapters(id) on delete cascade,
  section_id text not null references public.chapter_sections(id) on delete cascade,
  scroll_position double precision not null default 0 check (scroll_position >= 0 and scroll_position <= 100000),
  completed_at timestamptz,
  client_updated_at timestamptz not null,
  updated_at timestamptz not null default now(),
  primary key (user_id, section_id)
);
create index reading_positions_chapter_idx on public.reading_positions (user_id, chapter_id, client_updated_at desc);
create index reading_positions_section_idx on public.reading_positions (section_id);

-- 0012: one row per flashcard rating, written by the API's FSRS scheduler. The ts-fsrs ReviewLog fields hold
-- the memory state before the review (optimizer-ready); next_* / reps / lapses hold the outcome, returned again
-- for a repeated client_review_id (offline outbox) instead of scheduling twice.
create table public.card_review_logs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  card_id uuid not null references public.flashcards(id) on delete cascade,
  client_review_id uuid not null,
  rating smallint not null check (rating between 1 and 4),
  state smallint not null check (state between 0 and 3),
  due timestamptz not null,
  stability double precision,
  difficulty double precision,
  elapsed_days int not null check (elapsed_days >= 0),
  last_elapsed_days int not null check (last_elapsed_days >= 0),
  scheduled_days int not null check (scheduled_days >= 0),
  learning_step smallint check (learning_step is null or learning_step >= 0),
  review timestamptz not null,
  duration_ms int check (duration_ms is null or (duration_ms >= 0 and duration_ms <= 3600000)),
  next_state smallint not null check (next_state between 1 and 3),
  next_step smallint check (next_step is null or next_step >= 0),
  next_due timestamptz not null,
  next_stability double precision not null,
  next_difficulty double precision not null,
  reps int not null check (reps >= 1),
  lapses int not null check (lapses >= 0),
  created_at timestamptz not null default now(),
  unique (user_id, client_review_id)
);
create index card_review_logs_user_review_idx on public.card_review_logs (user_id, review desc);
create index card_review_logs_card_idx on public.card_review_logs (card_id, review);

-- ───────────── Runtime configuration (admin only) ─────────────
create table public.app_config (
  key text primary key,
  value jsonb not null,
  updated_by uuid references auth.users(id) on delete set null,
  updated_at timestamptz not null default now(),
  -- 0004: set by the admin PUT; the seed importer keeps such keys unless forced
  admin_edited_at timestamptz
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

-- 0004: a non-seed revision (admin editor, assistant) marks its section as edited.
create function public.naadi_mark_section_edited() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.origin <> 'seed' then
    update public.chapter_sections set admin_edited_at = coalesce(new.created_at, now()) where id = new.section_id;
  end if;
  return null;
end
$$;
create trigger chapter_section_revisions_mark_edited after insert on public.chapter_section_revisions
  for each row execute function public.naadi_mark_section_edited();

-- 0004: every app_config write notifies channel 'naadi_app_config' (payload = key) so each API process drops
-- its 60 s cache entry at once.
create function public.naadi_app_config_notify() returns trigger
language plpgsql set search_path = '' as $$
begin
  perform pg_notify('naadi_app_config', coalesce(new.key, old.key));
  return null;
end
$$;
create trigger app_config_notify after insert or update or delete on public.app_config
  for each row execute function public.naadi_app_config_notify();

-- 0004: admin seed jobs (POST /api/admin/seed). At most one row is 'running'.
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
create unique index seed_runs_one_running_idx on public.seed_runs ((true)) where status = 'running';
create index seed_runs_started_idx on public.seed_runs (started_at desc);

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
  created_at timestamptz not null default now(),
  purpose text not null default 'chat' check (purpose in ('chat','job'))  -- 0005: daily cap split chat / jobs
);
create index llm_usage_created_idx on public.llm_usage (created_at);
create index llm_usage_provider_created_idx on public.llm_usage (provider, created_at);
create index llm_usage_provider_purpose_created_idx on public.llm_usage (provider, purpose, created_at);

-- 0005: our own Tavily ledger (per-user daily cap, monthly credit stop; Tavily's usage API is not polled).
create table public.search_usage (
  id bigint generated by default as identity primary key,
  user_id uuid references auth.users(id) on delete set null,
  source text not null check (source in ('tavily')),
  credits smallint not null check (credits > 0),
  created_at timestamptz not null default now()
);
create index search_usage_source_created_idx on public.search_usage (source, created_at);
create index search_usage_user_created_idx on public.search_usage (user_id, created_at) where user_id is not null;

-- 0005: agent runtime self-test results (`ping` graph: interactive, background, hourly system cron).
create table public.agent_health (
  id bigint generated by default as identity primary key,
  kind text not null check (kind in ('ping','tavily_usage','coach')),  -- 0007: + daily Tavily usage reading; 0013: + nightly coach pass
  trigger text not null check (trigger in ('interactive','background','cron')),
  ok boolean not null,
  run_id text,
  provider text check (provider is null or provider in ('nvidia','openrouter')),
  model text,
  latency_ms int check (latency_ms is null or latency_ms >= 0),
  detail jsonb not null default '{}' check (jsonb_typeof(detail) = 'object'),
  created_at timestamptz not null default now()
);
create index agent_health_kind_trigger_created_idx on public.agent_health (kind, trigger, created_at desc);

-- 0005: RLS on + client privileges revoked for each Aegra / LangGraph table that exists (called by the
-- migration and again by migrate_all after every startup).
create function public.naadi_secure_aegra_tables() returns int
language plpgsql set search_path = '' as $$ ... $$;

-- ───────────── Function privileges ─────────────
revoke all on function public.naadi_touch_updated_at() from public, anon, authenticated;
revoke all on function public.naadi_app_config_history() from public, anon, authenticated;
revoke all on function public.naadi_app_config_notify() from public, anon, authenticated;
revoke all on function public.naadi_mark_section_edited() from public, anon, authenticated;
revoke all on function public.naadi_secure_aegra_tables() from public, anon, authenticated;

-- ───────────── updated_at triggers ─────────────
create trigger profiles_touch_updated_at before update on public.profiles for each row execute function public.naadi_touch_updated_at();
create trigger chapters_touch_updated_at before update on public.chapters for each row execute function public.naadi_touch_updated_at();
create trigger chapter_sections_touch_updated_at before update on public.chapter_sections for each row execute function public.naadi_touch_updated_at();
create trigger notes_touch_updated_at before update on public.notes for each row execute function public.naadi_touch_updated_at();
create trigger ink_layers_touch_updated_at before update on public.ink_layers for each row execute function public.naadi_touch_updated_at();
create trigger uploads_touch_updated_at before update on public.uploads for each row execute function public.naadi_touch_updated_at();
create trigger kb_sources_touch_updated_at before update on public.kb_sources for each row execute function public.naadi_touch_updated_at();
create trigger ingest_items_touch_updated_at before update on public.ingest_items for each row execute function public.naadi_touch_updated_at();
create trigger reading_positions_touch_updated_at before update on public.reading_positions for each row execute function public.naadi_touch_updated_at();
create trigger flashcards_touch_updated_at before update on public.flashcards for each row execute function public.naadi_touch_updated_at();
create trigger pathway_guides_touch_updated_at before update on public.pathway_guides for each row execute function public.naadi_touch_updated_at();
create trigger plan_templates_touch_updated_at before update on public.plan_templates for each row execute function public.naadi_touch_updated_at();

-- ───────────── Grants and row-level security (0001) ─────────────
-- Every table in public has RLS enabled (tests/db/test_rls_coverage.py enforces it).
--
-- User-owned tables: RLS enabled AND forced; `anon` has no privileges; `authenticated` has
-- select/insert/update/delete (column-restricted where noted: grading, moderation and pipeline columns are
-- backend-only); four policies `to authenticated`, each `(select auth.uid()) = user_id`:
-- <t>_select_own, <t>_insert_own, <t>_update_own, <t>_delete_own. Where a row references shared content, the
-- insert/update check also requires that row to be visible to the caller (no foreign-key oracle).
--   profiles
--     + insert/update check: pathway_stage is null or exists (select 1 from public.pathway_stages p where p.key = profiles.pathway_stage)
--   attempts  (client insert: user_id, question_id, block_id, chosen, seconds, flagged, mode; client update: flagged)
--   block_sections (0011): select only; every timing write is the API's (0008 also revoked client writes on blocks)
--     + insert/update check: exists (select 1 from public.questions p where p.id = attempts.question_id)
--   blocks  (client insert: id, user_id, config, question_ids, started_at; client update: started_at)
--   card_reviews  (0012: API-written; client select only)
--     + insert/update check: exists (select 1 from public.flashcards p where p.id = card_reviews.card_id)
--   notes
--   notebooks
--   ink_layers  (0009: select only for clients; the ink API writes layers and items)
--   ink_items (0009)  (select only for clients)
--   uploads  (client insert: id, user_id, kind, storage_path, original_name, bytes; client update: original_name)
--   plan_items
--   user_pathway_tasks
--     + insert/update check: exists (select 1 from public.pathway_tasks p where p.id = user_pathway_tasks.task_id)
--   vault_documents
--   costs
--     + insert/update check: task_id is null or exists (select 1 from public.pathway_tasks p where p.id = costs.task_id)
--   assistant_threads
--   kb_sources
--   ingest_items
--   generated_media  (client insert: id, user_id, kind, title, prompt, model, storage_path, width, height, duration_s; client update: title)
--   report_issues  (client insert: id, user_id, ref_type, ref_id, body; client update: body)
--   reading_positions (0003)  (client insert: user_id, chapter_id, section_id, scroll_position, completed_at, client_updated_at; client update: scroll_position, completed_at, client_updated_at)
--     + insert/update check: exists (select 1 from public.chapter_sections s where s.id = reading_positions.section_id and s.chapter_id = reading_positions.chapter_id)
--   card_review_logs (0012)  (API-written; client select only)
--     + insert/update check: exists (select 1 from public.flashcards p where p.id = card_review_logs.card_id)
--
-- Shared content: RLS enabled; select to anon, authenticated with the row filter below; writes by the
-- service role (backend) only, except that a student may insert/update/delete their own private
-- flashcards with origin = 'user' (assistant / anki_import / seed cards are backend writes) whose linked
-- question / section is visible to them. A shared flashcard linked to a question restates its
-- answer: it is never visible to anon, and to a student only after they have an attempt on that question.
--   sources: true
--   taxonomy_nodes: true
--   pathway_stages: true
--   pathway_tasks: true
--   pathway_guides, plan_templates, plan_template_days (0004): true (select only, to anon and authenticated)
--   chapters: (owner_user_id is null and status = 'published') or owner_user_id = (select auth.uid())
--   chapter_sections: exists (select 1 from public.chapters c where c.id = chapter_sections.chapter_id)
--   media_assets: review_status = 'approved'
--   section_media: exists (select 1 from public.chapter_sections s where s.id = section_media.section_id) and exists (select 1 from public.media_assets m where m.id = section_media.media_id)
--   questions: status = 'published' and (chapter_id is null or exists (select 1 from public.chapters c where c.id = questions.chapter_id))
--   flashcards (anon): owner_user_id is null and question_id is null
--   flashcards (authenticated): owner_user_id = (select auth.uid()) or (owner_user_id is null and (question_id is null or exists (select 1 from public.attempts a where a.user_id = (select auth.uid()) and a.question_id = flashcards.question_id)))
--
-- questions: clients get column-level select only on
--   (id, chapter_id, exam, block, stem_html, options, difficulty, taxonomy_ids, tags, source_id, status, created_at);
--   answer, answer_html, explanation_html, why_not_html, educational_objective, review and stats are
--   not readable by anon/authenticated at all (the API serves them after an attempt).
--
-- Server-side only (RLS enabled and forced, no client privileges, service role only):
--   app_config, app_config_history, chapter_section_revisions, search_cache, llm_usage, embeddings, seed_runs (0004),
--   search_usage, agent_health (0005), naadi_alembic_version
--   (embeddings: retrieval runs in the API; question and draft chunks would leak answers)
--
-- 0013: study_plans: select only. study_plan_days: select + update (status, score_notes). coach_suggestions:
--   select + update (status). RLS forced with the four _own policies; inserts and deletes come from the API.
--
-- Functions naadi_touch_updated_at(), naadi_app_config_history(), naadi_app_config_notify(),
-- naadi_mark_section_edited(), naadi_secure_aegra_tables() and naadi_coach_status_guard() (0014) pin search_path = '' and are not executable
-- by clients.

-- ───────────── Storage (0002) ─────────────
-- Buckets (storage.buckets; schema owned by the Supabase Storage service). No catch-all MIME type
-- (no application/octet-stream); the upload client sends an explicit contentType (application/vnd.anki for
-- .apkg/.colpkg after a ZIP magic check) and the server re-sniffs.
--   content-public  : public read; 10 MB; image/svg+xml, image/png, image/jpeg, image/webp, image/gif, image/avif
--   content-private : private; 50 MB; application/pdf, application/epub+zip, image/png, image/jpeg, image/webp, image/gif, image/avif, image/heic, image/heif, audio/mpeg, audio/mp4, audio/aac, audio/wav, audio/x-wav, audio/webm, audio/ogg, video/mp4, video/webm, video/quicktime
--   user-uploads    : private; 50 MB; application/pdf, application/vnd.openxmlformats-officedocument.wordprocessingml.document, application/vnd.openxmlformats-officedocument.presentationml.presentation, application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, text/csv, text/plain, text/markdown, text/html, application/epub+zip, application/rtf, text/rtf, image/png, image/jpeg, image/webp, image/gif, image/avif, image/heic, image/heif, audio/mpeg, audio/mp4, audio/aac, audio/wav, audio/x-wav, audio/webm, audio/ogg, video/mp4, video/webm, video/quicktime, application/zip, application/x-zip-compressed, application/vnd.anki
--   vault           : private; 20 MB; application/pdf, image/png, image/jpeg, image/webp, image/heic, image/heif
--   generated-media : private; 50 MB; image/png, image/jpeg, image/webp, video/mp4, video/webm
-- content-* writes: service role only. vault objects are NEVER sent to any AI / ingest path.
-- storage.objects policies: naadi_user_folders_select/insert/update/delete (authenticated, buckets
-- user-uploads|vault|generated-media, (storage.foldername(name))[1] = auth.uid()::text) and
-- naadi_content_public_read (anon, authenticated, bucket content-public).
-- Own large videos live in Cloudflare R2 with URLs in media_assets.url; YouTube videos are media_assets
-- rows (kind = 'video_youtube') rendered with the IFrame player.
