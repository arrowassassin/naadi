"""Timed and exam blocks: server-authoritative section timing, answer-change tracking (segment 09).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-16

- `block_sections`: one row per section of a timed or exam block (a mock exam is two sections of 40). Every
  timing column (`started_at`, `deadline_at`, `paused_at`, `paused_total_ms`, `ended_at`, `ended_reason`) is
  written by the API with its own clock; `authenticated` may only SELECT its own rows. The four `_own` policies
  exist (the RLS coverage contract) but INSERT/UPDATE/DELETE are not granted, the same backend-only pattern as
  `attempts.correct` in 0001. A missing row is never fresh time: the API treats it as an expired section.
- `blocks.break_used_ms`: break time already spent from the session's shared pool (exam_rules.break_minutes).
  Backend-only like the rest of `blocks` since 0008.
- `attempts.first_chosen`, `changes`, `answered_at`: the first choice, how many times the choice changed and
  when the last change was recorded. Backend-only (no client INSERT since 0008; UPDATE stays `flagged` only).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UID = "(select auth.uid())"
OWN = f"{UID} = user_id"

UPGRADE = f"""
alter table public.blocks add column break_used_ms bigint not null default 0 check (break_used_ms >= 0);

alter table public.attempts
  add column first_chosen char(1) check (first_chosen between 'A' and 'J'),
  add column changes smallint not null default 0 check (changes >= 0),
  add column answered_at timestamptz;

create table public.block_sections (
  block_id uuid not null,
  user_id uuid not null references auth.users(id) on delete cascade,
  n smallint not null check (n between 1 and 12),
  first_index smallint not null check (first_index >= 0),
  count smallint not null check (count between 1 and 80),
  time_limit_s int not null check (time_limit_s between 1 and 86400),
  started_at timestamptz,
  deadline_at timestamptz,
  paused_at timestamptz,
  paused_total_ms bigint not null default 0 check (paused_total_ms >= 0),
  ended_at timestamptz,
  ended_reason text check (ended_reason in ('submitted','expired')),
  created_at timestamptz not null default now(),
  primary key (block_id, n),
  foreign key (block_id, user_id) references public.blocks (id, user_id) on delete cascade,
  check ((ended_at is null) = (ended_reason is null)),
  check ((started_at is null) = (deadline_at is null)),
  check (paused_at is null or (started_at is not null and ended_at is null))
);
create index block_sections_user_open_idx on public.block_sections (user_id, block_id) where ended_at is null;

alter table public.block_sections enable row level security;
alter table public.block_sections force row level security;
revoke all on table public.block_sections from anon, authenticated;
grant select on table public.block_sections to authenticated;
grant all on table public.block_sections to service_role;
create policy block_sections_select_own on public.block_sections for select to authenticated using ({OWN});
create policy block_sections_insert_own on public.block_sections for insert to authenticated with check ({OWN});
create policy block_sections_update_own on public.block_sections for update to authenticated
  using ({OWN}) with check ({OWN});
create policy block_sections_delete_own on public.block_sections for delete to authenticated using ({OWN});
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("drop table if exists public.block_sections cascade")
    op.execute(
        "alter table public.attempts drop column if exists answered_at, drop column if exists changes,"
        " drop column if exists first_chosen"
    )
    op.execute("alter table public.blocks drop column if exists break_used_ms")
