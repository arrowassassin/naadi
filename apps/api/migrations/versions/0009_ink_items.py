"""Ink persistence (segment 12b): one row per ink item, server-merged, select-only for clients.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-16

- `ink_items`: one row per `InkItem` (packages/shared/src/ink.ts) of a layer, keyed `(layer_id, item_id)`.
  `updated_at_ms` and `deleted` mirror the item's `updatedAt` / `deleted` (checked), so the API's single
  `insert ... on conflict do update ... where` applies `mergeInkItems`' order in SQL: a higher `updated_at_ms` wins,
  on a tie a tombstone wins, on a full tie the stored row stays. `seq` comes from one sequence and is bumped on every
  write, so `seq > since` is an incremental sync cursor. `content_rev` is the revision of the surface content
  (section/question/card text) the item was drawn on, for the "this section was updated" notice. `written_at` drives
  the 90-day tombstone purge. The composite foreign key keeps an item with its own user's layer.
- `ink_layers` keeps one row per (user, surface_type, surface_id) and gains `rev` (max `seq` of its items),
  `item_count` (live items), `content_hash` (sha256 over every item's id/updatedAt/deleted, so replicas can check that
  they converged) and `purged_rev` (highest `seq` of a purged tombstone: a sync cursor below it gets `reset`).
  `strokes` is dropped (nothing was persisted before 12b).
- Both tables are written by the API only (every item validated and merged there): `authenticated` keeps SELECT on
  its own rows (RLS) and loses INSERT/UPDATE/DELETE. A client-side delete of a layer would remove items without
  tombstones, and other devices would resurrect them. The four `_own` policies stay, as in 0008: unreachable for the
  revoked commands, they document the row filter.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UID = "(select auth.uid())"
OWN = f"{UID} = user_id"

UPGRADE = f"""
alter table public.ink_layers drop column strokes;
alter table public.ink_layers
  add column rev bigint not null default 0 check (rev >= 0),
  add column item_count int not null default 0 check (item_count >= 0),
  add column content_hash text,
  add column purged_rev bigint not null default 0 check (purged_rev >= 0),
  add constraint ink_layers_id_user_key unique (id, user_id);

create sequence public.ink_items_seq as bigint;

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
  constraint ink_items_item_matches check (
    jsonb_typeof(item) = 'object'
    and item ->> 'id' = item_id::text
    and (item ->> 'updatedAt')::bigint = updated_at_ms
    and coalesce((item ->> 'deleted')::boolean, false) = deleted
  )
);
alter sequence public.ink_items_seq owned by public.ink_items.seq;
create index ink_items_layer_seq_idx on public.ink_items (layer_id, seq);
create index ink_items_user_idx on public.ink_items (user_id);
create index ink_items_tombstones_idx on public.ink_items (written_at) where deleted;

alter table public.ink_items enable row level security;
alter table public.ink_items force row level security;
revoke all on table public.ink_items from anon, authenticated;
grant select on table public.ink_items to authenticated;
grant all on table public.ink_items to service_role;
revoke all on sequence public.ink_items_seq from anon, authenticated;
grant usage, select on sequence public.ink_items_seq to service_role;
create policy ink_items_select_own on public.ink_items for select to authenticated using ({OWN});
create policy ink_items_insert_own on public.ink_items for insert to authenticated with check ({OWN});
create policy ink_items_update_own on public.ink_items for update to authenticated
  using ({OWN}) with check ({OWN});
create policy ink_items_delete_own on public.ink_items for delete to authenticated using ({OWN});

revoke insert, update, delete on table public.ink_layers from authenticated;
"""  # noqa: S608 - module constants only

DOWNGRADE = """
alter table public.ink_layers
  drop constraint if exists ink_layers_id_user_key,
  drop column if exists purged_rev,
  drop column if exists content_hash,
  drop column if exists item_count,
  drop column if exists rev,
  add column strokes jsonb not null default '[]';
grant insert, update, delete on table public.ink_layers to authenticated;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("drop table if exists public.ink_items cascade")
    op.execute(DOWNGRADE)
