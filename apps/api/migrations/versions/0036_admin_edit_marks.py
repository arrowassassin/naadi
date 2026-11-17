"""The seed keeps admin edits (segment 19 rework, review19 defect 1).

Revision ID: 0036
Revises: 0034
Create Date: 2026-11-17

- `chapters`, `questions` and `media_assets` gain `admin_edited_at`, as `chapter_sections` has had since 0004. A
  trigger (`naadi_mark_admin_edit`) sets it on every non-seed insert, and on every non-seed update that changes
  the row's content. Bookkeeping columns are ignored: `updated_at`, plus `stats` and `review` on questions (the
  nightly psychometrics and the review state are not content edits). The seed importer marks its own transactions
  (`set_config('naadi.seed', 'on', true)`), so seed writes never set the mark. The importer skips marked rows
  unless it runs with `--force`, which clears the mark.
- `section_media` gains `origin` ('seed' | 'admin'), default 'admin'. Every writer other than the seed (admin
  console, YouTube approvals, generated-media approvals) owns its placements, and the importer only replaces or
  deletes 'seed' rows. Existing placements of the seed's own figures (SVGs in the seed figure folder) are
  backfilled as 'seed'; everything else stays 'admin', so it is never deleted.
- Storage: `content-private` also accepts `image/svg+xml`, because unreviewed admin figures wait there (review19 #7)
  until an admin approves them into `content-public`. Every staged SVG has already been through the seed's
  sanitiser (seed/svg.py), as figures in `content-public` have; `user-uploads` still refuses SVG.
- No change to grants or RLS: these columns are written by the API and the importer only (clients have no write
  privileges on shared content), and `section_media` stays select-only for clients.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0036"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
alter table public.chapters add column admin_edited_at timestamptz;
alter table public.questions add column admin_edited_at timestamptz;
alter table public.media_assets add column admin_edited_at timestamptz;
alter table public.section_media
  add column origin text not null default 'admin' check (origin in ('seed','admin'));
update public.section_media sm set origin = 'seed'
  from public.media_assets m
 where m.id = sm.media_id and m.kind = 'svg' and m.url ~ '^[a-z0-9-]+/figures/[^/]+$';

update storage.buckets set allowed_mime_types = array_append(allowed_mime_types, 'image/svg+xml')
 where id = 'content-private' and not ('image/svg+xml' = any(allowed_mime_types));

create function public.naadi_mark_admin_edit() returns trigger
language plpgsql set search_path = '' as $$
begin
  if coalesce(current_setting('naadi.seed', true), '') = 'on' then
    return new;
  end if;
  if tg_op = 'INSERT' then
    new.admin_edited_at := coalesce(new.admin_edited_at, now());
  elsif (to_jsonb(new) - tg_argv) is distinct from (to_jsonb(old) - tg_argv) then
    new.admin_edited_at := now();
  end if;
  return new;
end
$$;
revoke all on function public.naadi_mark_admin_edit() from public, anon, authenticated;

create trigger chapters_mark_admin_edit before insert or update on public.chapters
  for each row execute function public.naadi_mark_admin_edit('updated_at', 'admin_edited_at');
create trigger questions_mark_admin_edit before insert or update on public.questions
  for each row execute function public.naadi_mark_admin_edit('stats', 'review', 'admin_edited_at');
create trigger media_assets_mark_admin_edit before insert or update on public.media_assets
  for each row execute function public.naadi_mark_admin_edit('admin_edited_at');
"""

DOWNGRADE_TRIGGERS = """
drop trigger if exists media_assets_mark_admin_edit on public.media_assets;
drop trigger if exists questions_mark_admin_edit on public.questions;
drop trigger if exists chapters_mark_admin_edit on public.chapters;
drop function if exists public.naadi_mark_admin_edit();
"""

DOWNGRADE = """
update storage.buckets set allowed_mime_types = array_remove(allowed_mime_types, 'image/svg+xml')
 where id = 'content-private';
alter table public.section_media drop column if exists origin;
alter table public.media_assets drop column if exists admin_edited_at;
alter table public.questions drop column if exists admin_edited_at;
alter table public.chapters drop column if exists admin_edited_at;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE_TRIGGERS)
    op.execute(DOWNGRADE)
