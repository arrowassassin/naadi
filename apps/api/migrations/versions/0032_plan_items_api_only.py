"""Plan items are written by the API only (segment 16 review).

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-17

0027 left `authenticated` an UPDATE grant on `plan_items (status, client_updated_at_ms)`. A signed-in client could
then PATCH its own rows straight through PostgREST, skipping the API's last-writer-wins check (a stamp from the far
future would freeze an item), its status whitelist and its clock bound. Every status change already goes through
the API (`PATCH /api/plan/items/{id}`, Today's status route), so clients keep SELECT only, like plan days'
neighbours `plan_rebalances` and `calendar_feeds`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("revoke update on table public.plan_items from authenticated")
    op.execute("grant select on table public.plan_items to authenticated")


def downgrade() -> None:
    op.execute("grant select on table public.plan_items to authenticated")
    op.execute("grant update (status, client_updated_at_ms) on table public.plan_items to authenticated")
