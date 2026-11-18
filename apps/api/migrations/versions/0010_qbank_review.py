"""Qbank review: graded-attempt card gate, no client DELETE on attempts/blocks, `attempts.correct` not client-readable.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-16

- A flag-only attempt row (the API keeps one when a student flags before answering) must not open the gate on
  question-linked flashcards: the policy now needs a *graded* attempt. Policies run with the caller's privileges
  and the caller can no longer read `attempts.correct`, so the test is a SECURITY DEFINER function that answers
  only for the caller's own rows (`auth.uid()`), with a pinned search_path.
- Students could delete an attempt and answer again: DELETE is revoked on `attempts` and `blocks` (account deletion
  still cascades through the foreign keys). Any future "remove a past block" goes through the API.
- `attempts.correct` is not selectable by clients (column-level SELECT on every other column), so mid-block
  correctness cannot leak through PostgREST once timed and exam blocks exist; the API serves it when allowed.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
create function public.qbank_has_graded_attempt(qid text) returns boolean
language sql stable security definer set search_path = ''
as $$
  select exists (
    select 1 from public.attempts a
    where a.user_id = (select auth.uid()) and a.question_id = qid and a.chosen is not null and a.correct is not null
  )
$$;
revoke all on function public.qbank_has_graded_attempt(text) from public, anon;
grant execute on function public.qbank_has_graded_attempt(text) to authenticated;

drop policy flashcards_read_authenticated on public.flashcards;
create policy flashcards_read_authenticated on public.flashcards for select to authenticated using (
  owner_user_id = (select auth.uid()) or (owner_user_id is null and (question_id is null
    or public.qbank_has_graded_attempt(question_id)))
);

revoke delete on table public.attempts from authenticated;
revoke delete on table public.blocks from authenticated;
revoke select on table public.attempts from authenticated;
grant select (id, user_id, question_id, block_id, chosen, seconds, flagged, mode, created_at)
  on table public.attempts to authenticated;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("grant select on table public.attempts to authenticated")
    op.execute("grant delete on table public.attempts to authenticated")
    op.execute("grant delete on table public.blocks to authenticated")
    op.execute("drop policy flashcards_read_authenticated on public.flashcards")
    uid = "(select auth.uid())"
    old = (
        "owner_user_id = UID or (owner_user_id is null and (question_id is null or exists"
        " (select 1 from public.attempts a where a.user_id = UID and a.question_id = flashcards.question_id)))"
    ).replace("UID", uid)
    op.execute(
        "create policy flashcards_read_authenticated on public.flashcards for select to authenticated using ("
        + old
        + ")"
    )
    op.execute("drop function public.qbank_has_graded_attempt(text)")
