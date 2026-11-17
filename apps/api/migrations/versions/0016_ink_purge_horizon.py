"""Ink purge horizon (segment 12b rework).

Revision ID: 0016
Revises: 0014
Create Date: 2026-11-17

- `ink_layers.purged_before_ms`: the highest `updatedAt` of a tombstone the daily purge removed from the layer.
  Once a tombstone is gone, an item at or below this stamp that the table no longer holds is refused as `stale`,
  with `reset`. Without it, a device that was offline longer than the 90-day horizon could send an edit older than
  the deletion and bring the stroke back. Only the backend writes it.
- RLS: unchanged. `ink_layers` keeps RLS forced and its four `_own` policies, and clients still have select only
  (0009). The new column is covered by the existing select policy and grants nothing new.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
alter table public.ink_layers
  add column purged_before_ms bigint not null default 0 check (purged_before_ms >= 0);
comment on column public.ink_layers.purged_before_ms is
  'highest updatedAt of a purged tombstone; absent items at or below it are refused (stale)';
-- Still select-only for clients (0009); restated so this migration stands on its own.
alter table public.ink_layers enable row level security;
alter table public.ink_layers force row level security;
revoke insert, update, delete on table public.ink_layers from authenticated;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("comment on column public.ink_layers.purged_before_ms is null")
    op.execute("alter table public.ink_layers drop column if exists purged_before_ms")
