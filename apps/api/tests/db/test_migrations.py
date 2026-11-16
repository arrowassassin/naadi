"""Migration round trip on a local Supabase Postgres (destructive: refuses any non-local DATABASE_URL).

empty → upgrade head → one row per table for two users → downgrade -1 → upgrade head, with an Aegra-style
`alembic_version` table present the whole time and left untouched. Also: concurrent upgrades serialise on
the advisory lock, a held lock times out with a clear error, and the CLI entry point works.
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from collections.abc import Iterator

import psycopg
import pytest

from naadi_api import migrate
from naadi_api.agent.migrate_all import AEGRA_TABLES

from .fixtures import USER_TABLE_NAMES, seed_admin_rows, seed_shared, seed_user_rows
from .rls import DB_MARKS, create_auth_user, require_local

pytestmark = DB_MARKS

NAADI_BUCKETS = {"content-public", "content-private", "user-uploads", "vault", "generated-media"}


def _scalar(conn: psycopg.Connection, query: str, params: tuple[object, ...] = ()) -> object:
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return row[0]


def _naadi_tables(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute(
        "select tablename from pg_tables where schemaname = 'public'"
        " and tablename not in ('naadi_alembic_version', 'alembic_version') and tablename <> all(%s)",
        (list(AEGRA_TABLES),),
    ).fetchall()
    return {r[0] for r in rows}


def _buckets(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute("select id from storage.buckets where id = any(%s)", (list(NAADI_BUCKETS),)).fetchall()
    return {r[0] for r in rows}


def _naadi_storage_policies(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute(
        "select policyname from pg_policies where schemaname = 'storage' and tablename = 'objects'"
        " and policyname like 'naadi\\_%'"
    ).fetchall()
    return {r[0] for r in rows}


@pytest.fixture
def local_url(migrated_url: str) -> Iterator[str]:
    require_local(migrated_url)
    yield migrated_url
    # Whatever happened, leave the shared test database at head for the other tests.
    migrate.upgrade(migrated_url)


@pytest.fixture
def aegra_version_table(local_url: str) -> Iterator[None]:
    # The database may already hold Aegra's real tables (the agent tests run migrate_all): keep its version row.
    with psycopg.connect(local_url, autocommit=True) as c:
        existed = c.execute("select to_regclass('public.alembic_version') is not null").fetchone()[0]
        saved = c.execute("select version_num from public.alembic_version").fetchall() if existed else []
        c.execute("create table if not exists public.alembic_version (version_num varchar(32) primary key)")
        c.execute("delete from public.alembic_version")
        c.execute("insert into public.alembic_version values ('aegra_head_marker')")
    yield
    with psycopg.connect(local_url, autocommit=True) as c:
        if existed:
            c.execute("delete from public.alembic_version")
            for (v,) in saved:
                c.execute("insert into public.alembic_version values (%s)", (v,))
        else:
            c.execute("drop table if exists public.alembic_version")


def test_round_trip_from_empty(local_url: str, aegra_version_table: None) -> None:
    head = migrate.head_revision()
    migrate.downgrade(local_url, "base")
    with psycopg.connect(local_url, autocommit=True) as c:
        assert _naadi_tables(c) == set(), "downgrade to base left tables behind"
        assert _buckets(c) == set()
        assert _naadi_storage_policies(c) == set()
        assert _scalar(c, "select count(*) from public.naadi_alembic_version") == 0

    before, after = migrate.upgrade(local_url)
    assert (before, after) == (None, head)

    users: list[uuid.UUID] = []
    with psycopg.connect(local_url) as c:
        with c.transaction():
            shared = seed_shared(c)
            for _ in range(2):
                uid = create_auth_user(c)
                users.append(uid)
                seed_user_rows(c, uid, shared)
            seed_admin_rows(c, users[0], shared)
        for table in USER_TABLE_NAMES:
            n = _scalar(c, f"select count(*) from public.{table} where user_id = any(%s)", (users,))  # noqa: S608
            assert n == 2, table
        assert _buckets(c) == NAADI_BUCKETS
        assert len(_naadi_storage_policies(c)) == 5
        assert _scalar(c, "select version_num from public.naadi_alembic_version") == head

    try:
        # down to 0001: drops 0004 (seed tables), 0003 (reading positions, figure sizes) and 0002 (storage);
        # keeps the core data
        migrate.downgrade(local_url, "0001")
        with psycopg.connect(local_url, autocommit=True) as c:
            assert _scalar(c, "select version_num from public.naadi_alembic_version") == "0001"
            assert _buckets(c) == set()
            assert _naadi_storage_policies(c) == set()
            assert _scalar(c, "select to_regclass('public.reading_positions')") is None
            for table in ("seed_runs", "pathway_guides", "plan_templates", "plan_template_days"):  # 0004
                assert _scalar(c, "select to_regclass(%s)", (f"public.{table}",)) is None, table
            assert _scalar(c, "select to_regprocedure('public.naadi_app_config_notify()')") is None
            assert (
                _scalar(
                    c,
                    "select count(*) from information_schema.columns where table_schema = 'public'"
                    " and (table_name, column_name) in (('sources', 'verified'), ('sources', 'verified_url'),"
                    " ('media_assets', 'content_sha256'))",
                )
                == 0
            )
            assert (
                _scalar(
                    c,
                    "select count(*) from information_schema.columns where table_schema = 'public'"
                    " and table_name = 'media_assets' and column_name in ('width', 'height')",
                )
                == 0
            )
            # downgrading storage must not touch the core data
            assert _scalar(c, "select count(*) from public.profiles where user_id = any(%s)", (users,)) == 2

        assert migrate.upgrade(local_url) == ("0001", head)
        with psycopg.connect(local_url, autocommit=True) as c:
            assert _buckets(c) == NAADI_BUCKETS
            assert len(_naadi_storage_policies(c)) == 5
            assert _scalar(c, "select version_num from public.naadi_alembic_version") == head
            # Aegra's version table is a different table and was never touched.
            assert c.execute("select version_num from public.alembic_version").fetchall() == [("aegra_head_marker",)]
            # A second upgrade is a no-op.
        assert migrate.upgrade(local_url) == (head, head)
    finally:
        # Leave an empty schema at head (no fixture rows) for the other tests.
        migrate.downgrade(local_url, "base")
        migrate.upgrade(local_url)
        with psycopg.connect(local_url, autocommit=True) as c:
            c.execute("delete from auth.users where id = any(%s)", (users,))


def test_downgrade_refuses_to_drop_non_empty_buckets(local_url: str) -> None:
    name = f"{uuid.uuid4()}/file.pdf"
    with psycopg.connect(local_url, autocommit=True) as c:
        c.execute("insert into storage.objects (bucket_id, name) values ('content-private', %s)", (name,))
    try:
        with pytest.raises(RuntimeError, match="refusing to drop non-empty buckets"):
            migrate.downgrade(local_url, "0001")
        with psycopg.connect(local_url, autocommit=True) as c:
            # the failed downgrade rolled back as a whole: still at head, policies intact
            assert _scalar(c, "select version_num from public.naadi_alembic_version") == migrate.head_revision()
            assert len(_naadi_storage_policies(c)) == 5
    finally:
        with psycopg.connect(local_url, autocommit=True) as c, c.transaction():
            c.execute("select set_config('storage.allow_delete_query', 'true', true)")
            c.execute("delete from storage.objects where bucket_id = 'content-private' and name = %s", (name,))


def _cli_env(url: str) -> dict[str, str]:
    return {"DATABASE_URL": url, "PATH": "/usr/bin:/bin", "LOG_LEVEL": "INFO"}


def test_concurrent_upgrades_serialise_on_the_advisory_lock(local_url: str) -> None:
    # Separate processes, like two containers (or a restart overlapping the old one) starting at once.
    migrate.downgrade(local_url, "0001")
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "naadi_api.migrate"],
            env=_cli_env(local_url),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(3)
    ]
    outs = [p.communicate(timeout=120) for p in procs]
    assert [p.returncode for p in procs] == [0, 0, 0], [o[1][-1000:] for o in outs]
    head = migrate.head_revision()
    events = [json.loads(line) for out, _ in outs for line in out.splitlines() if line.startswith("{")]
    oks = [e for e in events if e.get("event") == "migrate.ok"]
    assert len(oks) == 3 and all(e["revision_after"] == head for e in oks)
    # the migration body ran exactly once: duplicate policies or buckets would have failed a process
    with psycopg.connect(local_url, autocommit=True) as c:
        assert _buckets(c) == NAADI_BUCKETS
        assert len(_naadi_storage_policies(c)) == 5


def test_held_lock_times_out_with_a_clear_error(local_url: str) -> None:
    with psycopg.connect(local_url, autocommit=True) as holder:
        holder.execute("select pg_advisory_lock(%s)", (migrate.ADVISORY_LOCK_KEY,))
        try:
            with pytest.raises(migrate.MigrationLockTimeout, match="held the migration lock"):
                migrate.upgrade(local_url, lock_wait_s=2)
        finally:
            holder.execute("select pg_advisory_unlock(%s)", (migrate.ADVISORY_LOCK_KEY,))


def test_cli_entry_point(local_url: str) -> None:
    out = subprocess.run(
        [sys.executable, "-m", "naadi_api.migrate"],
        env=_cli_env(local_url),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert '"event": "migrate.ok"' in out.stdout
    assert "postgres:postgres" not in out.stdout + out.stderr
