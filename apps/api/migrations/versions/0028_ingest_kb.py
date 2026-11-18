"""Universal ingest and the private knowledge base (segment 15).

Revision ID: 0028
Revises: 0026
Create Date: 2026-11-17

- `sources` gains the `user-provided` row ("the student's own material"): every private chapter, figure, question,
  flashcard deck and knowledge-base entry made from a student's file or link references it. `allow_ai_ingest` is
  true because the owner's own retrieval may use it; owned rows are visible only to their owner (RLS below), so it
  never enters shared content or another student's retrieval, and nothing that references it is publishable.
- `media_assets.owner_user_id` already exists (S20, 0023: approved private rows are visible to their owner only).
  The read policy is split per role (anon: shared approved rows only) and a CHECK pins every owned row to one of the
  two private source rows (`user-upload` from 0023, `user-provided` here), whose licence allows no copying or
  commercial use, so no publishing path can treat a student's figure as shared content.
- `questions.owner_user_id` (null = shared): the student's own MCQs. Visible only to their owner (the same column
  grant hides the answer columns from clients, and the API's attempt gate applies to them unchanged).
- `ingest_items` gains `sha256`, `bytes`, `pages`, `progress` (0..100), `run_id`/`thread_id` (the background run of
  the `ingest` graph), `batch_id` (uploads grouped into one proposal), `duplicate_of` (an exact SHA-256 duplicate
  links to the earlier item instead of being processed again), `input_kind`, `placements jsonb[]` (exactly what a
  confirmed placement created, for Undo) and `stage_detail` (structured counts for the Library, no text).
- `uploads` gains `sha256`, `mime` and status `vaulted`; `kb_sources` gains `ingest_item_id`.
- `ingest_extracts` (new, server-only): the extracted document of an item (gzip JSON: sections, figures, tables,
  Q&A, MCQs, page texts), kept while the item waits for confirmation and for the Undo window, so a background run
  does not checkpoint megabytes of text and a re-confirmation after Undo does not repeat vision calls. RLS on and no
  client privileges at all (like `embeddings`); vault items never get a row (the gate runs before extraction).
- Writes: the pipeline (service role) owns `uploads`, `kb_sources` and `ingest_items`. Clients keep SELECT on their
  own rows; on `uploads` they may rename (`original_name`), on `ingest_items` they may set `confirmed_placement`
  only. INSERT/DELETE and every status column are backend-only (the four `_own` policies stay and document the row
  filter, as in 0017).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UID = "(select auth.uid())"
USER_SOURCE_ID = "user-provided"

INPUT_KINDS = (
    "pdf",
    "docx",
    "pptx",
    "xlsx",
    "csv",
    "txt",
    "md",
    "html",
    "epub",
    "rtf",
    "image",
    "audio",
    "video",
    "link",
    "anki",
    "zip",
    "paste",
)


def _in(values: Sequence[str]) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


UPGRADE = f"""
-- the student's own material
insert into public.sources (id, name, url, license, allow_copy, allow_modify, allow_commercial, allow_ai_ingest,
                            attribution_template, notes, verified, verified_url)
values ('{USER_SOURCE_ID}', 'Your own material', null, 'user-own-material', false, false, false, true, null,
        'Files, links and pasted text a student added. Private to that student (RLS); never published, never in '
        'shared content or another student''s retrieval.', true, null)
on conflict (id) do update set name = excluded.name, license = excluded.license, allow_copy = excluded.allow_copy,
  allow_modify = excluded.allow_modify, allow_commercial = excluded.allow_commercial,
  allow_ai_ingest = excluded.allow_ai_ingest, notes = excluded.notes, verified = excluded.verified;

-- media_assets: owned (private) figures
alter table public.media_assets
  add constraint media_assets_owned_private_source
    check (owner_user_id is null or source_id in ('user-upload', '{USER_SOURCE_ID}'));
drop policy media_assets_read on public.media_assets;
create policy media_assets_read_anon on public.media_assets for select to anon
  using (owner_user_id is null and review_status = 'approved');
create policy media_assets_read_authenticated on public.media_assets for select to authenticated
  using (review_status = 'approved' and (owner_user_id is null or owner_user_id = {UID}));

-- questions: the student's own MCQs
alter table public.questions add column owner_user_id uuid references auth.users(id) on delete cascade;
create index questions_owner_idx on public.questions (owner_user_id) where owner_user_id is not null;
grant select (owner_user_id) on table public.questions to authenticated;
drop policy questions_read on public.questions;
create policy questions_read_anon on public.questions for select to anon
  using (owner_user_id is null and status = 'published'
         and (chapter_id is null or exists (select 1 from public.chapters c where c.id = questions.chapter_id)));
create policy questions_read_authenticated on public.questions for select to authenticated
  using (((owner_user_id is null and status = 'published') or owner_user_id = {UID})
         and (chapter_id is null or exists (select 1 from public.chapters c where c.id = questions.chapter_id)));

-- uploads
alter table public.uploads
  add column sha256 text check (sha256 is null or sha256 ~ '^[0-9a-f]{{64}}$'),
  add column mime text check (mime is null or char_length(mime) <= 120),
  drop constraint uploads_status_check,
  add constraint uploads_status_check check (status in ('uploaded','processing','ready','failed','vaulted'));
create index uploads_user_sha_idx on public.uploads (user_id, sha256) where sha256 is not null;

-- ingest_items
alter table public.ingest_items
  add column input_kind text check (input_kind is null or input_kind in {_in(INPUT_KINDS)}),
  add column sha256 text check (sha256 is null or sha256 ~ '^[0-9a-f]{{64}}$'),
  add column bytes bigint check (bytes is null or bytes >= 0),
  add column pages int check (pages is null or pages >= 0),
  add column progress smallint not null default 0 check (progress between 0 and 100),
  add column run_id text,
  add column thread_id text,
  add column batch_id uuid,
  add column duplicate_of uuid,
  add column placements jsonb[] not null default '{{}}',
  add column stage_detail jsonb not null default '{{}}' check (jsonb_typeof(stage_detail) = 'object'),
  add constraint ingest_items_proposed_object
    check (proposed_placement is null or jsonb_typeof(proposed_placement) = 'object'),
  add constraint ingest_items_confirmed_object
    check (confirmed_placement is null or jsonb_typeof(confirmed_placement) = 'object'),
  add constraint ingest_items_error_code check (error is null or error ~ '^[a-z0-9_]{{1,64}}$');
create unique index ingest_items_id_user_key on public.ingest_items (id, user_id);
alter table public.ingest_items
  add constraint ingest_items_duplicate_of_fkey foreign key (duplicate_of, user_id)
    references public.ingest_items (id, user_id) on delete set null (duplicate_of);
create index ingest_items_user_sha_idx on public.ingest_items (user_id, sha256) where sha256 is not null;
create index ingest_items_batch_idx on public.ingest_items (user_id, batch_id) where batch_id is not null;
create index ingest_items_user_created_idx on public.ingest_items (user_id, created_at desc);

-- kb_sources
alter table public.kb_sources add column ingest_item_id uuid;
alter table public.kb_sources
  add constraint kb_sources_ingest_item_fkey foreign key (ingest_item_id, user_id)
    references public.ingest_items (id, user_id) on delete set null (ingest_item_id);

-- extracted documents (server-only)
create table public.ingest_extracts (
  item_id uuid primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  format smallint not null default 1 check (format >= 1),
  data bytea not null check (octet_length(data) <= 52428800),
  created_at timestamptz not null default now(),
  foreign key (item_id, user_id) references public.ingest_items (id, user_id) on delete cascade
);
create index ingest_extracts_user_idx on public.ingest_extracts (user_id, created_at);
alter table public.ingest_extracts enable row level security;
alter table public.ingest_extracts force row level security;
revoke all on table public.ingest_extracts from anon, authenticated;
grant all on table public.ingest_extracts to service_role;

-- writes belong to the pipeline (service role); clients read their own rows
revoke insert, update, delete on table public.uploads from authenticated;
grant update (original_name) on table public.uploads to authenticated;
revoke insert, update, delete on table public.kb_sources from authenticated;
revoke insert, update, delete on table public.ingest_items from authenticated;
grant update (confirmed_placement) on table public.ingest_items to authenticated;
"""  # noqa: S608 - module constants only

DOWNGRADE_EXTRACTS = """
drop table if exists public.ingest_extracts;
"""

DOWNGRADE = f"""
revoke update (confirmed_placement) on table public.ingest_items from authenticated;
grant insert, update, delete on table public.ingest_items to authenticated;
grant insert, update, delete on table public.kb_sources to authenticated;
revoke update (original_name) on table public.uploads from authenticated;
grant insert (id, user_id, kind, storage_path, original_name, bytes) on table public.uploads to authenticated;
grant update (original_name) on table public.uploads to authenticated;
grant delete on table public.uploads to authenticated;

alter table public.kb_sources drop constraint if exists kb_sources_ingest_item_fkey,
  drop column if exists ingest_item_id;

drop index if exists public.ingest_items_user_created_idx;
drop index if exists public.ingest_items_batch_idx;
drop index if exists public.ingest_items_user_sha_idx;
alter table public.ingest_items drop constraint if exists ingest_items_duplicate_of_fkey;
drop index if exists public.ingest_items_id_user_key;
alter table public.ingest_items
  drop constraint if exists ingest_items_error_code,
  drop constraint if exists ingest_items_confirmed_object,
  drop constraint if exists ingest_items_proposed_object,
  drop column if exists stage_detail,
  drop column if exists placements,
  drop column if exists duplicate_of,
  drop column if exists batch_id,
  drop column if exists thread_id,
  drop column if exists run_id,
  drop column if exists progress,
  drop column if exists pages,
  drop column if exists bytes,
  drop column if exists sha256,
  drop column if exists input_kind;

drop index if exists public.uploads_user_sha_idx;
update public.uploads set status = 'failed' where status = 'vaulted';
alter table public.uploads
  drop constraint uploads_status_check,
  add constraint uploads_status_check check (status in ('uploaded','processing','ready','failed')),
  drop column if exists mime,
  drop column if exists sha256;

drop policy if exists questions_read_authenticated on public.questions;
drop policy if exists questions_read_anon on public.questions;
delete from public.questions where owner_user_id is not null;
revoke select (owner_user_id) on table public.questions from authenticated;
drop index if exists public.questions_owner_idx;
alter table public.questions drop column if exists owner_user_id;
create policy questions_read on public.questions for select to anon, authenticated
  using (status = 'published'
         and (chapter_id is null or exists (select 1 from public.chapters c where c.id = questions.chapter_id)));

drop policy if exists media_assets_read_authenticated on public.media_assets;
drop policy if exists media_assets_read_anon on public.media_assets;
delete from public.media_assets where owner_user_id is not null and source_id = '{USER_SOURCE_ID}';
alter table public.media_assets drop constraint if exists media_assets_owned_private_source;
create policy media_assets_read on public.media_assets for select to anon, authenticated
  using (review_status = 'approved' and (owner_user_id is null or owner_user_id = {UID}));

delete from public.sources s where s.id = '{USER_SOURCE_ID}'
  and not exists (select 1 from public.chapters c where c.source_id = s.id);
"""  # noqa: S608 - module constants only


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE_EXTRACTS)
    op.execute(DOWNGRADE)
