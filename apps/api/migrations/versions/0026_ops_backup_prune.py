"""Ops (segment 22): `agent_health.kind` gains `backup` and `prune`.

- `backup`: one row per backup run (nightly database dump, weekly Storage tarball): outcome, object paths in the
  private backup dataset, sizes and sha256 of the encrypted files, files deleted by the 30-day retention. Never a
  secret or a row of user data. The admin health panel reads the latest one.
- `prune`: one row per daily prune pass: rows deleted per table.

No table is added, so there is no new RLS: `agent_health` is already RLS-forced with no client privileges.

Other segments extend the same check constraint, so upgrade and downgrade read the values it holds now and add
or remove only these two: the result does not depend on which segment's migration ran first.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0027"  # head at integrate time (S16 took 0027 on 0025)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS = "array['backup','prune']"

_REBUILD = """
do $$
declare
  def text;
  vals text[];
begin
  select pg_get_constraintdef(c.oid) into def
    from pg_constraint c
   where c.conname = 'agent_health_kind_check' and c.conrelid = 'public.agent_health'::regclass;
  if def is null then
    raise exception 'agent_health_kind_check not found';
  end if;
  -- Every lower-case word of the definition except the column name and the type: the allowed values, whether
  -- Postgres renders them as ARRAY['a'::text, ...] (as written by `in (...)`) or as '{a,b}'::text[] (as written here).
  select array_agg(distinct m[1]) into vals
    from regexp_matches(def, '([a-z_]+)', 'g') as m
   where m[1] not in ('kind', 'text');
  vals := array(select distinct v from unnest({expr}) as v order by v);
  alter table public.agent_health drop constraint agent_health_kind_check;
  execute format(
    'alter table public.agent_health add constraint agent_health_kind_check check (kind = any (%L::text[]))', vals
  );
end
$$;
"""


def upgrade() -> None:
    op.execute(_REBUILD.replace("{expr}", f"vals || {_KINDS}"))


def downgrade() -> None:
    op.execute("delete from public.agent_health where kind in ('backup','prune')")
    keep = f"array(select v from unnest(vals) as v where v <> all ({_KINDS}))"  # noqa: S608 - module constants
    op.execute(_REBUILD.replace("{expr}", keep))
