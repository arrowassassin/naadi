"""Generation quota cannot be undone by the client (segment 15b review).

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-17

- `generated_media`: clients lose DELETE (grant revoked; the `_delete_own` policy now denies everything) and see only
  rows that are not hidden. `DELETE /api/generated/{id}` becomes a soft hide (`hidden_at`): the row stays, so it
  still counts toward the daily cap, but its file and prompt are removed. A `ready` row needs a file unless hidden.
- `reached_space`: set once a run is handed to the Space. The daily cap counts every run that reached it, failed
  ones included (a timeout still spent GPU), plus queued/generating/ready runs.
- `generation_state`: the global ZeroGPU pause in its own server-only table (RLS forced, no client privileges),
  instead of "the newest unexpired paused row", which a row owner could delete to end everyone's pause.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
alter table public.generated_media
  add column hidden_at timestamptz,
  add column reached_space boolean not null default false;
update public.generated_media set reached_space = true where status in ('ready', 'generating', 'paused');
alter table public.generated_media drop constraint generated_media_ready_has_file;
alter table public.generated_media add constraint generated_media_ready_has_file
  check (status <> 'ready' or storage_path is not null or hidden_at is not null);
drop index if exists public.generated_media_user_day_idx;
create index generated_media_user_day_idx on public.generated_media (user_id, kind, created_at desc)
  where status in ('queued','generating','ready') or reached_space;

revoke delete on table public.generated_media from authenticated;
alter policy generated_media_delete_own on public.generated_media using ((select auth.uid()) = user_id and false);
alter policy generated_media_select_own on public.generated_media
  using ((select auth.uid()) = user_id and hidden_at is null);

create table public.generation_state (
  id smallint primary key default 1 check (id = 1),
  paused_until timestamptz,
  updated_at timestamptz not null default now()
);
insert into public.generation_state (id, paused_until)
  select 1, max(paused_until) from public.generated_media where status = 'paused' and paused_until > now();
alter table public.generation_state enable row level security;
alter table public.generation_state force row level security;
revoke all on table public.generation_state from anon, authenticated;
grant all on table public.generation_state to service_role;
"""

DOWNGRADE_STATE = """
drop table if exists public.generation_state;
alter policy generated_media_select_own on public.generated_media using ((select auth.uid()) = user_id);
alter policy generated_media_delete_own on public.generated_media using ((select auth.uid()) = user_id);
grant delete on table public.generated_media to authenticated;
"""

DOWNGRADE = """
drop index if exists public.generated_media_user_day_idx;
create index generated_media_user_day_idx on public.generated_media (user_id, kind, created_at desc)
  where status in ('queued','generating','ready');
alter table public.generated_media drop constraint generated_media_ready_has_file;
delete from public.generated_media where hidden_at is not null;
alter table public.generated_media add constraint generated_media_ready_has_file
  check (status <> 'ready' or storage_path is not null);
alter table public.generated_media
  drop column if exists reached_space,
  drop column if exists hidden_at;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE_STATE)
    op.execute(DOWNGRADE)
