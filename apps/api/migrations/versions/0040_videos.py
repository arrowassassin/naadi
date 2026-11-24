"""Videos section: catalog schema and the student's watched checklist.

Revision ID: 0040
Revises: 0039
Create Date: 2026-11-24

- `media_assets.kind` gains `video_file`: an open-licence HTML5 video file served from its host (`url` = file URL).
  `licence jsonb` holds the licence snapshot {license_short_name, license_url, artist} taken when the file was
  catalogued and `page_url` the file's own page; no existing column fits (`attribution` is the display string only).
- `video_topics` (shared, read-only to clients): the coverage map of topics a student can browse. `media_ids` are the
  videos that teach the topic directly, `chapter_media_ids` related ones from the same subject, step and chapter
  (plain arrays, no FK: the seed importer owns their consistency).
- `video_catalog` (shared): browse metadata of one video (`media_assets` row). Readable only while its media row is
  visible (approved), like `section_media`. `topic_ids` are the direct topic links.
- `video_watches` (own rows): the student's own "watched" checklist; no scores or rewards. The insert/update check
  requires the video to be in the catalog as seen by the caller (i.e. approved).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UID = "(select auth.uid())"
OWN = f"{UID} = user_id"
REFS = "exists (select 1 from public.video_catalog c where c.media_id = video_watches.media_id)"
OLD_KINDS = "'svg','image','video_own','video_youtube','audio','pdf','link'"
NEW_KINDS = OLD_KINDS + ",'video_file'"

UPGRADE = f"""
alter table public.media_assets drop constraint media_assets_kind_check;
alter table public.media_assets add constraint media_assets_kind_check check (kind in ({NEW_KINDS}));
alter table public.media_assets add column licence jsonb, add column page_url text;

create table public.video_topics (
  id text primary key,
  title text not null,
  subject text not null,
  unit text,
  chapter text,
  step text not null check (step in ('step1','step2ck')),
  core boolean not null default false,
  sort int not null,
  media_ids text[] not null default '{{}}',
  chapter_media_ids text[] not null default '{{}}'
);
create index video_topics_browse_idx on public.video_topics (step, subject, sort);
alter table public.video_topics enable row level security;
revoke all on table public.video_topics from anon, authenticated;
grant select on table public.video_topics to anon, authenticated;
grant all on table public.video_topics to service_role;
create policy video_topics_read on public.video_topics for select to anon, authenticated using (true);

create table public.video_catalog (
  media_id text primary key references public.media_assets(id) on delete cascade,
  subject text not null,
  step text not null check (step in ('step1','step2ck')),
  style text not null check (style in ('sketch','whiteboard','animation','lecture')),
  core boolean not null default false,
  topic_ids text[] not null default '{{}}',
  provider text not null check (provider in ('youtube','commons')),
  sort int not null
);
create index video_catalog_browse_idx on public.video_catalog (step, subject, sort);
create index video_catalog_topics_idx on public.video_catalog using gin (topic_ids);
alter table public.video_catalog enable row level security;
revoke all on table public.video_catalog from anon, authenticated;
grant select on table public.video_catalog to anon, authenticated;
grant all on table public.video_catalog to service_role;
create policy video_catalog_read on public.video_catalog for select to anon, authenticated
  using (exists (select 1 from public.media_assets m where m.id = video_catalog.media_id));

create table public.video_watches (
  user_id uuid not null references auth.users(id) on delete cascade,
  media_id text not null references public.media_assets(id) on delete cascade,
  watched_at timestamptz not null default now(),
  primary key (user_id, media_id)
);
create index video_watches_media_idx on public.video_watches (media_id);
alter table public.video_watches enable row level security;
alter table public.video_watches force row level security;
revoke all on table public.video_watches from anon, authenticated;
grant select, delete on table public.video_watches to authenticated;
grant insert (user_id, media_id, watched_at) on table public.video_watches to authenticated;
grant update (watched_at) on table public.video_watches to authenticated;
grant all on table public.video_watches to service_role;
create policy video_watches_select_own on public.video_watches for select to authenticated using ({OWN});
create policy video_watches_insert_own on public.video_watches for insert to authenticated
  with check ({OWN} and ({REFS}));
create policy video_watches_update_own on public.video_watches for update to authenticated
  using ({OWN}) with check ({OWN} and ({REFS}));
create policy video_watches_delete_own on public.video_watches for delete to authenticated using ({OWN});
"""  # noqa: S608 - module constants only

DOWNGRADE = f"""
drop table if exists public.video_watches cascade;
drop table if exists public.video_catalog cascade;
drop table if exists public.video_topics cascade;
delete from public.media_assets where kind = 'video_file';
alter table public.media_assets drop column if exists page_url, drop column if exists licence;
alter table public.media_assets drop constraint media_assets_kind_check;
alter table public.media_assets add constraint media_assets_kind_check check (kind in ({OLD_KINDS}));
"""  # noqa: S608 - module constants only


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
