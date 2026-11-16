"""Catalog guards: fail as soon as a table is added without RLS, or a user table without the full policy set."""

from __future__ import annotations

import psycopg

from .fixtures import USER_TABLE_NAMES
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
        "   and has_table_privilege('authenticated', c.oid, 'select') order by 1"
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
                "naadi_alembic_version",
            ],
        ),
    ).fetchall()
    assert rows == []


def test_naadi_functions_pin_search_path_and_are_not_callable_by_clients(pg: psycopg.Connection) -> None:
    rows = pg.execute(
        "select p.proname, p.proconfig, has_function_privilege('anon', p.oid, 'execute'),"
        " has_function_privilege('authenticated', p.oid, 'execute')"
        " from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname like 'naadi\\_%'"
    ).fetchall()
    assert {r[0] for r in rows} == {"naadi_touch_updated_at", "naadi_app_config_history"}
    for name, config, anon_exec, auth_exec in rows:
        assert config == ['search_path=""'], name
        assert not anon_exec and not auth_exec, name
