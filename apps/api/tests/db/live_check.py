"""Read-only check of the REAL Supabase project's schema after a deploy (the Space migrates at startup).

    cd apps/api && uv run python -m tests.db.live_check

Needs SUPABASE_URL (for the project ref) and SUPABASE_ACCESS_TOKEN. Uses the Supabase Management API SQL
endpoint over HTTPS with `read_only: true` (the build sandbox has no raw Postgres egress). Prints only
check names and pass/fail with counts, never a value from the environment. Exit 0 when every check passes.
"""

from __future__ import annotations

import os
import sys
from typing import Any
from urllib.parse import urlparse

import httpx

from naadi_api import migrate

# Each check: (name, SQL returning one row with a boolean column `ok` and an optional `detail`).
CHECKS: list[tuple[str, str]] = [
    (
        "naadi_alembic_version at head",
        "select coalesce((select version_num from public.naadi_alembic_version), 'none') = '{head}' as ok,"
        " coalesce((select version_num from public.naadi_alembic_version), 'none') as detail",
    ),
    (
        "every public table has RLS",
        "select count(*) = 0 as ok, coalesce(string_agg(relname, ','), '') as detail from pg_class"
        " where relnamespace = 'public'::regnamespace and relkind in ('r','p') and not relrowsecurity",
    ),
    (
        "33 Naadi tables present",
        "select count(*) = 33 as ok, count(*)::text as detail from pg_tables where schemaname = 'public'"
        " and tablename not in ('naadi_alembic_version', 'alembic_version')",
    ),
    (
        "user tables forced RLS with 4 policies each",
        "select bool_and(c.relforcerowsecurity and p.n = 4) as ok, count(*)::text as detail"
        " from pg_class c join (select tablename, count(*) n from pg_policies where schemaname = 'public'"
        " and policyname like '%\\_own' group by 1) p on p.tablename = c.relname"
        " where c.relnamespace = 'public'::regnamespace and c.relname <> 'flashcards'",
    ),
    (
        "question answers hidden from anon and authenticated",
        "select not bool_or(has_column_privilege(r, 'public.questions', c, 'select')) as ok, '' as detail"
        " from unnest(array['anon','authenticated']) r,"
        " unnest(array['answer','answer_html','explanation_html','why_not_html','educational_objective',"
        "'review','stats']) c",
    ),
    (
        "anon has no privileges on user tables",
        "select count(*) = 0 as ok, count(*)::text as detail from information_schema.role_table_grants"
        " where table_schema = 'public' and grantee = 'anon' and table_name = any(array['profiles','attempts',"
        "'blocks','card_reviews','notes','notebooks','ink_layers','uploads','plan_items','user_pathway_tasks',"
        "'vault_documents','costs','assistant_threads','kb_sources','ingest_items','generated_media','report_issues'])",
    ),
    (
        "five storage buckets with limits",
        "select count(*) = 5 and bool_and(file_size_limit is not null and allowed_mime_types is not null) as ok,"
        " count(*)::text as detail from storage.buckets"
        " where id = any(array['content-public','content-private','user-uploads','vault','generated-media'])",
    ),
    (
        "only content-public is public",
        "select bool_and(public = (id = 'content-public')) as ok, '' as detail from storage.buckets"
        " where id = any(array['content-public','content-private','user-uploads','vault','generated-media'])",
    ),
    (
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
    failed = 0
    with httpx.Client(timeout=60) as client:
        for name, query in CHECKS:
            try:
                rows = run_sql(client, ref, token, query.replace("{head}", head))
                ok = bool(rows and rows[0].get("ok"))
                detail = str(rows[0].get("detail", "")) if rows else "no row"
            except httpx.HTTPError as exc:
                ok, detail = False, type(exc).__name__
            failed += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""))
    print(f"live_check: {len(CHECKS) - failed}/{len(CHECKS)} passed (head {head})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
