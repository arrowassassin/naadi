"""USMLE-2026 taxonomy, blueprint, content packs and the 2026 item formats (segment 23, S23-0).

Revision ID: 0039
Revises: 0038
Create Date: 2026-11-17

- `taxonomy_nodes` gains `outline_version` (so a future outline migrates side by side: every legacy node is
  'naadi-legacy', the new tree 'USMLE-2026'), `code` (the outline number, e.g. '08.05.01'), `level` (0 root,
  1 system, 2 category, 3 area) and `source_id` (the outline's `sources` row). New kinds: outline, outline_system,
  outline_category, outline_area, task. Shared read for clients, as before.
- `blueprint_targets`: items per leaf area (and co-coded axis targets) per Step. Shared read; seed-written only.
- `taxonomy_crosswalk`: legacy node -> USMLE-2026 node(s). Server-only (admin tooling and the next outline move).
- `content_packs`: one row per `data/content/<pack>/`. Server-only.
- `question_sets`: sequential item sets and abstract-format sets. The intro (the abstract, or the shared vignette)
  is stem-level text, so clients may read published sets; nothing answer-like lives on this table.
- `questions` gains `difficulty_level` 1-5, `item_format`, `set_id`/`set_order`, `why_not` (per option, jsonb),
  `figure_ids` and `pack_id`. None of the new columns is granted to clients: the column-level select grant of
  0001 stays exactly as it was, so `why_not` (like `why_not_html`) only ever leaves through the API after an
  attempt; the API serves the rest.
- `chapters.pack_id`, `chapter_sections.pack_id` and `chapter_sections.further_reading` (link-only references:
  [{label, url, sourceId}]), `flashcards.pack_id`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = "'exam','system','discipline','competency','clinical_science','topic'"
NEW_KINDS = f"{OLD_KINDS},'outline','outline_system','outline_category','outline_area','task'"


def upgrade() -> None:
    op.execute(f"""
alter table public.taxonomy_nodes
  add column if not exists outline_version text not null default 'naadi-legacy'
    check (outline_version ~ '^[A-Za-z0-9._-]{{1,32}}$'),
  add column if not exists code text check (code is null or code ~ '^[0-9]{{2}}(\\.[0-9]{{2}}){{0,2}}$'),
  add column if not exists level smallint check (level is null or level between 0 and 3),
  add column if not exists source_id text references public.sources(id);
alter table public.taxonomy_nodes drop constraint if exists taxonomy_nodes_kind_check;
alter table public.taxonomy_nodes add constraint taxonomy_nodes_kind_check check (kind in ({NEW_KINDS}));
create unique index if not exists taxonomy_nodes_version_code_idx on public.taxonomy_nodes (outline_version, code)
  where code is not null;

create table public.blueprint_targets (
  node_id text not null references public.taxonomy_nodes(id) on delete cascade,
  exam text not null check (exam in ('step1','step2ck','step3')),
  outline_version text not null,
  row_key text not null,
  row_label text not null,
  weight_min numeric not null,
  weight_max numeric not null check (weight_min <= weight_max),
  share integer not null check (share >= 0),
  min_items integer not null check (min_items >= 0),
  target_items integer not null check (target_items >= min_items),
  tested boolean not null default true,
  primary key (node_id, exam)
);
create index blueprint_targets_version_idx on public.blueprint_targets (outline_version, exam);
alter table public.blueprint_targets enable row level security;
revoke all on table public.blueprint_targets from anon, authenticated;
grant select on table public.blueprint_targets to anon, authenticated;
grant all on table public.blueprint_targets to service_role;
create policy blueprint_targets_read on public.blueprint_targets for select to anon, authenticated using (true);

create table public.taxonomy_crosswalk (
  from_id text not null references public.taxonomy_nodes(id) on delete cascade,
  to_id text not null references public.taxonomy_nodes(id) on delete cascade,
  to_version text not null,
  primary key (from_id, to_id)
);
alter table public.taxonomy_crosswalk enable row level security;
alter table public.taxonomy_crosswalk force row level security;
revoke all on table public.taxonomy_crosswalk from anon, authenticated;
grant all on table public.taxonomy_crosswalk to service_role;

create table public.content_packs (
  id text primary key check (id ~ '^[a-z0-9][a-z0-9-]{{0,63}}$'),
  title text not null check (char_length(title) between 1 and 200),
  owner text not null check (char_length(owner) between 1 and 64),
  outline_version text not null,
  systems text[] not null default '{{}}',
  updated_at timestamptz not null default now()
);
alter table public.content_packs enable row level security;
alter table public.content_packs force row level security;
revoke all on table public.content_packs from anon, authenticated;
grant all on table public.content_packs to service_role;

create table public.question_sets (
  id text primary key check (id ~ '^[a-z0-9][a-z0-9_.-]{{0,127}}$'),
  exam text not null check (exam in ('step1','step2ck','step3')),
  kind text not null check (kind in ('sequential','abstract')),
  title text,
  intro_html text,
  source_id text not null references public.sources(id) default 'naadi-original-v1',
  pack_id text references public.content_packs(id) on delete set null,
  status text not null default 'published' check (status in ('draft','in_review','published','retired')),
  created_at timestamptz not null default now(),
  check (kind <> 'abstract' or intro_html is not null)
);
alter table public.question_sets enable row level security;
revoke all on table public.question_sets from anon, authenticated;
grant select on table public.question_sets to anon, authenticated;
grant all on table public.question_sets to service_role;
create policy question_sets_read on public.question_sets for select to anon, authenticated
  using (status = 'published');

alter table public.questions
  add column if not exists difficulty_level smallint
    check (difficulty_level is null or difficulty_level between 1 and 5),
  add column if not exists item_format text not null default 'single'
    check (item_format in ('single','chart','sequential','abstract')),
  add column if not exists set_id text references public.question_sets(id) on delete set null,
  add column if not exists set_order smallint check (set_order is null or set_order between 1 and 9),
  add column if not exists why_not jsonb check (why_not is null or jsonb_typeof(why_not) = 'object'),
  add column if not exists figure_ids text[] not null default '{{}}',
  add column if not exists pack_id text references public.content_packs(id) on delete set null,
  add constraint questions_set_shape check ((set_id is null) = (set_order is null)
    and (item_format not in ('sequential','abstract') or set_id is not null));
create unique index if not exists questions_set_order_idx on public.questions (set_id, set_order)
  where set_id is not null;
create index if not exists questions_pack_idx on public.questions (pack_id) where pack_id is not null;

alter table public.chapters
  add column if not exists pack_id text references public.content_packs(id) on delete set null;
alter table public.chapter_sections
  add column if not exists pack_id text references public.content_packs(id) on delete set null,
  add column if not exists further_reading jsonb not null default '[]'
    check (jsonb_typeof(further_reading) = 'array');
alter table public.flashcards
  add column if not exists pack_id text references public.content_packs(id) on delete set null;
create index if not exists chapter_sections_taxonomy_idx on public.chapter_sections using gin (taxonomy_ids);
""")  # noqa: S608 - DDL built from module constants only


def downgrade() -> None:
    op.execute("""
drop index if exists public.chapter_sections_taxonomy_idx;
alter table public.flashcards drop column if exists pack_id;
alter table public.chapter_sections drop column if exists further_reading, drop column if exists pack_id;
alter table public.chapters drop column if exists pack_id;
drop index if exists public.questions_pack_idx;
drop index if exists public.questions_set_order_idx;
alter table public.questions drop constraint if exists questions_set_shape,
  drop column if exists pack_id, drop column if exists figure_ids, drop column if exists why_not,
  drop column if exists set_order, drop column if exists set_id, drop column if exists item_format,
  drop column if exists difficulty_level;
drop table if exists public.question_sets;
drop table if exists public.content_packs;
drop table if exists public.taxonomy_crosswalk;
drop table if exists public.blueprint_targets;
""")
    op.execute(f"""
delete from public.taxonomy_nodes where kind in ('outline','outline_system','outline_category','outline_area','task');
drop index if exists public.taxonomy_nodes_version_code_idx;
alter table public.taxonomy_nodes drop constraint if exists taxonomy_nodes_kind_check;
alter table public.taxonomy_nodes add constraint taxonomy_nodes_kind_check check (kind in ({OLD_KINDS}));
alter table public.taxonomy_nodes drop column if exists source_id, drop column if exists level,
  drop column if exists code, drop column if exists outline_version;
""")  # noqa: S608 - DDL built from module constants only
