"""Planner: rebalance proposals, calendar feed tokens, Web Push subscriptions (segment 16).

Revision ID: 0027
Revises: 0025
Create Date: 2026-11-17

- `plan_rebalances`: what the `planner` graph proposes after missed days (`proposal` = moves, drops, warnings, with a
  fingerprint), who asked (`trigger` missed | student | coach), the Aegra thread waiting on the confirm
  (`thread_id`) and the outcome (`status` proposed | accepted | declined | expired, `applied_at` set exactly when
  accepted). At most one `proposed` row per student (partial unique index): a new proposal expires the old one.
  Written by the API / graph only; students read their own.
- `plan_items` gains `client_updated_at_ms` (last-writer-wins stamp of a status change), `moved_from` (the day a
  rebalance moved the item from) and `rebalance_id`. Plan items become API rows like plan days (0013): clients keep
  SELECT, may UPDATE `status` and `client_updated_at_ms` only, and lose INSERT and DELETE (student items, moves and
  drops all go through the API, which checks the plan's span and keeps order).
- `calendar_feeds`: the ICS feed token, stored only as its SHA-256 (64 hex chars); the token itself is shown once
  when it is made. One active row per student (rotating revokes the old one: `revoked_at`). Written by the API;
  students read their own.
- `push_subscriptions`: one row per browser endpoint (https only; the push service's own URL). `failures`,
  `last_ok_at` and the per-day send counter (`last_sent_on`, `sent_today`) are the sender's bookkeeping. Written by
  the API; students read and delete their own subscriptions (turning reminders off on one device).
- RLS forced on all three with the four `_own` policies (as 0009 and 0013: the policies of revoked commands document
  the row filter).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UID = "(select auth.uid())"
OWN = f"{UID} = user_id"


def _policies(table: str) -> str:
    return f"""
alter table public.{table} enable row level security;
alter table public.{table} force row level security;
revoke all on table public.{table} from anon, authenticated;
grant all on table public.{table} to service_role;
create policy {table}_select_own on public.{table} for select to authenticated using ({OWN});
create policy {table}_insert_own on public.{table} for insert to authenticated with check ({OWN});
create policy {table}_delete_own on public.{table} for delete to authenticated using ({OWN});
create policy {table}_update_own on public.{table} for update to authenticated using ({OWN}) with check ({OWN});
"""


UPGRADE = f"""
create table public.plan_rebalances (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  plan_id uuid,
  created_at timestamptz not null default now(),
  trigger text not null check (trigger in ('missed','student','coach')),
  proposal jsonb not null check (jsonb_typeof(proposal) = 'object'),
  status text not null default 'proposed' check (status in ('proposed','accepted','declined','expired')),
  applied_at timestamptz,
  thread_id text check (thread_id is null or char_length(thread_id) <= 100),
  unique (id, user_id),
  foreign key (plan_id, user_id) references public.study_plans (id, user_id) on delete cascade,
  check ((status = 'accepted') = (applied_at is not null))
);
create unique index plan_rebalances_one_proposed_idx on public.plan_rebalances (user_id) where status = 'proposed';
create index plan_rebalances_user_created_idx on public.plan_rebalances (user_id, created_at desc);

alter table public.plan_items
  add column client_updated_at_ms bigint check (client_updated_at_ms is null or client_updated_at_ms >= 0),
  add column moved_from date,
  add column rebalance_id uuid,
  add constraint plan_items_rebalance_fkey foreign key (rebalance_id, user_id)
    references public.plan_rebalances (id, user_id) on delete set null (rebalance_id);
create index plan_items_rebalance_idx on public.plan_items (rebalance_id) where rebalance_id is not null;

create table public.calendar_feeds (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  token_hash text not null unique check (token_hash ~ '^[0-9a-f]{{64}}$'),
  created_at timestamptz not null default now(),
  revoked_at timestamptz
);
create unique index calendar_feeds_one_active_idx on public.calendar_feeds (user_id) where revoked_at is null;

create table public.push_subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  endpoint text not null unique check (endpoint ~ '^https://' and char_length(endpoint) <= 1024),
  p256dh text not null check (char_length(p256dh) between 1 and 200),
  auth text not null check (char_length(auth) between 1 and 100),
  ua_label text check (ua_label is null or char_length(ua_label) <= 80),
  created_at timestamptz not null default now(),
  last_ok_at timestamptz,
  failures smallint not null default 0 check (failures >= 0),
  last_sent_on date,
  sent_today smallint not null default 0 check (sent_today >= 0)
);
create index push_subscriptions_user_idx on public.push_subscriptions (user_id);

{_policies("plan_rebalances")}
grant select on table public.plan_rebalances to authenticated;

{_policies("calendar_feeds")}
grant select on table public.calendar_feeds to authenticated;

{_policies("push_subscriptions")}
grant select, delete on table public.push_subscriptions to authenticated;

-- plan items: the client reads and may only change a status through the API's own stamp column
revoke insert, update, delete on table public.plan_items from authenticated;
grant select on table public.plan_items to authenticated;
grant update (status, client_updated_at_ms) on table public.plan_items to authenticated;
"""  # noqa: S608 - DDL built from constants


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("grant insert, update, delete on table public.plan_items to authenticated")
    op.execute("drop table if exists public.push_subscriptions")
    op.execute("drop table if exists public.calendar_feeds")
    op.execute(
        "alter table public.plan_items drop constraint if exists plan_items_rebalance_fkey,"
        " drop column if exists rebalance_id, drop column if exists moved_from,"
        " drop column if exists client_updated_at_ms"
    )
    op.execute("drop table if exists public.plan_rebalances")
