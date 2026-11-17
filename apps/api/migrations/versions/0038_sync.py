"""Offline sync (segment 18): tombstones, idempotent replays, per-row client stamps on notes and notebooks.

Revision ID: 0038
Revises: 0037
Create Date: 2026-11-17

- `sync_tombstones` (user table): one row per deleted LWW row, `(user_id, entity, key)` with the delete's
  `client_updated_at_ms`, so a delete replayed after an edit (or an edit replayed after a delete) resolves the same
  way on every device. Written by the API only (`naadi_api/sync/lww.py`); the student may SELECT their own rows
  (the four `_own` policies document the row filter, as `notes` in 0017). Kept 90 days (`sync/gc.py`, daily).
- `sync_ops` (backend-only): the stored response of each write sent with an `Idempotency-Key`, keyed by
  `(user_id, op_id)`; `status 0` marks a request in flight. No grant to `anon` or `authenticated` at all, no
  policy: clients cannot read stored bodies. Kept 24 hours (`sync/gc.py`, daily).
- `client_updated_at_ms bigint` on `notes` and `notebooks`: the device's stamp of the last write that won (last
  writer wins, ties to the stored row). Null for rows written before this migration (older than any stamp).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Entities whose deletes leave a tombstone (S18 outbox entity list: the LWW ones).
ENTITIES = ("note", "highlight", "notebook", "page", "plan_item", "user_pathway_task", "cost", "reading_position")


def _in(values: Sequence[str]) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


UPGRADE = f"""
create table public.sync_tombstones (
  user_id uuid not null references auth.users (id) on delete cascade,
  entity text not null check (entity in {_in(ENTITIES)}),
  key text not null check (char_length(key) between 1 and 200),
  client_updated_at_ms bigint not null,
  created_at timestamptz not null default now(),
  primary key (user_id, entity, key)
);
create index sync_tombstones_created_idx on public.sync_tombstones (created_at);
alter table public.sync_tombstones enable row level security;
alter table public.sync_tombstones force row level security;
create policy sync_tombstones_select_own on public.sync_tombstones for select to authenticated
  using ((select auth.uid()) = user_id);
create policy sync_tombstones_insert_own on public.sync_tombstones for insert to authenticated
  with check ((select auth.uid()) = user_id);
create policy sync_tombstones_update_own on public.sync_tombstones for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy sync_tombstones_delete_own on public.sync_tombstones for delete to authenticated
  using ((select auth.uid()) = user_id);
revoke all on table public.sync_tombstones from anon, authenticated;
grant select on table public.sync_tombstones to authenticated;

create table public.sync_ops (
  user_id uuid not null references auth.users (id) on delete cascade,
  op_id uuid not null,
  method text not null check (char_length(method) between 1 and 10),
  path text not null check (char_length(path) between 1 and 512),
  status int not null check (status = 0 or status between 200 and 499),
  body jsonb,
  created_at timestamptz not null default now(),
  primary key (user_id, op_id)
);
create index sync_ops_created_idx on public.sync_ops (created_at);
alter table public.sync_ops enable row level security;
alter table public.sync_ops force row level security;
revoke all on table public.sync_ops from anon, authenticated;

alter table public.notes add column client_updated_at_ms bigint;
alter table public.notebooks add column client_updated_at_ms bigint;
"""  # noqa: S608 - module constants only

DOWNGRADE_COLUMNS = """
alter table public.notebooks drop column if exists client_updated_at_ms;
alter table public.notes drop column if exists client_updated_at_ms;
"""

DOWNGRADE_TABLES = """
drop table if exists public.sync_ops;
drop table if exists public.sync_tombstones;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE_COLUMNS)
    op.execute(DOWNGRADE_TABLES)
