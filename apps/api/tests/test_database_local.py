"""The database health and readiness paths against a LOCAL supabase/postgres (moved out of the `live`
suite): `select 1`, `/api/health` + `/api/ready` with a real pool, and Supabase's roles and auth schema."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from naadi_api.db import Database
from naadi_api.health.checks import database_check
from naadi_api.health.service import COMPONENTS, Check, HealthService
from naadi_api.main import create_app

from .conftest import fake_check, make_settings

pytestmark = [pytest.mark.db]


@pytest.fixture
def url() -> str:
    from tests.db.rls import database_url, require_local

    u = database_url()
    require_local(u)
    return u


async def test_database_select_1(url: str) -> None:
    db = Database(SecretStr(url))
    await db.open()
    try:
        out = await database_check(db)()
        assert out.status == "ok"
    finally:
        await db.close()


def test_health_database_ok_and_ready(url: str, static_dir: Path) -> None:
    s = make_settings(database_url=SecretStr(url), static_dir=static_dir)
    db = Database(s.database_url)
    # The real database check; the external components are not this test's subject.
    checks = {n: Check(n, fake_check(), ttl_s=60) for n in COMPONENTS}
    checks["database"] = Check("database", database_check(db), ttl_s=5)
    health = HealthService(checks=checks, version="t", environment="prod")
    with TestClient(create_app(s, health=health, db=db)) as c:
        h = c.get("/api/health")
        ready = c.get("/api/ready")
    assert h.status_code == 200, h.json()
    assert h.json()["components"]["database"]["status"] == "ok"
    assert ready.status_code == 200, ready.json()
    assert ready.json() == {"ready": True, "checks": {"settings": True, "database_pool": True}}


async def test_database_is_supabase_postgres_with_its_roles(url: str) -> None:
    # The real supabase/postgres image, so migrations (RLS for anon / authenticated / service_role,
    # references to auth.users) are tested against the same roles and schemas as the project.
    db = Database(SecretStr(url))
    await db.open()
    try:
        assert db.pool is not None
        async with db.pool.connection(timeout=10) as conn:
            cur = await conn.execute(
                "select rolname from pg_roles where rolname = any(%s) order by rolname",
                (["anon", "authenticated", "service_role"],),
            )
            roles = [r[0] for r in await cur.fetchall()]
            cur = await conn.execute("select exists(select 1 from pg_namespace where nspname = 'auth')")
            row = await cur.fetchone()
            cur = await conn.execute("show server_version_num")
            version = await cur.fetchone()
    finally:
        await db.close()
    assert roles == ["anon", "authenticated", "service_role"]
    assert row is not None and row[0] is True
    assert version is not None and int(version[0]) >= 170000
