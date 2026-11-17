"""Pathway links reference a `sources` row (segment 17 review).

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-17

Every media or text asset references a `sources` row. The pathway's links did not: `pathway_tasks.official_url` and
the stage links had no source, and the weekly check stores verbatim third-party quotes with only a URL. A task now
carries `source_id` (the seed importer sets it: `pathway-official-pages` for a host in `search.include_domains`,
`pathway-third-party-pages` otherwise; both are link-only), each stage link carries one inside its jsonb, and a check
row copies its task's source. Nothing here widens access: the columns are plain data on tables that are already
select-only for clients or closed to them, so no policy changes.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "alter table public.pathway_tasks add column if not exists source_id text references public.sources(id);"
        "alter table public.pathway_task_checks add column if not exists source_id text references public.sources(id)"
    )
    op.execute("create index if not exists pathway_tasks_source_idx on public.pathway_tasks (source_id)")


def downgrade() -> None:
    op.execute("drop index if exists public.pathway_tasks_source_idx")
    op.execute(
        "alter table public.pathway_task_checks drop column if exists source_id;"
        "alter table public.pathway_tasks drop column if exists source_id"
    )
