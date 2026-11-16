from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from naadi_api.db import Database
from naadi_api.health.checks import (
    TAVILY_USAGE_URL,
    CheckOutcome,
    NotConfigured,
    database_check,
    openrouter_check,
    storage_check,
    tavily_check,
)
from naadi_api.health.models import HealthResponse
from naadi_api.health.service import COMPONENTS, Check, HealthService

from .conftest import FIXTURES, fake_check, fake_service, make_settings

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"


def _fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return data


# ───────────── aggregation over HTTP ─────────────
def test_all_ok_is_200(client_factory: Callable[..., TestClient]) -> None:
    r = client_factory().get("/api/health")
    assert r.status_code == 200
    body = HealthResponse.model_validate(r.json())
    assert body.status == "ok"
    assert set(body.components.model_dump()) == set(COMPONENTS)


def test_database_down_is_503(client_factory: Callable[..., TestClient]) -> None:
    svc = fake_service(database=fake_check(exc=OSError("failed to resolve host")))
    r = client_factory(health=svc).get("/api/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "down"
    assert body["components"]["database"]["status"] == "down"
    assert body["components"]["database"]["detail"] == "OSError: failed to resolve host"
    assert body["components"]["storage"]["status"] == "ok"


@pytest.mark.parametrize("component", ["storage", "openrouter", "tavily"])
def test_other_component_down_is_degraded_200(client_factory: Callable[..., TestClient], component: str) -> None:
    svc = fake_service(**{component: fake_check(exc=RuntimeError("nope"))})
    r = client_factory(health=svc).get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
    assert r.json()["components"][component]["status"] == "down"


def test_degraded_component_is_degraded(client_factory: Callable[..., TestClient]) -> None:
    svc = fake_service(tavily=fake_check("degraded", "Credit guard reached"))
    assert client_factory(health=svc).get("/api/health").json()["status"] == "degraded"


def test_not_configured_reports_names_without_latency(client_factory: Callable[..., TestClient]) -> None:
    svc = fake_service(storage=fake_check(exc=NotConfigured("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")))
    comp = client_factory(health=svc).get("/api/health").json()["components"]["storage"]
    assert comp["status"] == "down"
    assert comp["latency_ms"] is None
    assert comp["detail"] == "Not configured: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set"


def test_secrets_are_scrubbed_from_details(client_factory: Callable[..., TestClient]) -> None:
    secret = "sk-or-v1-0123456789abcdef0123456789"
    exc = RuntimeError(f"auth failed for {secret} and postgresql://user:pw-x9y8z7@host:5432/db Bearer abc.def.ghi")
    svc = HealthService(
        checks={n: Check(n, fake_check(exc=exc) if n == "openrouter" else fake_check(), ttl_s=1) for n in COMPONENTS},
        version="t",
        environment="prod",
        secrets=[secret],
    )
    text = client_factory(health=svc).get("/api/health").text
    for needle in (secret, "pw-x9y8z7", "abc.def.ghi"):
        assert needle not in text


def test_ready_without_database_is_503(client_factory: Callable[..., TestClient]) -> None:
    r = client_factory().get("/api/ready")
    assert r.status_code == 503
    assert r.json() == {"ready": False, "checks": {"settings": True, "database_pool": False}}


# ───────────── cache and concurrency ─────────────
class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _counting(outcome: CheckOutcome, delay: float = 0.0) -> tuple[Any, list[int]]:
    calls: list[int] = []

    async def run() -> CheckOutcome:
        calls.append(1)
        if delay:
            await asyncio.sleep(delay)
        return outcome

    return run, calls


async def test_cache_respects_ttl_and_marks_cached() -> None:
    clock = Clock()
    run, calls = _counting(CheckOutcome("ok", "fine"))
    checks = {n: Check(n, fake_check(), ttl_s=60) for n in COMPONENTS}
    checks["openrouter"] = Check("openrouter", run, ttl_s=60)
    svc = HealthService(checks=checks, version="t", environment="prod", clock=clock)
    first = await svc.component("openrouter")
    second = await svc.component("openrouter")
    assert (first.cached, second.cached) == (False, True)
    assert len(calls) == 1
    clock.t += 61
    third = await svc.component("openrouter")
    assert third.cached is False
    assert len(calls) == 2


async def test_failures_are_cached_briefly() -> None:
    clock = Clock()
    calls: list[int] = []

    async def failing() -> CheckOutcome:
        calls.append(1)
        raise RuntimeError("upstream 500")

    checks = {n: Check(n, fake_check(), ttl_s=300) for n in COMPONENTS}
    checks["tavily"] = Check("tavily", failing, ttl_s=300)
    svc = HealthService(checks=checks, version="t", environment="prod", clock=clock)
    await svc.component("tavily")
    await svc.component("tavily")
    assert len(calls) == 1
    clock.t += 16  # failure TTL is capped at 15 s so recovery shows quickly
    await svc.component("tavily")
    assert len(calls) == 2


async def test_single_flight_under_concurrency() -> None:
    run, calls = _counting(CheckOutcome("ok", "fine"), delay=0.05)
    checks = {n: Check(n, fake_check(), ttl_s=60) for n in COMPONENTS}
    checks["tavily"] = Check("tavily", run, ttl_s=300)
    svc = HealthService(checks=checks, version="t", environment="prod")
    results = await asyncio.gather(*(svc.health() for _ in range(25)))
    assert len(calls) == 1
    assert all(r.components.tavily.status == "ok" for r in results)


async def test_timeout_becomes_down() -> None:
    async def slow() -> CheckOutcome:
        await asyncio.sleep(5)
        return CheckOutcome("ok", "late")

    checks = {n: Check(n, fake_check(), ttl_s=60) for n in COMPONENTS}
    checks["storage"] = Check("storage", slow, ttl_s=60, timeout_s=0.05)
    svc = HealthService(checks=checks, version="t", environment="prod")
    comp = await svc.component("storage")
    assert comp.status == "down"
    assert comp.detail == "Timed out after 0.05 s"


def test_service_requires_all_components() -> None:
    with pytest.raises(ValueError, match="missing health checks"):
        HealthService(checks={"database": Check("database", fake_check(), 1)}, version="t", environment="prod")


# ───────────── individual checks (recorded fixtures) ─────────────
@respx.mock
async def test_openrouter_free_tier_fixture() -> None:
    respx.get(OPENROUTER_KEY_URL).mock(return_value=httpx.Response(200, json=_fixture("openrouter_key_free_tier.json")))
    s = make_settings(openrouter_api_key="sk-or-v1-test", openrouter_daily_cap=50)
    out = await openrouter_check(s)()
    assert out.status == "ok"
    assert out.free_tier is True
    assert out.quota is not None
    assert (out.quota.unit, out.quota.period, out.quota.guard) == ("requests", "day", 50)
    assert "free-model requests left today" in out.detail
    sent = respx.calls.last.request
    assert sent.headers["authorization"] == "Bearer sk-or-v1-test"


@respx.mock
async def test_openrouter_quota_exhausted_is_degraded() -> None:
    body = copy.deepcopy(_fixture("openrouter_key_free_tier.json"))
    body["data"]["free_model_daily_requests"] = {"limit": 50, "remaining": 0, "used": 50}
    respx.get(OPENROUTER_KEY_URL).mock(return_value=httpx.Response(200, json=body))
    out = await openrouter_check(make_settings(openrouter_api_key="k-123456"))()
    assert out.status == "degraded"
    assert "resets at 00:00 UTC" in out.detail


@respx.mock
async def test_openrouter_cap_mismatch_is_degraded() -> None:
    respx.get(OPENROUTER_KEY_URL).mock(return_value=httpx.Response(200, json=_fixture("openrouter_key_free_tier.json")))
    out = await openrouter_check(make_settings(openrouter_api_key="k-123456", openrouter_daily_cap=1000))()
    assert out.status == "degraded"
    assert "OPENROUTER_DAILY_CAP" in out.detail


@respx.mock
async def test_openrouter_bad_key_raises() -> None:
    respx.get(OPENROUTER_KEY_URL).mock(return_value=httpx.Response(401, json={"error": {"message": "No auth"}}))
    with pytest.raises(Exception):  # noqa: B017,PT011 - the SDK's error type; the service turns it into `down`
        await openrouter_check(make_settings(openrouter_api_key="k-123456"))()


async def test_openrouter_not_configured() -> None:
    with pytest.raises(NotConfigured, match="OPENROUTER_API_KEY"):
        await openrouter_check(make_settings())()


def _tavily(status: int, body: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == TAVILY_USAGE_URL
        assert request.method == "GET"  # never /search: health must not spend a credit
        assert request.headers["authorization"] == "Bearer tvly-test-key"
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


async def test_tavily_fixture_ok() -> None:
    s = make_settings(tavily_api_key="tvly-test-key")
    out = await tavily_check(s, 950, transport=_tavily(200, _fixture("tavily_usage.json")))()
    assert out.status == "ok"
    assert out.quota is not None
    assert (out.quota.limit, out.quota.guard, out.quota.unit, out.quota.period) == (1000, 950, "credits", "month")


async def test_tavily_guard_reached_is_degraded() -> None:
    body = copy.deepcopy(_fixture("tavily_usage.json"))
    body["account"]["plan_usage"] = 951
    out = await tavily_check(make_settings(tavily_api_key="tvly-test-key"), 950, transport=_tavily(200, body))()
    assert out.status == "degraded"
    assert out.quota is not None and out.quota.used == 951 and out.quota.remaining == 49


async def test_tavily_rejected_key_is_down() -> None:
    out = await tavily_check(make_settings(tavily_api_key="tvly-test-key"), 950, transport=_tavily(401, {}))()
    assert out.status == "down"
    assert out.detail == "API key rejected (HTTP 401)"


async def test_tavily_key_only_payload() -> None:
    body = {"key": {"usage": 12, "limit": 500}}
    out = await tavily_check(make_settings(tavily_api_key="tvly-test-key"), 950, transport=_tavily(200, body))()
    assert out.quota is not None
    assert (out.quota.used, out.quota.limit, out.quota.guard) == (12, 500, 500)


async def test_storage_not_configured() -> None:
    with pytest.raises(NotConfigured, match="SUPABASE_SERVICE_ROLE_KEY"):
        await storage_check(make_settings(supabase_url="https://abcdefghijklmnopqrst.supabase.co"))()


async def test_database_not_configured() -> None:
    with pytest.raises(NotConfigured, match="DATABASE_URL"):
        await database_check(Database(None))()


async def test_database_unreachable_fails_fast_without_leaking_password() -> None:
    db = Database(SecretStr("postgresql://user:pw-should-not-leak@127.0.0.1:1/postgres?connect_timeout=1"))
    await db.open()
    try:
        with pytest.raises(Exception) as ei:  # noqa: PT011 - PoolTimeout or OperationalError
            await db.ping(deadline_s=1.0)
        assert "pw-should-not-leak" not in str(ei.value)
    finally:
        await db.close()


def test_models_yaml_guard_is_used(tmp_path: Path) -> None:
    from naadi_api.config import ConfigError, load_models_config

    from .conftest import REPO_ROOT

    assert load_models_config(REPO_ROOT / "config").search.monthly_credit_guard == 950
    with pytest.raises(ConfigError, match=r"models\.yaml"):
        load_models_config(tmp_path)
