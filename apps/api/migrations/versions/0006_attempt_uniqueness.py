"""One attempt row per (block, question): makes answering idempotent and a flag row the same row as the answer.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-16

`attempts` rows inside a block (segment 08) are written by the API (it grades: `correct` is backend-only). A
student's answer, flag and time for a question of a block live in one row, so a retried or offline-replayed
request cannot create a second answer. Attempts outside a block (block_id null: single-question drills, flashcard
links) are unaffected. No new table, so no new RLS policy: the existing own-row policies already cover the index.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "create unique index attempts_block_question_key on public.attempts (block_id, question_id)"
        " where block_id is not null"
    )


def downgrade() -> None:
    index = "public.attempts_block_question_key"
    op.execute(f"drop index if exists {index}")
