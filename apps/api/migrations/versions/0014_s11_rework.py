"""S11 rework: one open diagnostic per student, one-way coach suggestion status, coach rotation (segment 11).

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-17

- `blocks`: a partial unique index allows at most one unfinished onboarding diagnostic
  (`config->>'purpose' = 'diagnostic'`) per student, so two concurrent `POST /api/diagnostic` calls cannot both
  create one (the loser gets the winner's block back). Any existing extra open diagnostics, all but the newest, lose
  their `purpose` tag first (they stay as ordinary timed blocks, with their attempts).
- `coach_suggestions`: a trigger makes `status` one-way. A row may leave `new` once and never return or change
  again, so accepting a suggestion cannot be repeated to add its plan items twice. A client (`authenticated`) may
  only dismiss; accepting goes through the API, which adds the items in the same transaction.
- `study_plans.last_coached_at`: when the nightly coach last looked at this plan's student. The pass takes the least
  recently coached first, so `max_users_per_run` rotates through everyone. Only the backend writes it.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = """
update public.blocks b set config = b.config - 'purpose'
where b.config->>'purpose' = 'diagnostic' and b.finished_at is null
  and exists (
    select 1 from public.blocks n
    where n.user_id = b.user_id and n.config->>'purpose' = 'diagnostic' and n.finished_at is null
      and (n.created_at, n.id) > (b.created_at, b.id)
  );
create unique index blocks_one_open_diagnostic on public.blocks (user_id)
  where config->>'purpose' = 'diagnostic' and finished_at is null;

create function public.naadi_coach_status_guard() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.status is distinct from old.status then
    if old.status <> 'new' then
      raise exception 'coach suggestion % is already %', old.id, old.status using errcode = 'check_violation';
    end if;
    if current_user = 'authenticated' and new.status <> 'dismissed' then
      raise exception 'only the API accepts a coach suggestion'
        using errcode = 'insufficient_privilege';
    end if;
  end if;
  return new;
end $$;
revoke all on function public.naadi_coach_status_guard() from public;
create trigger coach_suggestions_status_guard before update of status on public.coach_suggestions
  for each row execute function public.naadi_coach_status_guard();

alter table public.study_plans add column last_coached_at timestamptz;
create index study_plans_coach_rotation on public.study_plans (last_coached_at nulls first) where active;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("drop index if exists public.study_plans_coach_rotation")
    op.execute("alter table public.study_plans drop column if exists last_coached_at")
    op.execute("drop trigger if exists coach_suggestions_status_guard on public.coach_suggestions")
    op.execute("drop function if exists public.naadi_coach_status_guard()")
    op.execute("drop index if exists public.blocks_one_open_diagnostic")
