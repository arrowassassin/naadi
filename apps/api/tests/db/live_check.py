"""Read-only check of the REAL Supabase project's schema after a deploy (the Space migrates at startup).

    cd apps/api && uv run python -m tests.db.live_check [--seed]

`--seed` adds read-only checks that the seed import landed (counts from data/ and config/, every figure
node resolving to an approved media row with a Storage object); run it after the admin seed job.

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
from naadi_api.agent.migrate_all import AEGRA_TABLES

# The fixed contract: these user-owned tables, each with RLS forced and exactly four `<t>_<cmd>_own` policies.
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
    "ink_items",
    "block_sections",
    "card_review_logs",
    "study_plans",
    "study_plan_days",
    "coach_suggestions",
    "export_jobs",
)
# Written by the API only (the client may only SELECT its own rows): ink (0009), notes and notebooks (0017).
SELECT_ONLY_TABLES = (
    "public.ink_layers",
    "public.ink_items",
    "public.notes",
    "public.notebooks",
    "public.export_jobs",
)
# Card scheduling: only the API writes it (the client may only SELECT its own rows).
API_WRITTEN_TABLES = ("card_reviews", "card_review_logs")
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
CLIENT_CLOSED_TABLES = (
    *USER_TABLES,
    "embeddings",
    "app_config",
    "llm_usage",
    "naadi_alembic_version",
    "seed_runs",
    "search_usage",
    "agent_health",
    "video_candidates",
    "question_reviews",
    "authoring_runs",
    "admin_audit",
    "question_revisions",
)

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
            "52 Naadi tables present",
            "select count(*) = 52 as ok, count(*)::text as detail from pg_tables where schemaname = 'public'"
            " and tablename not in ('naadi_alembic_version', 'alembic_version')"
            f" and tablename <> all({_array(AEGRA_TABLES)})",
        ),
        Check(
            "Aegra tables present, RLS on, closed to anon/authenticated",
            "select count(*) filter (where relname = any("
            "array['assistant','thread','runs','crons','checkpoints','store'])) = 6"
            " and bool_and(relrowsecurity and not has_table_privilege('anon', oid, 'select')"
            " and not has_table_privilege('authenticated', oid, 'select')) as ok, count(*)::text as detail"
            f" from pg_class where relnamespace = 'public'::regnamespace and relname = any({_array(AEGRA_TABLES)})",
        ),
        Check("user tables: RLS forced + 4 own policies each", USER_TABLES_SQL, evaluate_user_tables),
        Check(
            "card scheduling is API-written (no client INSERT/UPDATE/DELETE on card_reviews, card_review_logs)",
            "select count(*) = 0 as ok, coalesce(string_agg(t || ':' || p, ','), '') as detail"
            f" from unnest({_array(API_WRITTEN_TABLES)}) t cross join unnest(array['INSERT','UPDATE','DELETE']) p"
            " where to_regclass('public.' || t) is null"
            " or has_table_privilege('authenticated', ('public.' || t)::regclass, p)"
            " or exists (select 1 from information_schema.column_privileges c where c.table_schema = 'public'"
            " and c.table_name = t and c.grantee = 'authenticated' and c.privilege_type = p)",
        ),
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
            " and table_name = any(array['embeddings','app_config','llm_usage','naadi_alembic_version','seed_runs'])",
        ),
        Check(
            "ink layers and items, notes and notebooks are select-only for clients",
            "select bool_and(has_table_privilege('authenticated', t, 'select')"
            " and not has_table_privilege('authenticated', t, 'insert,update,delete,truncate')) as ok, '' as detail"
            f" from unnest({_array(SELECT_ONLY_TABLES)}) t",
        ),
        Check(
            "generated media: clients may only select and delete their own rows",
            "select has_table_privilege('authenticated', 'public.generated_media', 'select,delete')"
            " and not has_any_column_privilege('authenticated', 'public.generated_media', 'insert,update')"
            " and not has_table_privilege('anon', 'public.generated_media', 'select')"
            " and exists (select 1 from information_schema.columns where table_schema = 'public'"
            " and table_name = 'generated_media' and column_name = 'media_asset_id')"
            " as ok, '' as detail",
        ),
        Check(
            "authoring: question_reviews and authoring_runs are backend-only; one active export job per student",
            "select not exists (select 1 from unnest(array['question_reviews','authoring_runs']) t"
            " where not (select relrowsecurity from pg_class where oid = ('public.' || t)::regclass)"
            " or has_any_column_privilege('anon', ('public.' || t)::regclass, 'select,insert,update')"
            " or has_any_column_privilege('authenticated', ('public.' || t)::regclass, 'select,insert,update')"
            " or has_table_privilege('authenticated', ('public.' || t)::regclass, 'delete'))"
            " and to_regclass('public.export_jobs_one_active_idx') is not null"
            " and not has_any_column_privilege('authenticated', 'public.export_jobs', 'insert,update') as ok,"
            " '' as detail",
        ),
        Check(
            "handwriting OCR columns present (ocr_status, ocr_hash, ocr_at) and notes.body_text",
            "select count(*) = 4 as ok, count(*)::text as detail from information_schema.columns"
            " where table_schema = 'public' and (table_name, column_name) in (('ink_layers','ocr_status'),"
            " ('ink_layers','ocr_hash'), ('ink_layers','ocr_at'), ('notes','body_text'))",
        ),
        Check(
            "study plans are API-written; plan days and coach suggestions take status only",
            "select has_table_privilege('authenticated', 'public.study_plans', 'select')"
            " and not has_any_column_privilege('authenticated', 'public.study_plans', 'insert,update')"
            " and not has_table_privilege('authenticated', 'public.study_plans', 'delete')"
            " and not has_any_column_privilege('authenticated', 'public.study_plan_days', 'insert')"
            " and not has_table_privilege('authenticated', 'public.study_plan_days', 'delete')"
            " and not has_column_privilege('authenticated', 'public.study_plan_days', 'topic', 'update')"
            " and has_column_privilege('authenticated', 'public.study_plan_days', 'status', 'update')"
            " and not has_any_column_privilege('authenticated', 'public.coach_suggestions', 'insert')"
            " and not has_table_privilege('authenticated', 'public.coach_suggestions', 'delete')"
            " and not has_column_privilege('authenticated', 'public.coach_suggestions', 'facts', 'update')"
            " and has_column_privilege('authenticated', 'public.coach_suggestions', 'status', 'update')"
            " as ok, '' as detail",
        ),
        Check(
            "attempts and blocks are written by the API only; attempts.correct is not client-readable",
            "select not has_any_column_privilege('authenticated', 'public.attempts', 'insert')"
            " and not has_table_privilege('authenticated', 'public.attempts', 'delete')"
            " and not has_column_privilege('authenticated', 'public.attempts', 'correct', 'select')"
            " and has_column_privilege('authenticated', 'public.attempts', 'chosen', 'select')"
            " and not has_any_column_privilege('authenticated', 'public.blocks', 'insert,update')"
            " and not has_table_privilege('authenticated', 'public.blocks', 'delete') as ok, '' as detail",
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


def seed_checks(expected: dict[str, int], app_config_keys: Sequence[str]) -> list[Check]:
    """Read-only checks that the seed import landed (`--seed`): counts from the bundle, figures resolvable."""

    def count(name: str, query: str, want: int) -> Check:
        return Check(f"seed: {name} = {want}", f"select count(*) = {want} as ok, count(*)::text as detail {query}")

    e = expected
    return [
        count(
            "chapters",
            "from public.chapters where owner_user_id is null and status = 'published'"
            " and kind in ('chapter','mock_exam')",
            e["chapters"],
        ),
        count(
            "chapter_sections",
            "from public.chapter_sections s join public.chapters c on c.id = s.chapter_id"
            " where c.owner_user_id is null and c.kind in ('chapter','mock_exam')",
            e["chapter_sections"],
        ),
        count(
            "questions (published, original)",
            "from public.questions where status = 'published' and source_id = 'naadi-original-v1'",
            e["questions"],
        ),
        count(
            "approved SVG figures",
            "from public.media_assets where kind = 'svg' and review_status = 'approved'"
            " and source_id = 'naadi-original-v1'",
            e["media_assets"],
        ),
        count(
            "section_media",
            "from public.section_media m join public.media_assets a on a.id = m.media_id"
            " where a.kind = 'svg' and a.source_id = 'naadi-original-v1'",
            e["section_media"],
        ),
        count("taxonomy_nodes", "from public.taxonomy_nodes", e["taxonomy_nodes"]),
        count(
            "questions with taxonomy",
            "from public.questions where source_id = 'naadi-original-v1' and cardinality(taxonomy_ids) > 0",
            e["question_map_entries"],
        ),
        count("pathway_stages", "from public.pathway_stages", e["pathway_stages"]),
        count("pathway_tasks", "from public.pathway_tasks", e["pathway_tasks"]),
        count("pathway_guides", "from public.pathway_guides", e["pathway_guides"]),
        count(
            "plan_template_days",
            "from public.plan_template_days where template_id = 'step1-60d'",
            e["plan_template_days"],
        ),
        # 'user-upload' is made by migration 0023 (private media), not by the seed bundle
        count("sources", "from public.sources where id <> 'user-upload'", e["sources"]),
        count(
            "app_config keys",
            f"from public.app_config where key = any({_array(app_config_keys)})",
            len(app_config_keys),
        ),
        Check(
            "seed: no [[figure: placeholder left",
            "select count(*) = 0 as ok, count(*)::text as detail from public.chapter_sections"
            " where body::text like '%[[figure:%' or coalesce(body_html, '') like '%[[figure:%'",
        ),
        Check(
            "seed: every figure node resolves to an approved media row with a Storage object",
            "select count(*) = 0 as ok, coalesce(string_agg(distinct n.id, ','), '') as detail from ("
            " select j.v #>> '{}' as id from public.chapter_sections s"
            " cross join lateral jsonb_path_query(s.body, 'strict $.** ? (@.type == \"figure\").attrs.mediaId') j(v)) n"
            " left join public.media_assets m on m.id = n.id and m.review_status = 'approved'"
            " left join storage.objects o on o.bucket_id = split_part(m.url, '/', 1)"
            "  and o.name = substr(m.url, length(split_part(m.url, '/', 1)) + 2)"
            " where o.id is null",
        ),
        Check(
            "seed: last admin seed job succeeded (if any ran)",
            "select coalesce((select status <> 'failed' from public.seed_runs where not (options ? 'dry_run'"
            " and (options->>'dry_run')::boolean) order by started_at desc limit 1), true) as ok,"
            " coalesce((select status from public.seed_runs order by started_at desc limit 1), 'none') as detail",
        ),
    ]


def bundle_expectations() -> tuple[dict[str, int], list[str]]:
    """Expected seed counts from the repo's data/ and config/ (the same numbers the importer verifies)."""
    from naadi_api.config_api.defaults import file_values
    from naadi_api.seed.data import load_bundle
    from naadi_api.seed.importer import expected_counts
    from naadi_api.settings import REPO_ROOT

    b = load_bundle(REPO_ROOT / "data", REPO_ROOT / "config")
    return expected_counts(b), sorted(file_values(REPO_ROOT / "config"))


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
    if "--seed" in sys.argv[1:]:
        expected, keys = bundle_expectations()
        all_checks += seed_checks(expected, keys)
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
