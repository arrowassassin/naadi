"""Read-only check of the REAL Supabase project's schema after a deploy (the Space migrates at startup).

    cd apps/api && uv run python -m tests.db.live_check

Needs SUPABASE_URL (for the project ref) and SUPABASE_ACCESS_TOKEN. Uses the Supabase Management API SQL
endpoint over HTTPS with `read_only: true` (the build sandbox has no raw Postgres egress). Prints only
check names and pass/fail with counts or table names, never a value from the environment. Exit 0 when every
check passes. The same SQL runs against the local test database in tests/db/test_live_check.py.
"""

# ruff: noqa: S608 - every SQL string is built from module constants and the local head revision id

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from naadi_api import migrate

# The fixed contract: these 18 user-owned tables, each with RLS forced and exactly four `<t>_<cmd>_own` policies.
USER_TABLES = (
    "profiles",
    "attempts",
    "blocks",
    "card_reviews",
    "notes",
    "notebooks",
    "ink_layers",
    "uploads",
    "plan_items",
    "user_pathway_tasks",
    "vault_documents",
    "costs",
    "assistant_threads",
    "kb_sources",
    "ingest_items",
    "generated_media",
    "report_issues",
    "reading_positions",
)
BUCKETS = ("content-public", "content-private", "user-uploads", "vault", "generated-media")
HIDDEN_QUESTION_COLUMNS = (
    "answer",
    "answer_html",
    "explanation_html",
    "why_not_html",
    "educational_objective",
    "review",
    "stats",
)
CLIENT_CLOSED_TABLES = (*USER_TABLES, "embeddings", "app_config", "llm_usage", "naadi_alembic_version")

Rows = Sequence[dict[str, Any]]


def _array(values: Sequence[str]) -> str:
    return "array[" + ",".join(f"'{v}'" for v in values) + "]"


def ok_column(rows: Rows) -> tuple[bool, str]:
    """Default evaluator: one row with boolean `ok` and optional `detail`."""
    if not rows:
        return False, "no row"
    return bool(rows[0].get("ok")), str(rows[0].get("detail") or "")


def evaluate_user_tables(rows: Rows) -> tuple[bool, str]:
    """Rows: one per expected table (left-joined, so a missing table or one with zero policies still appears),
    with `exists`, `forced` and the sorted list of its `_own` policy commands."""
    by_name = {r["table_name"]: r for r in rows}
    problems: list[str] = []
    for t in USER_TABLES:
        r = by_name.get(t)
        if r is None or not r.get("exists"):
            problems.append(f"{t}: missing")
            continue
        if not r.get("forced"):
            problems.append(f"{t}: RLS not forced")
        cmds = sorted(r.get("cmds") or [])
        if cmds != ["DELETE", "INSERT", "SELECT", "UPDATE"]:
            problems.append(f"{t}: policies {cmds}")
    if len(by_name) != len(USER_TABLES):
        problems.append(f"expected {len(USER_TABLES)} rows, got {len(by_name)}")
    return not problems, "; ".join(problems) or f"{len(USER_TABLES)} tables"


USER_TABLES_SQL = f"""
select t.name as table_name, c.oid is not null as exists, coalesce(c.relforcerowsecurity, false) as forced,
       (select coalesce(array_agg(p.cmd order by p.cmd), '{{}}') from pg_policies p
         where p.schemaname = 'public' and p.tablename = t.name
           and p.policyname = t.name || '_' || lower(p.cmd) || '_own'
           and p.roles = '{{authenticated}}'
           and coalesce(p.qual, p.with_check) like '%( SELECT auth.uid() AS uid) = user_id%') as cmds
from unnest({_array(USER_TABLES)}) as t(name)
left join pg_class c on c.relname = t.name and c.relnamespace = 'public'::regnamespace and c.relkind = 'r'
order by 1
"""


@dataclass(frozen=True)
class Check:
    name: str
    sql: str
    evaluate: Callable[[Rows], tuple[bool, str]] = ok_column


def checks(head: str) -> list[Check]:
    return [
        Check(
            "naadi_alembic_version at head",
            # before the first migration the table is missing: the query errors and the check fails
            f"select coalesce((select version_num from public.naadi_alembic_version), 'none') = '{head}' as ok,"
            " coalesce((select version_num from public.naadi_alembic_version), 'none') as detail",
        ),
        Check(
            "every public table has RLS",
            "select count(*) = 0 as ok, coalesce(string_agg(relname, ','), '') as detail from pg_class"
            " where relnamespace = 'public'::regnamespace and relkind in ('r','p') and not relrowsecurity",
        ),
        Check(
            "34 Naadi tables present",
            "select count(*) = 34 as ok, count(*)::text as detail from pg_tables where schemaname = 'public'"
            " and tablename not in ('naadi_alembic_version', 'alembic_version')",
        ),
        Check("18 user tables: RLS forced + 4 own policies each", USER_TABLES_SQL, evaluate_user_tables),
        Check(
            "question answers hidden from anon and authenticated",
            "select to_regclass('public.questions') is not null and not exists (select 1"
            " from unnest(array['anon','authenticated']) r,"
            f" unnest({_array(HIDDEN_QUESTION_COLUMNS)}) c join pg_attribute a"
            "   on a.attrelid = 'public.questions'::regclass and a.attname = c"
            " where has_column_privilege(r, a.attrelid, a.attnum, 'select')) as ok, '' as detail",
        ),
        Check(
            "no client grants on user, embedding and admin tables",
            "select count(*) = 0 as ok, coalesce(string_agg(distinct grantee || ':' || table_name, ','), '')"
            " as detail from information_schema.role_table_grants where table_schema = 'public'"
            f" and grantee = 'anon' and table_name = any({_array(CLIENT_CLOSED_TABLES)})"
            " or table_schema = 'public' and grantee = 'authenticated'"
            " and table_name = any(array['embeddings','app_config','llm_usage','naadi_alembic_version'])",
        ),
        Check(
            "default privileges closed for anon/authenticated",
            "select count(*) = 0 as ok, count(*)::text as detail from pg_default_acl d"
            " cross join lateral aclexplode(d.defaclacl) a where d.defaclrole = 'postgres'::regrole"
            " and d.defaclnamespace = 'public'::regnamespace and a.grantee <> 0"
            " and a.grantee::regrole::text in ('anon', 'authenticated')",
        ),
        Check(
            "five storage buckets with limits, no catch-all MIME",
            "select count(*) = 5 and bool_and(file_size_limit is not null and allowed_mime_types is not null"
            " and not allowed_mime_types && array['application/octet-stream','*/*']) as ok,"
            f" count(*)::text as detail from storage.buckets where id = any({_array(BUCKETS)})",
        ),
        Check(
            "only content-public is public",
            "select count(*) = 5 and bool_and(public = (id = 'content-public')) as ok, '' as detail"
            f" from storage.buckets where id = any({_array(BUCKETS)})",
        ),
        Check(
            "five naadi storage.objects policies",
            "select count(*) = 5 as ok, count(*)::text as detail from pg_policies"
            " where schemaname = 'storage' and tablename = 'objects' and policyname like 'naadi\\_%'",
        ),
    ]


def run_sql(client: httpx.Client, ref: str, token: str, query: str) -> list[dict[str, Any]]:
    # No SDK covers the Management API SQL endpoint (supabase-py wraps the project APIs only).
    r = client.post(
        f"https://api.supabase.com/v1/projects/{ref}/database/query",
        json={"query": query, "read_only": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    r.raise_for_status()
    rows: list[dict[str, Any]] = r.json()
    return rows


def main() -> int:
    url, token = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_ACCESS_TOKEN", "")
    if not url or not token:
        print("live_check: SUPABASE_URL and SUPABASE_ACCESS_TOKEN are required", file=sys.stderr)
        return 2
    ref = (urlparse(url).hostname or "").split(".")[0]
    head = migrate.head_revision()
    all_checks = checks(head)
    failed = 0
    with httpx.Client(timeout=60) as client:
        for c in all_checks:
            try:
                ok, detail = c.evaluate(run_sql(client, ref, token, c.sql))
            except httpx.HTTPError as exc:
                ok, detail = False, type(exc).__name__
            failed += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {c.name}" + (f"  [{detail}]" if detail and not ok else ""))
    print(f"live_check: {len(all_checks) - failed}/{len(all_checks)} passed (head {head})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
