"""First deploy on a brand-new project (review: first-deploy race). Aegra's migrations run before Naadi's 0001,
so the startup closes the default privileges first: Aegra's tables are never readable by anon/authenticated,
not even in the window before 0005 secures them. Runs against a scratch database in the local container."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest

from ..db.rls import database_url, require_local
from .host_server import API_DIR

pytestmark = [pytest.mark.integration, pytest.mark.db]

# Run Aegra's migrations exactly as migrate_all does, in a fresh process (Aegra reads DATABASE_URL at import).
STEP = """
import sys
from naadi_api.agent import migrate_all
url = sys.argv[1]
migrate_all.close_client_defaults(url)
import os
os.environ["DATABASE_URL"] = url
migrate_all.aegra_upgrade()
"""


@pytest.fixture
def scratch_db() -> Iterator[str]:
    base = database_url()
    require_local(base)
    name = f"naadi_fresh_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(base, autocommit=True) as c:
        c.execute(f'create database "{name}"')
    url = urlunparse(urlparse(base)._replace(path=f"/{name}"))
    # What a new Supabase project has in `public` (the platform's defaults: clients get everything).
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(
            "grant usage on schema public to anon, authenticated, service_role;"
            " alter default privileges for role postgres in schema public"
            " grant all on tables to anon, authenticated, service_role;"
            " alter default privileges for role postgres in schema public"
            " grant all on sequences to anon, authenticated, service_role"
        )
    try:
        yield url
    finally:
        with psycopg.connect(base, autocommit=True) as c:
            c.execute(f'drop database if exists "{name}" with (force)')


def test_aegra_tables_are_closed_before_naadi_has_migrated(scratch_db: str) -> None:
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-c", STEP, scratch_db], cwd=API_DIR, env=env, check=True, capture_output=True, timeout=120
    )
    with psycopg.connect(scratch_db) as c:
        rows = c.execute(
            "select c.relname, has_table_privilege('anon', c.oid, 'select'),"
            " has_table_privilege('authenticated', c.oid, 'select')"
            " from pg_class c where c.relnamespace = 'public'::regnamespace and c.relkind = 'r'"
        ).fetchall()
        naadi = c.execute("select to_regclass('public.naadi_alembic_version')").fetchone()
    assert naadi == (None,)  # Naadi's migrations have not run: this is the window that matters
    names = {r[0] for r in rows}
    assert {"assistant", "thread", "runs", "crons"} <= names
    exposed = [r[0] for r in rows if r[1] or r[2]]
    assert exposed == []
