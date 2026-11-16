"""Study plans and coach suggestions (segment 11).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-16

- `study_plans`: one generated plan per onboarding (template, the inputs it was generated from, generator
  version). At most one active plan per student (partial unique index). Written by the API only.
- `study_plan_days`: the plan's dated rows in the template CSV's shape (`GET /api/plan/export.csv` writes them back
  out), keyed `(plan_id, day_no)`. `template_day` is the first template day a row came from, `merged_days` every
  template day folded into it, `extra` marks an inserted review day. The student may change only `status` and
  `score_notes` (the CSV's last two columns); everything else is the generator's.
- `plan_items` gains `plan_id` / `plan_day`: generator items point at their plan and day (composite foreign key,
  so an item can only reference its own user's plan). Items keep full client access, as before.
- `coach_suggestions`: what the nightly `coach` graph proposes for a day. `facts` are the numbers the text was
  checked against, `action` what accepting applies (through the API). `source = 'llm'` rows carry `title`/`body`
  written by the model; `source = 'rules'` rows carry none (the client words them from `ui_copy`
  `coach.rules.<kind>.*` with `facts`). The student may update `status` only.
- RLS forced on all three with the four `_own` policies (as 0009: the revoked commands' policies document the row
  filter); no client INSERT/DELETE on any of them, no UPDATE on `study_plans`.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UID = "(select auth.uid())"
OWN = f"{UID} = user_id"
KINDS = "'catch_up_block','review_misses','cards_catchup','reread_section'"
DAY_STATUS = "'Not started','In progress','Done','Skipped'"

UPGRADE = f"""
create table public.study_plans (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  template_id text not null references public.plan_templates(id),
  inputs jsonb not null check (jsonb_typeof(inputs) = 'object'),
  generator_version smallint not null check (generator_version >= 1),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (id, user_id)
);
create unique index study_plans_one_active_idx on public.study_plans (user_id) where active;
create index study_plans_template_idx on public.study_plans (template_id);

create table public.study_plan_days (
  user_id uuid not null references auth.users(id) on delete cascade,
  plan_id uuid not null,
  day_no smallint not null check (day_no >= 1),
  date date not null,
  weekday text not null check (weekday in ('Mon','Tue','Wed','Thu','Fri','Sat','Sun')),
  week smallint not null check (week >= 1),
  phase text,
  chapter_ref text,
  chapter_ids text[] not null default '{{}}',
  topic text,
  chapter_questions text,
  qbank text,
  self_assessment text,
  admin_task text,
  extra_resource text,
  target_hours numeric(4,1) check (target_hours is null or target_hours between 0 and 24),
  template_day smallint not null check (template_day >= 1),
  merged_days smallint[] not null default '{{}}',
  extra boolean not null default false,
  status text not null default 'Not started' check (status in ({DAY_STATUS})),
  score_notes text not null default '' check (char_length(score_notes) <= 2000),
  primary key (plan_id, day_no),
  unique (plan_id, date),
  foreign key (plan_id, user_id) references public.study_plans (id, user_id) on delete cascade
);
create index study_plan_days_user_date_idx on public.study_plan_days (user_id, date);

alter table public.plan_items
  add column plan_id uuid,
  add column plan_day smallint check (plan_day is null or plan_day >= 1),
  add constraint plan_items_plan_fkey foreign key (plan_id, user_id)
    references public.study_plans (id, user_id) on delete cascade,
  add constraint plan_items_plan_link_check check ((plan_id is null) = (plan_day is null));
create index plan_items_plan_idx on public.plan_items (plan_id, plan_day) where plan_id is not null;

create table public.coach_suggestions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  for_date date not null,
  kind text not null check (kind in ({KINDS})),
  title text check (title is null or char_length(title) between 1 and 120),
  body text check (body is null or char_length(body) between 1 and 600),
  facts jsonb not null default '{{}}' check (jsonb_typeof(facts) = 'object'),
  action jsonb not null default '{{}}' check (jsonb_typeof(action) = 'object'),
  source text not null check (source in ('llm','rules')),
  status text not null default 'new' check (status in ('new','accepted','dismissed')),
  created_at timestamptz not null default now(),
  check ((source = 'llm') = (title is not null and body is not null))
);
create index coach_suggestions_user_date_idx on public.coach_suggestions (user_id, for_date desc, created_at);

alter table public.study_plans enable row level security;
alter table public.study_plans force row level security;
revoke all on table public.study_plans from anon, authenticated;
grant select on table public.study_plans to authenticated;
grant all on table public.study_plans to service_role;
create policy study_plans_select_own on public.study_plans for select to authenticated using ({OWN});
create policy study_plans_insert_own on public.study_plans for insert to authenticated with check ({OWN});
create policy study_plans_delete_own on public.study_plans for delete to authenticated using ({OWN});
create policy study_plans_update_own on public.study_plans for update to authenticated
  using ({OWN}) with check ({OWN});

alter table public.study_plan_days enable row level security;
alter table public.study_plan_days force row level security;
revoke all on table public.study_plan_days from anon, authenticated;
grant select on table public.study_plan_days to authenticated;
grant update (status, score_notes) on table public.study_plan_days to authenticated;
grant all on table public.study_plan_days to service_role;
create policy study_plan_days_select_own on public.study_plan_days for select to authenticated using ({OWN});
create policy study_plan_days_insert_own on public.study_plan_days for insert to authenticated with check ({OWN});
create policy study_plan_days_delete_own on public.study_plan_days for delete to authenticated using ({OWN});
create policy study_plan_days_update_own on public.study_plan_days for update to authenticated
  using ({OWN}) with check ({OWN});

alter table public.coach_suggestions enable row level security;
alter table public.coach_suggestions force row level security;
revoke all on table public.coach_suggestions from anon, authenticated;
grant select on table public.coach_suggestions to authenticated;
grant update (status) on table public.coach_suggestions to authenticated;
grant all on table public.coach_suggestions to service_role;
create policy coach_suggestions_select_own on public.coach_suggestions for select to authenticated using ({OWN});
create policy coach_suggestions_insert_own on public.coach_suggestions for insert to authenticated with check ({OWN});
create policy coach_suggestions_delete_own on public.coach_suggestions for delete to authenticated using ({OWN});
create policy coach_suggestions_update_own on public.coach_suggestions for update to authenticated
  using ({OWN}) with check ({OWN});
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute("drop table if exists public.coach_suggestions")
    op.execute(
        "alter table public.plan_items drop constraint if exists plan_items_plan_link_check,"
        " drop constraint if exists plan_items_plan_fkey, drop column if exists plan_day, drop column if exists plan_id"
    )
    op.execute("drop table if exists public.study_plan_days")
    op.execute("drop table if exists public.study_plans")
