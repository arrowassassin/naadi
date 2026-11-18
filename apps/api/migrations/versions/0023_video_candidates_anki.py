"""YouTube candidates and Anki import (segment 20).

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-17

- `video_candidates`: the weekly `youtube_map` graph's proposals, one row per (video, section). Backend-only like
  the other admin tables (RLS forced, no grant to `anon` / `authenticated`): the admin queue reads and decides
  through the API. A candidate video is a `media_assets` row (`kind 'video_youtube'`, `source_id 'youtube'`,
  `review_status 'unreviewed'`), which RLS already hides from students until an admin approves it.
  `status` is pending | approved | rejected | stale; a rejected video keeps its row so it is never proposed again,
  and a `stale` one (private, deleted or no longer embeddable) has its stored title and description dropped
  (YouTube policy: non-authorised metadata is kept at most 30 days).
- `media_assets.owner_user_id`: private media (pictures of an imported Anki deck). Null = shared as before. The read
  policy now shows an approved private row only to its owner, so a student's images never reach another student.
  `youtube_id` gets an index (the mapper looks videos up by it). It is not unique: one row per video is kept by the
  deterministic id `yt-<id>` instead, and e2e helpers may seed several rows for one demo video.
- `flashcards` gains `tags`, `anki_guid` and `anki_note_type`. `(owner_user_id, anki_guid)` is unique, which makes a
  re-import of the same deck idempotent. These are written by the import API (privileged role); a student's own
  `origin 'user'` card can carry none of them (enforced from migration 0031 on).
- `agent_health.kind` gains `youtube_map` (one row per weekly run: counts and quota units only).
- `sources` gains the `user-upload` row private media points at (seeded here, not from config/sources.yaml, so the
  public license catalog stays a list of third-party sources).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
create table public.video_candidates (
  id uuid primary key default gen_random_uuid(),
  media_id text not null references public.media_assets(id) on delete cascade,
  section_id text references public.chapter_sections(id) on delete cascade,
  taxonomy_ids text[] not null default '{}',
  score real not null check (score >= 0 and score <= 2),
  matched_terms text[] not null default '{}',
  status text not null default 'pending' check (status in ('pending','approved','rejected','stale')),
  reviewed_by uuid references auth.users(id) on delete set null,
  reviewed_at timestamptz,
  created_at timestamptz not null default now(),
  check ((status = 'pending') = (reviewed_at is null))
);
create unique index video_candidates_media_section_idx
  on public.video_candidates (media_id, section_id) nulls not distinct;
create index video_candidates_status_idx on public.video_candidates (status, score desc, created_at);
create index video_candidates_section_idx on public.video_candidates (section_id) where section_id is not null;
alter table public.video_candidates enable row level security;
alter table public.video_candidates force row level security;
revoke all on table public.video_candidates from anon, authenticated;
grant all on table public.video_candidates to service_role;

insert into public.sources (id, name, url, license, allow_copy, allow_modify, allow_commercial, allow_ai_ingest,
                            attribution_template, notes)
values ('user-upload', 'A student''s own upload (private)', null, 'private-user-content', false, false, false, false,
        null, 'Private to its owner: never shared, never in shared retrieval.')
on conflict (id) do nothing;

alter table public.media_assets
  add column owner_user_id uuid references auth.users(id) on delete cascade;
create index media_assets_owner_idx on public.media_assets (owner_user_id) where owner_user_id is not null;
create index media_assets_youtube_id_idx on public.media_assets (youtube_id) where kind = 'video_youtube';
drop policy media_assets_read on public.media_assets;
create policy media_assets_read on public.media_assets for select to anon, authenticated
  using (review_status = 'approved' and (owner_user_id is null or owner_user_id = (select auth.uid())));

alter table public.flashcards
  add column tags text[] not null default '{}',
  add column anki_guid text check (anki_guid is null or char_length(anki_guid) between 1 and 200),
  add column anki_note_type text check (anki_note_type is null or char_length(anki_note_type) <= 200);
create unique index flashcards_owner_anki_guid_idx on public.flashcards (owner_user_id, anki_guid)
  where anki_guid is not null;
create index flashcards_owner_deck_idx on public.flashcards (owner_user_id, deck) where owner_user_id is not null;

alter table public.agent_health drop constraint agent_health_kind_check,
  add constraint agent_health_kind_check check (kind in ('ping','tavily_usage','coach','ink_ocr','youtube_map'));
"""

DOWNGRADE_FLASHCARDS = """
delete from public.agent_health where kind = 'youtube_map';
alter table public.agent_health drop constraint agent_health_kind_check,
  add constraint agent_health_kind_check check (kind in ('ping','tavily_usage','coach','ink_ocr'));
drop index if exists public.flashcards_owner_deck_idx;
drop index if exists public.flashcards_owner_anki_guid_idx;
alter table public.flashcards drop column if exists anki_note_type, drop column if exists anki_guid,
  drop column if exists tags;
"""

DOWNGRADE_MEDIA = """
drop policy if exists media_assets_read on public.media_assets;
create policy media_assets_read on public.media_assets for select to anon, authenticated
  using (review_status = 'approved');
drop index if exists public.media_assets_youtube_id_idx;
drop index if exists public.media_assets_owner_idx;
delete from public.media_assets where owner_user_id is not null;
alter table public.media_assets drop column if exists owner_user_id;
delete from public.sources s where s.id = 'user-upload'
  and not exists (select 1 from public.media_assets m where m.source_id = s.id);
"""

DOWNGRADE_CANDIDATES = "drop table if exists public.video_candidates;"


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE_FLASHCARDS)
    op.execute(DOWNGRADE_MEDIA)
    op.execute(DOWNGRADE_CANDIDATES)
