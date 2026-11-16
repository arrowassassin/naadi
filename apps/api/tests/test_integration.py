"""Integration tests against the real services (Supabase, OpenRouter, NVIDIA, Tavily).

`-m "integration and not db"` runs where raw Postgres is unreachable (the build sandbox);
CI runs `-m integration`, which includes the `db` tests. Missing configuration fails the test loudly.
None of these spend paid quota: OpenRouter key metadata and Tavily usage are free endpoints; the NVIDIA
probe is one free one-token call per configured model.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from naadi_api.config import load_models_config
from naadi_api.db import Database
from naadi_api.health.checks import database_check, nvidia_check, openrouter_check, storage_check, tavily_check
from naadi_api.main import create_app
from naadi_api.settings import Settings

from .conftest import REPO_ROOT, require_env

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


async def test_tavily_usage_without_spending() -> None:
    require_env("TAVILY_API_KEY")
    s = _settings()
    first = await tavily_check(s, 950)()
    second = await tavily_check(s, 950)()
    assert first.status in ("ok", "degraded")
    assert first.quota is not None and second.quota is not None
    assert second.quota.used == first.quota.used  # checking usage must not cost a credit


async def test_nvidia_probes_every_configured_model() -> None:
    # In the build sandbox the network proxy injects the real key; NVIDIA_API_KEY holds a placeholder there.
    require_env("NVIDIA_API_KEY")
    s = _settings()
    models = load_models_config(s.config_dir).models_for("nvidia")
    out = await nvidia_check(s, models)()
    assert out.models is not None
    assert [m.model for m in out.models] == models
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
