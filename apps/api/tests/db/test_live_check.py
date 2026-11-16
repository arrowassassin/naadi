"""The live checker's SQL, run against the local migrated database: all pass, and each mutation is caught."""

from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import dict_row

from naadi_api import migrate

from .live_check import checks
from .rls import DB_MARKS

pytestmark = DB_MARKS


def _run(conn: psycopg.Connection) -> dict[str, tuple[bool, str]]:
    out: dict[str, tuple[bool, str]] = {}
    for c in checks(migrate.head_revision()):
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(c.sql)  # no params: psycopg sends the text as is
            out[c.name] = c.evaluate(cur.fetchall())
    return out


def test_every_live_check_passes_on_the_migrated_schema(pg: psycopg.Connection) -> None:
    results = _run(pg)
    assert results and all(ok for ok, _ in results.values()), results


MUTATIONS = [
    (
        "drop policy costs_select_own on public.costs; drop policy costs_insert_own on public.costs;"
        " drop policy costs_update_own on public.costs; drop policy costs_delete_own on public.costs",
        "19 user tables",
    ),
    ("drop policy notes_delete_own on public.notes", "19 user tables"),
    ("alter table public.blocks no force row level security", "19 user tables"),
    ("create table public.naadi_probe (id int)", "every public table has RLS"),
    ("grant insert on public.ink_items to authenticated", "ink layers and items are select-only"),
    ("grant delete on public.ink_layers to authenticated", "ink layers and items are select-only"),
    ("grant select on public.embeddings to authenticated", "no client grants"),
    ("grant select (answer) on public.questions to anon", "question answers hidden"),
    (
        "alter default privileges for role postgres in schema public grant select on tables to anon",
        "default privileges",
    ),
    ("update storage.buckets set allowed_mime_types = array['application/octet-stream'] where id = 'vault'", "MIME"),
    ("update storage.buckets set public = true where id = 'vault'", "only content-public"),
    ("drop policy naadi_content_public_read on storage.objects", "storage.objects policies"),
]


@pytest.mark.parametrize(("sql", "check"), MUTATIONS, ids=[m[1] + ":" + m[0][:30] for m in MUTATIONS])
def test_each_mutation_is_caught(pg: psycopg.Connection, sql: str, check: str) -> None:
    with pg.transaction(force_rollback=True):
        pg.execute(sql)
        results = _run(pg)
    failing = [name for name, (ok, _) in results.items() if not ok]
    assert any(check in name for name in failing), (check, failing)
