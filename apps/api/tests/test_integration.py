"""Integration tests against the real services (Supabase, OpenRouter, NVIDIA, Tavily).

All of them are `live` (real services, real secrets). The database checks that used to live here run
against a LOCAL database in tests/test_database_local.py (`db`). Missing configuration fails loudly.
None of these spend paid quota: OpenRouter key metadata is a free endpoint (Tavily is never called from
/api/health: see tests/agent/test_tavily_health_db.py); the NVIDIA
probe is one free one-token call per configured model.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from naadi_api.config import load_models_config
from naadi_api.health.checks import (
    nvidia_check,
    openrouter_check,
    storage_check,
)
from naadi_api.main import create_app
from naadi_api.settings import Settings

from .conftest import REPO_ROOT, require_env

# Real external services and the real project's database: `live` only (never in the unit or db jobs).
pytestmark = [pytest.mark.integration, pytest.mark.live]


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
