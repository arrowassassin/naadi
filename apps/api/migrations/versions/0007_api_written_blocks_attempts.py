"""Blocks and attempts are written by the API only: revoke the client's INSERT (attempts) and INSERT/UPDATE (blocks).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-16

A client-inserted attempt would open the attempt gate (question-linked flashcards, explanations) without server
grading, and a client-written `started_at` / `config` would break timer integrity (segment 09). The API (segment 08)
creates blocks and attempts with its privileged role after checking ownership; students keep SELECT (own rows,
RLS), DELETE of their own rows, and UPDATE of `attempts.flagged`. The policies stay: they are unreachable for the
revoked commands and still document the intended row filter.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Column-level grants were given in 0001, so revoke at column level too (a table-level revoke alone leaves them).
    op.execute("revoke insert on table public.attempts from authenticated")
    attempt_cols = "user_id, question_id, block_id, chosen, seconds, flagged, mode"
    op.execute(f"revoke insert ({attempt_cols}) on public.attempts from authenticated")
    op.execute("revoke insert, update on table public.blocks from authenticated")
    op.execute("revoke insert (id, user_id, config, question_ids, started_at) on public.blocks from authenticated")
    op.execute("revoke update (started_at) on public.blocks from authenticated")


def downgrade() -> None:
    attempt_cols = "user_id, question_id, block_id, chosen, seconds, flagged, mode"
    op.execute(f"grant insert ({attempt_cols}) on table public.attempts to authenticated")
    op.execute("grant insert (id, user_id, config, question_ids, started_at) on table public.blocks to authenticated")
    op.execute("grant update (started_at) on table public.blocks to authenticated")
