"""Notes and notebooks remember when they were last EDITED (segment 13 rework: bounded last-writer-wins).

Revision ID: 0039
Revises: 0037
Create Date: 2026-11-17

Offline sync of notes, notebook pages and notebooks used to be last-ARRIVAL-wins: a device that had been offline
replayed an old edit on reconnect and silently replaced a newer edit made meanwhile on another device. Each write
now carries the time the student made the edit (`client_updated_at`, on the server's clock as the device sees it;
segment 12b's ink rule), and the API keeps the newer one: a write older than the stored `edited_at` is refused with
409 `note_conflict` / `notebook_conflict` and the client keeps both versions.

`edited_at` is that edit time, not the arrival time (`updated_at` stays the arrival time: lists sort by it and page
operations touch a notebook's `updated_at`). Existing rows start from their `updated_at`. The columns are plain data
on tables that are already select-only for clients (migration 0017), so no policy or grant changes: `authenticated`
holds table-level SELECT, which covers new columns.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0039"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("notes", "notebooks"):
        op.execute(f"alter table public.{table} add column if not exists edited_at timestamptz")
        op.execute(f"update public.{table} set edited_at = updated_at where edited_at is null")  # noqa: S608
        op.execute(
            f"alter table public.{table} alter column edited_at set default now(), alter column edited_at set not null"
        )


def downgrade() -> None:
    op.execute("alter table public.notebooks drop column if exists edited_at")
    op.execute("alter table public.notes drop column if exists edited_at")
