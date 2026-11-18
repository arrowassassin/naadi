"""Reading positions (cross-device reader sync) and intrinsic figure sizes.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-16

- `reading_positions`: one row per (user, section) the student has opened. `scroll_position` is the reader's
  layout-independent anchor (index of the top-most visible top-level block plus the fraction of it scrolled
  past), so the same spot is restored on an iPad, a phone or a laptop. `completed_at` is set once the student
  reaches the end of the section; `client_updated_at` (clamped by the API to the server clock) orders writes
  from several devices, so a late offline write never overwrites a newer position. User-owned table: RLS
  forced, the four `_own` policies, and the insert/update check also requires the section to be visible to
  the caller and to belong to the named chapter (no FK oracle for hidden or private content).
- `media_assets.width/height`: intrinsic size in px (an SVG's viewBox), so the reader reserves the space of a
  lazy-loaded figure and a restored scroll position does not jump. Nullable for media without a known size.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UID = "(select auth.uid())"
OWN = f"{UID} = user_id"
REFS = (
    "exists (select 1 from public.chapter_sections s"
    " where s.id = reading_positions.section_id and s.chapter_id = reading_positions.chapter_id)"
)

UPGRADE = f"""
alter table public.media_assets
  add column width int check (width is null or width > 0),
  add column height int check (height is null or height > 0);

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
create trigger reading_positions_touch_updated_at before update on public.reading_positions
  for each row execute function public.naadi_touch_updated_at();

alter table public.reading_positions enable row level security;
alter table public.reading_positions force row level security;
revoke all on table public.reading_positions from anon, authenticated;
grant select, delete on table public.reading_positions to authenticated;
grant insert (user_id, chapter_id, section_id, scroll_position, completed_at, client_updated_at)
  on table public.reading_positions to authenticated;
grant update (scroll_position, completed_at, client_updated_at) on table public.reading_positions to authenticated;
grant all on table public.reading_positions to service_role;
create policy reading_positions_select_own on public.reading_positions for select to authenticated using ({OWN});
create policy reading_positions_insert_own on public.reading_positions for insert to authenticated
  with check ({OWN} and ({REFS}));
create policy reading_positions_update_own on public.reading_positions for update to authenticated
  using ({OWN}) with check ({OWN} and ({REFS}));
create policy reading_positions_delete_own on public.reading_positions for delete to authenticated using ({OWN});
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("drop table if exists public.reading_positions cascade")
    op.execute("alter table public.media_assets drop column if exists height, drop column if exists width")
