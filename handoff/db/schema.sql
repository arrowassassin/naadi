-- Naadi — Postgres 17 schema (Supabase or Neon). Requires: pgvector, pg_trgm.
-- All user-owned tables carry user_id and are protected by row-level security (RLS) policies at the bottom.
create extension if not exists vector;
create extension if not exists pg_trgm;

-- ───────────── Users & settings ─────────────
create table profiles (
  user_id uuid primary key,                       -- = auth.users.id (Supabase) or your auth provider id
  display_name text,
  exam_target text check (exam_target in ('step1','step2ck','step3')) default 'step1',
  exam_date date,
  daily_hours numeric(4,1) default 8,
  study_days smallint[] default '{1,2,3,4,5,6}',  -- ISO weekday numbers
  timezone text default 'Asia/Kolkata',
  pathway_stage text,                              -- see pathway_stages.key
  settings jsonb not null default '{}',            -- ink, appearance, assistant prefs
  created_at timestamptz default now()
);

-- ───────────── Taxonomy (official USMLE outline + internal topics) ─────────────
create table taxonomy_nodes (
  id text primary key,                 -- e.g. 'sys.cardio', 'disc.pathology', 'comp.diagnosis', 'topic.cardio.murmurs'
  kind text not null check (kind in ('exam','system','discipline','competency','clinical_science','topic')),
  parent_id text references taxonomy_nodes(id),
  label text not null,
  exam text[] not null,                 -- which Steps it applies to
  weight_min numeric, weight_max numeric,   -- official % range where published
  sort smallint default 0
);

-- ───────────── Content library ─────────────
create table sources (                 -- every external or internal origin of content, with its license
  id text primary key,                 -- 'naadi-original-v1', 'youtube:UCxxxx', 'statpearls', 'cdc-phil' …
  name text not null,
  url text,
  license text not null,               -- SPDX-ish: 'proprietary-naadi', 'CC-BY-4.0', 'CC-BY-NC-ND-4.0', 'public-domain-us-gov', 'youtube-embed-only', 'link-only'
  allow_copy boolean not null default false,
  allow_modify boolean not null default false,
  allow_commercial boolean not null default false,
  allow_ai_ingest boolean not null default false,  -- may it enter RAG / LLM prompts?
  attribution_template text,
  notes text
);

create table chapters (
  id text primary key,                 -- 'ch05'
  exam text not null,
  kind text not null check (kind in ('chapter','mock_exam','pathway','user_upload')),
  title text not null,
  meta text,
  sort smallint,
  status text not null default 'published' check (status in ('draft','in_review','published','retired')),
  owner_user_id uuid,                  -- set for user-uploaded chapters (private)
  source_id text references sources(id) default 'naadi-original-v1',
  updated_at timestamptz default now()
);

create table chapter_sections (        -- "pages" of a chapter
  id text primary key,                 -- 'ch05-s03'
  chapter_id text not null references chapters(id) on delete cascade,
  sort smallint not null,
  heading text not null,
  body jsonb,                          -- TipTap/ProseMirror JSON (canonical)
  body_html text,                      -- rendered/seed HTML (import path from the review book)
  reading_minutes smallint,
  taxonomy_ids text[] default '{}'
);

create table media_assets (            -- figures, images, own videos, YouTube embeds, podcasts, PDFs
  id text primary key,                 -- 'ch05-fig01', 'yt:dQw4w9WgXcQ'
  kind text not null check (kind in ('svg','image','video_own','video_youtube','audio','pdf','link')),
  source_id text not null references sources(id),
  title text, caption text,
  url text,                            -- storage URL, YouTube watch URL, or external link
  youtube_id text, channel_id text, duration_s int,
  embeddable boolean,                  -- from YouTube videos.list status.embeddable
  metadata_fetched_at timestamptz,     -- YouTube API policy: refresh or drop metadata within 30 days
  attribution text,
  taxonomy_ids text[] default '{}',
  review_status text default 'unreviewed' check (review_status in ('unreviewed','approved','rejected'))
);

create table section_media (           -- ordered placement of media inside a section
  section_id text references chapter_sections(id) on delete cascade,
  media_id text references media_assets(id) on delete cascade,
  sort smallint, start_s int, end_s int,  -- video clip window
  primary key (section_id, media_id)
);

create table questions (
  id text primary key,                 -- 'ch05-q12'
  chapter_id text references chapters(id),
  exam text not null,
  block smallint,                      -- for mock exams
  stem_html text not null,
  options jsonb not null,              -- [{label:'A', html:'…'}]
  answer char(1) not null,
  answer_html text, explanation_html text, why_not_html text,
  educational_objective text,
  difficulty text check (difficulty in ('easy','medium','hard')),
  taxonomy_ids text[] default '{}',
  tags text[] default '{}',
  source_id text references sources(id) default 'naadi-original-v1',
  status text not null default 'published' check (status in ('draft','auto_checked','in_review','published','retired')),
  review jsonb default '{}',           -- {authorModel, reviewerModel, humanReviewers:[…], flags:[…]}
  stats jsonb default '{}',            -- {n, pCorrect, pointBiserial, optionCounts}
  created_at timestamptz default now()
);

create table flashcards (
  id uuid primary key default gen_random_uuid(),
  owner_user_id uuid,                  -- null = shared deck card
  deck text not null default 'Naadi core',
  front_html text not null, back_html text not null,
  question_id text references questions(id),
  section_id text references chapter_sections(id),
  taxonomy_ids text[] default '{}',
  origin text check (origin in ('seed','user','assistant','anki_import')) default 'user',
  created_at timestamptz default now()
);

-- Retrieval index (RAG) over sections, questions, notes, uploads — only rows whose source allows AI ingest
create table embeddings (
  id bigserial primary key,
  owner_user_id uuid,                  -- null = shared content
  ref_type text not null check (ref_type in ('section','question','note','upload','pathway')),
  ref_id text not null,
  chunk_index int not null default 0,
  content text not null,
  embedding halfvec(384),              -- fastembed BAAI/bge-small-en-v1.5 (384d); halfvec halves storage on the 500 MB free DB
  tsv tsvector generated always as (to_tsvector('english', content)) stored
);
create index on embeddings using hnsw (embedding halfvec_cosine_ops);
create index on embeddings using gin (tsv);

-- ───────────── Student activity ─────────────
create table attempts (
  id bigserial primary key,
  user_id uuid not null,
  question_id text not null references questions(id),
  block_id uuid,
  chosen char(1), correct boolean,
  seconds int, flagged boolean default false,
  mode text check (mode in ('tutor','timed','exam')),
  created_at timestamptz default now()
);

create table blocks (                  -- Qbank blocks / practice exams
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  config jsonb not null,               -- {exam, systems, disciplines, status:'unused|incorrect|flagged', count, mode, timeLimit}
  question_ids text[] not null,
  started_at timestamptz, finished_at timestamptz,
  score numeric
);

create table card_reviews (            -- FSRS state per user per card
  user_id uuid not null,
  card_id uuid not null references flashcards(id) on delete cascade,
  due timestamptz not null,
  stability double precision, difficulty double precision,
  elapsed_days int, scheduled_days int, reps int, lapses int,
  state smallint,                       -- ts-fsrs State enum
  last_review timestamptz,
  primary key (user_id, card_id)
);

create table notes (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  notebook_id uuid,
  anchor_type text check (anchor_type in ('section','question','video','pdf','free')),
  anchor_id text,                      -- section id, question id, media id, upload id
  anchor_pos jsonb,                    -- {from,to} text range | {t: seconds} | {page, rect}
  highlight_color text check (highlight_color in ('lime','violet','amber')),  -- key fact / formula / my mistake
  body jsonb,                          -- TipTap JSON
  ocr_text text,                       -- handwriting recognition result (for search)
  created_at timestamptz default now(), updated_at timestamptz default now()
);

create table notebooks (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  title text not null, cover text, paper text check (paper in ('ruled','dotted','grid','cornell','blank')) default 'ruled',
  created_at timestamptz default now()
);

create table ink_layers (              -- vector strokes per surface (chapter page, question, card, pdf page, notebook page)
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  surface_type text not null check (surface_type in ('section','question','flashcard','pdf_page','notebook_page','exam_scratch')),
  surface_id text not null,
  strokes jsonb not null default '[]', -- [{id, tool, color, size, points:[[x,y,pressure,t]…]}] in surface-relative coords
  recognized_text text,
  updated_at timestamptz default now(),
  unique (user_id, surface_type, surface_id)
);

create table uploads (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  kind text check (kind in ('pdf','image','video','link','anki')),
  storage_path text, original_name text, bytes bigint,
  processed_chapter_id text references chapters(id),
  status text default 'uploaded' check (status in ('uploaded','processing','ready','failed')),
  created_at timestamptz default now()
);

create table plan_items (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  day date not null,
  kind text check (kind in ('read','questions','assessment','cards','video','admin','rest')),
  title text not null,
  ref_type text, ref_id text,          -- chapter/section/block/pathway task
  target_minutes int, target_count int,
  status text default 'planned' check (status in ('planned','now','done','skipped','behind')),
  origin text default 'generator' check (origin in ('generator','user','assistant','seed')),
  sort smallint
);

-- ───────────── Pathway (MBBS → Match → landing in the USA → practice) ─────────────
create table pathway_stages (
  key text primary key,                -- see data/pathway/pathway_stages.json (16 keys: eligibility … california_bay_area)
  sort smallint, title text, summary_md text,
  official_links jsonb,                -- [{label,url}]
  last_verified date                   -- every stage must be re-verified on a schedule
);
create table pathway_tasks (
  id text primary key, stage_key text references pathway_stages(key),
  sort smallint, title text, detail_md text,
  due_rule text,                       -- e.g. 'match_day+14d', 'start_date-30d'
  cost_usd numeric, official_url text, last_verified date
);
create table user_pathway_tasks (
  user_id uuid, task_id text references pathway_tasks(id),
  status text default 'todo' check (status in ('todo','doing','done','na')),
  due date, notes text, primary key (user_id, task_id)
);
create table vault_documents (         -- passport, MBBS diploma, transcripts, OET result, DS-2019 …
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null, doc_type text, title text, storage_path text,
  expires_on date, created_at timestamptz default now()
);
create table costs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null, task_id text, label text, amount numeric, currency text default 'USD',
  paid_on date, created_at timestamptz default now()
);

-- ───────────── Assistant ─────────────
create table assistant_threads (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null, title text, created_at timestamptz default now()
);
-- LangGraph's PostgresSaver creates its own checkpoint tables (checkpoints, checkpoint_writes, checkpoint_blobs).

-- ───────────── Runtime configuration (nothing hardcoded in the app) ─────────────
create table app_config (              -- seeded from config/*.yaml; edited in the admin console; cached 60 s by the API
  key text primary key,                -- 'models', 'budget', 'search', 'feature_flags', 'ui_copy', 'exam_rules', 'storage'
  value jsonb not null,
  updated_by uuid, updated_at timestamptz default now()
);
create table app_config_history (key text, value jsonb, updated_by uuid, updated_at timestamptz default now());

-- Storage layout (Supabase Storage buckets, created by migration 0002):
--   content-public  : figures (SVG/PNG), chapter images, posters, PDF thumbnails   (public read, service-role write)
--   content-private : licensed/internal PDFs, source files                          (signed URLs only)
--   user-uploads    : student uploads (PDF/image)                                   (RLS by user folder: {user_id}/...)
--   vault           : passport, diploma, DS-2019 ...                                 (RLS by user folder; never sent to LLMs)
-- Own videos (large) live in Cloudflare R2 (10 GB free, $0 egress) with HLS/MP4 URLs stored in media_assets.url;
-- YouTube videos are stored as media_assets rows (kind='video_youtube', youtube_id) and rendered with the IFrame API.

-- ───────────── Free-tier budget guards ─────────────
create table search_cache (            -- Tavily results cached 7 days (free plan = 1,000 credits/month)
  query_norm text primary key, results jsonb not null, credits smallint not null default 1,
  created_at timestamptz default now()
);
create table llm_usage (               -- one row per OpenRouter call; enforces the free daily request cap
  id bigserial primary key, user_id uuid, role text, model text,
  ok boolean, status int, prompt_tokens int, completion_tokens int,
  created_at timestamptz default now()
);
create index on llm_usage (created_at);

-- ───────────── Row-level security ─────────────
do $$ declare t text; begin
  foreach t in array array['profiles','attempts','blocks','card_reviews','notes','notebooks','ink_layers','uploads',
                           'plan_items','user_pathway_tasks','vault_documents','costs','assistant_threads'] loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy own_rows on %I using (user_id = auth.uid()) with check (user_id = auth.uid())', t);
  end loop;
end $$;
-- Shared content tables (chapters, sections, questions, media, taxonomy, sources, pathway_*) are read-only to users:
-- enable RLS and add `for select using (status = 'published' or owner_user_id = auth.uid())` where applicable.
-- Writes to shared content happen only from the backend with the service role.
