"""Integration tests against the real services (Supabase, OpenRouter, NVIDIA, Tavily).

`-m "integration and not db"` runs where raw Postgres is unreachable (the build sandbox);
CI runs `-m integration`, which includes the `db` tests. Missing configuration fails the test loudly.
None of these spend paid quota: OpenRouter key metadata and Tavily usage are free endpoints; the NVIDIA
probe is one free one-token call per configured model.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import naadi_api.health.checks as checks_mod
from naadi_api.config import load_models_config
from naadi_api.db import Database
from naadi_api.health.checks import (
    TAVILY_USAGE_URL,
    database_check,
    nvidia_check,
    openrouter_check,
    storage_check,
    tavily_check,
)
from naadi_api.health.service import COMPONENTS, Check, HealthService
from naadi_api.main import create_app
from naadi_api.settings import Settings

from .conftest import REPO_ROOT, fake_check, require_env

pytestmark = pytest.mark.integration


def _settings() -> Settings:
    return Settings(config_dir=REPO_ROOT / "config")


async def test_storage_lists_buckets_with_service_role() -> None:
    require_env("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
    out = await storage_check(_settings())()
    assert out.status == "ok"
    assert out.detail.startswith("Storage reachable")


async def test_openrouter_key_metadata() -> None:
    env = require_env("OPENROUTER_API_KEY")
    out = await openrouter_check(_settings())()
    assert out.status in ("ok", "degraded")
    assert out.free_tier is not None
    assert out.quota is not None and out.quota.unit == "requests"
    assert env["OPENROUTER_API_KEY"] not in out.detail


async def test_tavily_usage_is_read_once_and_served_from_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real check behind the real cache: two reads, one GET to the free usage endpoint (never /search).
    Tavily throttles that endpoint (HTTP 429); then the component is deterministically `degraded`."""
    require_env("TAVILY_API_KEY")
    s = _settings()
    sent: list[httpx.Request] = []

    class CountingClient(httpx.AsyncClient):
        """The real client (env proxy and CA intact); it only records what it sends."""

        async def send(self, request: httpx.Request, **kw: Any) -> httpx.Response:
            sent.append(request)
            return await super().send(request, **kw)

    monkeypatch.setattr(checks_mod.httpx, "AsyncClient", CountingClient)
    checks = {n: Check(n, fake_check(), ttl_s=60) for n in COMPONENTS}
    checks["tavily"] = Check("tavily", tavily_check(s, 950), ttl_s=300, timeout_s=15)
    svc = HealthService(checks=checks, version="t", environment="staging")
    first = await svc.component("tavily")
    second = await svc.component("tavily")
    assert len(sent) == 1
    assert (sent[0].method, str(sent[0].url)) == ("GET", TAVILY_USAGE_URL)
    assert (first.cached, second.cached) == (False, True)
    assert second.model_dump(exclude={"cached"}) == first.model_dump(exclude={"cached"})
    if "HTTP 429" in first.detail:
        assert first.status == "degraded" and first.quota is None
    else:
        assert first.status in ("ok", "degraded"), first.detail
        assert first.quota is not None and first.quota.unit == "credits" and first.quota.period == "month"


async def test_nvidia_probes_every_configured_model() -> None:
    """Every configured NVIDIA model is probed. Models that are the first entry of a role chain must answer;
    a fallback may be slow or failing, which must then be reported as `degraded` with a reason."""
    # In the build sandbox the network proxy injects the real key; NVIDIA_API_KEY holds a placeholder there.
    require_env("NVIDIA_API_KEY")
    s = _settings()
    cfg = load_models_config(s.config_dir)
    models = cfg.models_for("nvidia")
    primaries = {r.chain[0].model for r in cfg.roles.values() if r.chain and r.chain[0].provider == "nvidia"}
    out = await nvidia_check(s, models, timeout_s=cfg.providers["nvidia"].probe_timeout_s)()
    assert out.models is not None
    assert [m.model for m in out.models] == models
    by_id = {m.model: m for m in out.models}
    for mid in primaries:
        assert by_id[mid].ok, (mid, by_id[mid].detail)
    failing = [m for m in out.models if not m.ok]
    if failing:
        assert out.status == "degraded"
        for m in failing:
            assert m.detail and m.detail != "answers"
            assert m.model in out.detail
    else:
        assert out.status == "ok", out.detail


def test_health_endpoint_reports_real_external_components() -> None:
    require_env("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "OPENROUTER_API_KEY", "NVIDIA_API_KEY", "TAVILY_API_KEY")
    s = _settings()
    with TestClient(create_app(s)) as c:
        r = c.get("/api/health")
    body = r.json()
    for name in ("storage", "openrouter", "nvidia", "tavily"):
        assert body["components"][name]["status"] in ("ok", "degraded"), (name, body["components"][name]["detail"])
    for secret in s.secret_values():
        assert secret not in r.text


@pytest.mark.db
async def test_database_select_1() -> None:
    require_env("DATABASE_URL")
    db = Database(_settings().database_url)
    await db.open()
    try:
        out = await database_check(db)()
        assert out.status == "ok"
    finally:
        await db.close()


@pytest.mark.db
def test_health_database_ok_and_ready() -> None:
    # Only DATABASE_URL is required: in CI it points at the supabase/postgres service container and the
    # external components (no secrets there) report `down: not configured`, which keeps the overall
    # status at `degraded` with HTTP 200; on the Space every component is configured.
    require_env("DATABASE_URL")
    with TestClient(create_app(_settings())) as c:
        health = c.get("/api/health")
        ready = c.get("/api/ready")
    assert health.status_code == 200, health.json()
    assert health.json()["components"]["database"]["status"] == "ok"
    assert health.json()["status"] in ("ok", "degraded")
    assert ready.status_code == 200, ready.json()
    assert ready.json() == {"ready": True, "checks": {"settings": True, "database_pool": True}}


@pytest.mark.db
async def test_database_is_supabase_postgres_with_its_roles() -> None:
    # CI uses the real supabase/postgres image, so later migrations (RLS for anon / authenticated /
    # service_role, references to auth.users) are tested against the same roles and schemas as the project.
    require_env("DATABASE_URL")
    db = Database(_settings().database_url)
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
