"""Catalog guards: fail as soon as a table is added without RLS, or a user table without the full policy set."""

from __future__ import annotations

import uuid

import psycopg

from .fixtures import USER_TABLE_NAMES, Shared, insert_row
from .rls import DB_MARKS

pytestmark = DB_MARKS


def test_every_public_table_has_rls(pg: psycopg.Connection) -> None:
    rows = pg.execute(
        "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace"
        " where n.nspname = 'public' and c.relkind in ('r', 'p') and not c.relrowsecurity order by 1"
    ).fetchall()
    assert [r[0] for r in rows] == [], "tables in public without row-level security"


def test_every_user_owned_table_is_covered_by_the_rls_suite(pg: psycopg.Connection) -> None:
    rows = pg.execute(
        "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace"
        " join pg_attribute a on a.attrelid = c.oid and a.attname = 'user_id' and not a.attisdropped"
        " where n.nspname = 'public' and c.relkind = 'r'"
        "   and has_any_column_privilege('authenticated', c.oid, 'select') order by 1"
    ).fetchall()
    assert sorted(r[0] for r in rows) == sorted(USER_TABLE_NAMES)


def test_user_tables_force_rls_with_four_own_row_policies(pg: psycopg.Connection) -> None:
    for table in USER_TABLE_NAMES:
        row = pg.execute(
            "select relrowsecurity, relforcerowsecurity from pg_class where oid = %s::regclass", (f"public.{table}",)
        ).fetchone()
        assert row == (True, True), table
        policies = pg.execute(
            "select cmd, roles::text[], qual, with_check from pg_policies"
            " where schemaname = 'public' and tablename = %s",
            (table,),
        ).fetchall()
        assert sorted(p[0] for p in policies) == ["DELETE", "INSERT", "SELECT", "UPDATE"], table
        for cmd, roles, qual, with_check in policies:
            assert roles == ["authenticated"], (table, cmd, roles)
            # Each command carries exactly the clauses it needs; an UPDATE without an explicit WITH CHECK would
            # silently reuse USING and drop any reference checks added later.
            assert (qual is not None) == (cmd in ("SELECT", "UPDATE", "DELETE")), (table, cmd, "using")
            assert (with_check is not None) == (cmd in ("INSERT", "UPDATE")), (table, cmd, "with check")
            for expr in (qual, with_check):
                if expr is not None:
                    assert "( SELECT auth.uid() AS uid) = user_id" in expr, (table, cmd, expr)


def test_no_policy_is_granted_to_public(pg: psycopg.Connection) -> None:
    rows = pg.execute(
        "select tablename, policyname from pg_policies where schemaname = 'public' and 'public' = any(roles)"
    ).fetchall()
    assert rows == []


def test_anon_has_no_privileges_on_user_or_admin_tables(pg: psycopg.Connection) -> None:
    rows = pg.execute(
        "select table_name, privilege_type from information_schema.role_table_grants"
        " where table_schema = 'public' and grantee = 'anon' and table_name = any(%s)",
        (
            [
                *USER_TABLE_NAMES,
                "app_config",
                "app_config_history",
                "chapter_section_revisions",
                "search_cache",
                "llm_usage",
                "embeddings",
                "naadi_alembic_version",
                "seed_runs",
                "search_usage",
                "agent_health",
                "video_candidates",
            ],
        ),
    ).fetchall()
    assert rows == []


def test_seed_runs_is_admin_only(pg: psycopg.Connection) -> None:
    row = pg.execute(
        "select relrowsecurity, relforcerowsecurity, has_table_privilege('authenticated', oid, 'select')"
        " from pg_class where oid = 'public.seed_runs'::regclass"
    ).fetchone()
    assert row == (True, True, False)


def test_seed_content_tables_are_read_only_for_clients(pg: psycopg.Connection) -> None:
    for table in ("pathway_guides", "plan_templates", "plan_template_days"):
        for role in ("anon", "authenticated"):
            row = pg.execute(
                "select has_table_privilege(%s, %s, 'select'),"
                " has_table_privilege(%s, %s, 'insert,update,delete,truncate')",
                (role, f"public.{table}", role, f"public.{table}"),
            ).fetchone()
            assert row == (True, False), (table, role)
        policies = pg.execute(
            "select cmd, roles::text[] from pg_policies where schemaname = 'public' and tablename = %s", (table,)
        ).fetchall()
        assert policies == [("SELECT", ["anon", "authenticated"])], table


def test_naadi_functions_pin_search_path_and_are_not_callable_by_clients(pg: psycopg.Connection) -> None:
    rows = pg.execute(
        "select p.proname, p.proconfig, has_function_privilege('anon', p.oid, 'execute'),"
        " has_function_privilege('authenticated', p.oid, 'execute')"
        " from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname like 'naadi\\_%'"
    ).fetchall()
    assert {r[0] for r in rows} == {
        "naadi_touch_updated_at",
        "naadi_app_config_history",
        "naadi_app_config_notify",
        "naadi_mark_section_edited",
        "naadi_secure_aegra_tables",
        "naadi_coach_status_guard",  # 0014: trigger function (fires for clients without EXECUTE)
    }
    for name, config, anon_exec, auth_exec in rows:
        assert config == ['search_path=""'], name
        assert not anon_exec and not auth_exec, name


def test_future_objects_in_public_grant_nothing_to_client_roles(pg: psycopg.Connection) -> None:
    """Default privileges: a table / sequence / function created later (Aegra, a new migration) is closed
    to anon and authenticated until a migration grants it explicitly. The RLS tripwire stays on top."""
    with pg.transaction(force_rollback=True):
        pg.execute("create table public.naadi_probe_future (id bigint generated by default as identity primary key)")
        pg.execute("create function public.naadi_probe_fn() returns int language sql as 'select 1'")
        rows = pg.execute(
            "select r, has_table_privilege(r, 'public.naadi_probe_future', 'select,insert,update,delete'),"
            " has_sequence_privilege(r, pg_get_serial_sequence('public.naadi_probe_future', 'id'), 'usage,select'),"
            " has_function_privilege(r, 'public.naadi_probe_fn()', 'execute')"
            " from unnest(array['anon', 'authenticated', 'service_role']) r order by r"
        ).fetchall()
    # Functions keep PostgreSQL's built-in PUBLIC execute grant (global, not schema-revocable); a function that
    # must not be callable revokes it explicitly, as 0001 does for its own.
    assert rows == [
        ("anon", False, False, True),
        ("authenticated", False, False, True),
        ("service_role", True, True, True),
    ]


def test_default_acl_for_postgres_in_public_has_no_client_roles(pg: psycopg.Connection) -> None:
    rows = pg.execute(
        "select d.defaclobjtype, a.grantee::regrole::text from pg_default_acl d"
        " cross join lateral aclexplode(d.defaclacl) a"
        " where d.defaclrole = 'postgres'::regrole and d.defaclnamespace = 'public'::regnamespace"
        " and a.grantee <> 0 and a.grantee::regrole::text in ('anon', 'authenticated')"
    ).fetchall()
    assert rows == []


def test_aegra_tables_are_closed_to_clients(pg: psycopg.Connection) -> None:
    """Aegra / LangGraph tables (created at container start, before Naadi's migrations) have RLS on and no
    anon/authenticated privilege: 0005's naadi_secure_aegra_tables() runs again after every startup."""
    from naadi_api.agent.migrate_all import AEGRA_TABLES

    rows = pg.execute(
        "select c.relname, c.relrowsecurity, has_table_privilege('anon', c.oid, 'select,insert,update,delete'),"
        " has_table_privilege('authenticated', c.oid, 'select,insert,update,delete')"
        " from pg_class c where c.relnamespace = 'public'::regnamespace and c.relname = any(%s)",
        (list(AEGRA_TABLES),),
    ).fetchall()
    for name, rls, anon, auth in rows:
        assert (rls, anon, auth) == (True, False, False), name
    with pg.transaction(force_rollback=True):
        pg.execute("create table if not exists public.store_migrations (v int)")
        pg.execute("alter table public.store_migrations disable row level security")
        pg.execute("grant select on public.store_migrations to authenticated")
        assert pg.execute("select public.naadi_secure_aegra_tables()").fetchone()[0] >= 1
        row = pg.execute(
            "select relrowsecurity, has_table_privilege('authenticated', oid, 'select') from pg_class"
            " where oid = 'public.store_migrations'::regclass"
        ).fetchone()
        assert row == (True, False)


def test_migration_and_startup_secure_the_same_aegra_tables() -> None:
    import importlib.util

    from naadi_api import migrate
    from naadi_api.agent.migrate_all import AEGRA_TABLES

    spec = importlib.util.spec_from_file_location(
        "m0005", migrate.MIGRATIONS_DIR / "versions" / "0005_agent_platform.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert tuple(mod.AEGRA_TABLES) == tuple(AEGRA_TABLES)


# Every SECURITY DEFINER function in `public` is listed here on purpose: it runs with its owner's rights, so a new
# one must be reviewed (pinned search_path, not callable by anon, answers only for the caller's own rows).
DEFINER_ALLOWLIST = {"qbank_has_graded_attempt": {"anon": False, "authenticated": True}}


def test_security_definer_functions_are_an_explicit_allowlist(pg: psycopg.Connection) -> None:
    rows = pg.execute(
        "select p.proname, p.proconfig, has_function_privilege('anon', p.oid, 'execute'),"
        " has_function_privilege('authenticated', p.oid, 'execute')"
        " from pg_proc p where p.pronamespace = 'public'::regnamespace and p.prosecdef order by 1"
    ).fetchall()
    naadi = {r[0] for r in rows if r[0].startswith("naadi_")}  # internal helpers: closed to clients
    for name, config, anon_exec, auth_exec in rows:
        if name in naadi:
            assert not anon_exec and not auth_exec, name
            continue
        assert name in DEFINER_ALLOWLIST, f"unreviewed SECURITY DEFINER function public.{name}"
        assert config == ['search_path=""'], name
        assert {"anon": anon_exec, "authenticated": auth_exec} == DEFINER_ALLOWLIST[name], name
    assert {r[0] for r in rows} - naadi == set(DEFINER_ALLOWLIST)


def test_graded_attempt_function_answers_only_for_the_caller(
    pg: psycopg.Connection, user_a: uuid.UUID, user_b: uuid.UUID, shared: Shared
) -> None:

    from .rls import as_user

    insert_row(pg, "attempts", {"user_id": user_a, "question_id": shared.question_id, "chosen": "A", "correct": True})
    insert_row(pg, "attempts", {"user_id": user_b, "question_id": shared.question_id, "flagged": True})
    q = "select public.qbank_has_graded_attempt(%s)"
    with as_user(pg, user_a) as conn:
        assert conn.execute(q, (shared.question_id,)).fetchone() == (True,)
    with as_user(pg, user_b) as conn:  # B has only a flag-only row and cannot see A's graded one
        assert conn.execute(q, (shared.question_id,)).fetchone() == (False,)
