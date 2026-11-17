"""Assistant threads map 1:1 to Aegra threads (segment 14).

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-17

- `assistant_threads` gains `aegra_thread_id` (unique: one row per Aegra thread), `last_message_at` (thread list
  order), `page_ref` (where the conversation started: `route`, `type`, `id` only — never selection text, a
  question's state or anything the student typed) and `memory_reflected_at` (the background memory manager runs
  at most once per thread per hour). Titles are capped at 120 characters.
- The tutor graph writes the row on the first turn and touches it on every turn; deleting a thread goes through
  the API, which also deletes the Aegra thread and its checkpoints. A client (`authenticated`) may read its own
  rows and rename them (`title`), nothing else: no insert, no delete, no other column.
- Long-term memory is NOT in a Naadi table: it lives in the Aegra store, namespace ("memories", user_id). The
  store's semantic index (local fastembed) adds LangGraph's `store_vectors` and `vector_migrations` tables, so
  `naadi_secure_aegra_tables()` (0005) now also closes those two to clients (RLS on, no anon/authenticated grant).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWN = "(select auth.uid()) = user_id"

# 0005's list plus the store's vector tables (langgraph.store.postgres, created when the index is configured).
AEGRA_TABLES = (
    "alembic_version",
    "assistant",
    "assistant_versions",
    "thread",
    "runs",
    "crons",
    "thread_ttl",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
    "store",
    "store_migrations",
    "store_vectors",
    "vector_migrations",
)


def _secure_fn(tables: tuple[str, ...]) -> str:
    array = "array[" + ",".join(f"'{t}'" for t in tables) + "]::text[]"
    return f"""
create or replace function public.naadi_secure_aegra_tables() returns int
language plpgsql set search_path = '' as $$
declare
  t text;
  n int := 0;
begin
  foreach t in array {array} loop
    if to_regclass('public.' || quote_ident(t)) is not null then
      execute format('alter table public.%I enable row level security', t);
      execute format('revoke all on table public.%I from anon, authenticated', t);
      n := n + 1;
    end if;
  end loop;
  return n;
end
$$;
revoke all on function public.naadi_secure_aegra_tables() from public, anon, authenticated;
select public.naadi_secure_aegra_tables();
"""


UPGRADE = f"""
alter table public.assistant_threads
  add column aegra_thread_id uuid,
  add column last_message_at timestamptz,
  add column page_ref jsonb,
  add column memory_reflected_at timestamptz,
  add constraint assistant_threads_aegra_thread_id_key unique (aegra_thread_id),
  add constraint assistant_threads_title_len check (title is null or char_length(title) between 1 and 120),
  add constraint assistant_threads_page_ref_shape check (
    page_ref is null or (
      jsonb_typeof(page_ref) = 'object'
      and page_ref - array['route','type','id'] = '{{}}'::jsonb
      and char_length(page_ref::text) <= 600
    )
  );
drop index if exists public.assistant_threads_user_idx;
create index assistant_threads_user_recent_idx
  on public.assistant_threads (user_id, (coalesce(last_message_at, created_at)) desc);

alter table public.assistant_threads enable row level security;
alter table public.assistant_threads force row level security;
revoke all on table public.assistant_threads from anon, authenticated;
grant select on table public.assistant_threads to authenticated;
grant update (title) on table public.assistant_threads to authenticated;
grant all on table public.assistant_threads to service_role;
drop policy if exists assistant_threads_select_own on public.assistant_threads;
drop policy if exists assistant_threads_insert_own on public.assistant_threads;
drop policy if exists assistant_threads_update_own on public.assistant_threads;
drop policy if exists assistant_threads_delete_own on public.assistant_threads;
create policy assistant_threads_select_own on public.assistant_threads for select to authenticated using ({OWN});
create policy assistant_threads_insert_own on public.assistant_threads for insert to authenticated
  with check ({OWN});
create policy assistant_threads_delete_own on public.assistant_threads for delete to authenticated using ({OWN});
create policy assistant_threads_update_own on public.assistant_threads for update to authenticated
  using ({OWN}) with check ({OWN});
"""


def upgrade() -> None:
    op.execute(UPGRADE)
    op.execute(_secure_fn(AEGRA_TABLES))


def downgrade() -> None:
    op.execute(_secure_fn(AEGRA_TABLES[:-2]))
    op.execute("drop index if exists public.assistant_threads_user_recent_idx")
    op.execute(
        "create index if not exists assistant_threads_user_idx on public.assistant_threads (user_id, created_at desc)"
    )
    op.execute(
        "alter table public.assistant_threads"
        " drop constraint if exists assistant_threads_page_ref_shape,"
        " drop constraint if exists assistant_threads_title_len,"
        " drop constraint if exists assistant_threads_aegra_thread_id_key,"
        " drop column if exists memory_reflected_at,"
        " drop column if exists page_ref,"
        " drop column if exists last_message_at,"
        " drop column if exists aegra_thread_id"
    )
    op.execute("grant select, insert, update, delete on table public.assistant_threads to authenticated")
