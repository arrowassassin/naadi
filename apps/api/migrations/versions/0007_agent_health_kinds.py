"""agent_health records the daily Tavily usage reading as well as pings.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-16

`kind = 'tavily_usage'`: once a day the system cron (`tavily_usage` graph) reads Tavily's usage endpoint and
stores the reading next to our own ledger's count (`search_usage`). It only records: the monthly stop is still
counted from our ledger, so a throttled or failing usage endpoint never blocks search.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "alter table public.agent_health drop constraint agent_health_kind_check,"
        " add constraint agent_health_kind_check check (kind in ('ping','tavily_usage'))"
    )


def downgrade() -> None:
    op.execute("delete from public.agent_health where kind = 'tavily_usage'")
    op.execute(
        "alter table public.agent_health drop constraint agent_health_kind_check,"
        " add constraint agent_health_kind_check check (kind in ('ping'))"
    )
