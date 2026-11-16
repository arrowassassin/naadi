"""Flashcard scheduling is server-authoritative: review logs, the learning step, card faces as content docs.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-16

- `card_review_logs`: one row per rating, with every field of a ts-fsrs `ReviewLog` (the memory state *before*
  the review: rating, state, due, stability, difficulty, elapsed_days, last_elapsed_days, scheduled_days,
  learning step, review time) plus `duration_ms`, so the FSRS optimizer can be trained on it, and the outcome
  (`next_*`, reps, lapses) so a repeated `client_review_id` (offline outbox, S18) returns the same answer
  without scheduling twice. Unique per (user, client_review_id).
- `card_reviews.step`: the learning / relearning step the Python `fsrs` scheduler keeps (null in Review).
- `card_reviews` and `card_review_logs` are written by the API only (it runs the scheduler, segment 10): a
  client-written due date or stability would let anyone forge intervals. Students may only SELECT their own rows
  (like the ink tables of 0009); deleting a card, or the account, cascades through the foreign keys. The four
  `_own` policies exist on both tables, as on every user table; the revoked commands never reach them.
- `flashcards.front_doc` / `back_doc`: the faces as TipTap documents (the reader's content schema) for
  `ContentRenderer`; `front_html` / `back_html` stay the interchange form (Anki export, search). `updated_at`
  records edits.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UID = "(select auth.uid())"
OWN = f"{UID} = user_id"
# The reviewed card must be visible to the caller (no FK oracle for someone's private card or a gated one).
LOG_REFS = "exists (select 1 from public.flashcards p where p.id = card_review_logs.card_id)"

UPGRADE = f"""
alter table public.flashcards
  add column front_doc jsonb check (front_doc is null or jsonb_typeof(front_doc) = 'object'),
  add column back_doc jsonb check (back_doc is null or jsonb_typeof(back_doc) = 'object'),
  add column updated_at timestamptz not null default now();
create trigger flashcards_touch_updated_at before update on public.flashcards
  for each row execute function public.naadi_touch_updated_at();
create index flashcards_question_idx on public.flashcards (question_id) where question_id is not null;

alter table public.card_reviews
  add column step smallint check (step is null or step >= 0);

create table public.card_review_logs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  card_id uuid not null references public.flashcards(id) on delete cascade,
  client_review_id uuid not null,
  rating smallint not null check (rating between 1 and 4),
  state smallint not null check (state between 0 and 3),
  due timestamptz not null,
  stability double precision,
  difficulty double precision,
  elapsed_days int not null check (elapsed_days >= 0),
  last_elapsed_days int not null check (last_elapsed_days >= 0),
  scheduled_days int not null check (scheduled_days >= 0),
  learning_step smallint check (learning_step is null or learning_step >= 0),
  review timestamptz not null,
  duration_ms int check (duration_ms is null or (duration_ms >= 0 and duration_ms <= 3600000)),
  next_state smallint not null check (next_state between 1 and 3),
  next_step smallint check (next_step is null or next_step >= 0),
  next_due timestamptz not null,
  next_stability double precision not null,
  next_difficulty double precision not null,
  reps int not null check (reps >= 1),
  lapses int not null check (lapses >= 0),
  created_at timestamptz not null default now(),
  unique (user_id, client_review_id)
);
create index card_review_logs_user_review_idx on public.card_review_logs (user_id, review desc);
create index card_review_logs_card_idx on public.card_review_logs (card_id, review);

alter table public.card_review_logs enable row level security;
alter table public.card_review_logs force row level security;
revoke all on table public.card_review_logs from anon, authenticated;
grant select on table public.card_review_logs to authenticated;
grant all on table public.card_review_logs to service_role;
create policy card_review_logs_select_own on public.card_review_logs for select to authenticated using ({OWN});
create policy card_review_logs_insert_own on public.card_review_logs for insert to authenticated
  with check ({OWN} and ({LOG_REFS}));
create policy card_review_logs_update_own on public.card_review_logs for update to authenticated
  using ({OWN}) with check ({OWN} and ({LOG_REFS}));
create policy card_review_logs_delete_own on public.card_review_logs for delete to authenticated using ({OWN});

revoke insert, update, delete on table public.card_reviews from authenticated;
"""  # noqa: S608 - module constants only


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("grant insert, update, delete on table public.card_reviews to authenticated")
    op.execute("drop table if exists public.card_review_logs cascade")
    op.execute("alter table public.card_reviews drop column if exists step")
    op.execute("drop index if exists public.flashcards_question_idx")
    op.execute("drop trigger if exists flashcards_touch_updated_at on public.flashcards")
    op.execute(
        "alter table public.flashcards drop column if exists updated_at,"
        " drop column if exists back_doc, drop column if exists front_doc"
    )
