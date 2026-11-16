"""The four live checks behind /api/health. None of them spends quota:

- database:   `select 1` on the pool
- storage:    supabase SDK `storage.list_buckets()` with the service role
- openrouter: SDK `api_keys.get_current_key_metadata()` (key metadata is free; no completion is made)
- nvidia:     one-token probe of each NVIDIA model in the role chains (`naadi_api.llm.probe_nvidia`); the
              service caches it for an hour so the public endpoint cannot burn the NVIDIA quota
- tavily:     GET https://api.tavily.com/usage (free; reports credits used this month)
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import httpx
from openrouter import OpenRouter
from pydantic import SecretStr
from supabase import AsyncClient, acreate_client

from ..db import Database
from ..llm import ProbeResult, probe_nvidia
from ..settings import Settings
from .models import ModelProbe, Quota, Status

TAVILY_USAGE_URL = "https://api.tavily.com/usage"


@dataclass(frozen=True)
class CheckOutcome:
    status: Status
    detail: str
    quota: Quota | None = None
    free_tier: bool | None = None
    models: list[ModelProbe] | None = None


CheckFn = Callable[[], Awaitable[CheckOutcome]]
NvidiaProbe = Callable[[str, SecretStr], Awaitable[ProbeResult]]


class NotConfigured(Exception):
    """Raised when the settings a check needs are absent; reported with the variable names.

    `status` is `down` for components the app cannot work without, `degraded` for optional ones.
    """

    def __init__(self, message: str, status: Status = "down") -> None:
        super().__init__(message)
        self.status: Status = status


def _int_or_none(v: object) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v)
    return None


def database_check(db: Database) -> CheckFn:
    async def run() -> CheckOutcome:
        if not db.configured:
            raise NotConfigured("DATABASE_URL is not set")
        await db.ping()
        return CheckOutcome("ok", "select 1 succeeded")

    return run


def storage_check(settings: Settings) -> CheckFn:
    client: AsyncClient | None = None

    async def run() -> CheckOutcome:
        nonlocal client
        if not settings.supabase_url or settings.supabase_service_role_key is None:
            raise NotConfigured("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")
        if client is None:
            client = await acreate_client(settings.supabase_url, settings.supabase_service_role_key.get_secret_value())
        buckets = await client.storage.list_buckets()
        n = len(buckets)
        return CheckOutcome("ok", f"Storage reachable; {n} bucket{'s' if n != 1 else ''}")

    return run


def openrouter_check(settings: Settings) -> CheckFn:
    async def run() -> CheckOutcome:
        if settings.openrouter_api_key is None:
            raise NotConfigured("OPENROUTER_API_KEY not set")
        async with OpenRouter(
            api_key=settings.openrouter_api_key.get_secret_value(),
            http_referer=settings.openrouter_app_url,
            x_open_router_title=settings.openrouter_app_title,
            timeout_ms=5000,
        ) as client:
            res = await client.api_keys.get_current_key_metadata_async()
        data = res.data
        free = data.free_model_daily_requests
        quota: Quota | None = None
        status: Status = "ok"
        if free is not None:
            limit = int(free.limit) if free.limit is not None else None
            remaining = int(free.remaining) if free.remaining is not None else None
            guard = min(settings.openrouter_daily_cap, limit) if limit is not None else settings.openrouter_daily_cap
            used = int(free.used or 0)
            quota = Quota(used=used, limit=limit, remaining=remaining, unit="requests", period="day", guard=guard)
            if (remaining is not None and remaining <= 0) or used >= guard:
                status = "degraded"
                detail = "Free-model quota for today is used up; it resets at 00:00 UTC"
            else:
                left = remaining if remaining is not None else guard - used
                detail = f"Key valid; {left} free-model requests left today"
        else:
            detail = "Key valid; free-model quota not reported"
        if data.is_free_tier and settings.openrouter_daily_cap > 50:
            status = "degraded"
            detail = "OPENROUTER_DAILY_CAP is above 50 but the account has no credit purchase (real cap 50/day)"
        return CheckOutcome(status, detail, quota=quota, free_tier=bool(data.is_free_tier))

    return run


def tavily_check(
    settings: Settings, monthly_credit_guard: int, transport: httpx.AsyncBaseTransport | None = None
) -> CheckFn:
    async def run() -> CheckOutcome:
        if settings.tavily_api_key is None:
            raise NotConfigured("TAVILY_API_KEY not set")
        # tavily-python has no usage method; this documented endpoint is free (spends no credit).
        async with httpx.AsyncClient(timeout=5.0, transport=transport) as http:
            r = await http.get(
                TAVILY_USAGE_URL, headers={"Authorization": f"Bearer {settings.tavily_api_key.get_secret_value()}"}
            )
        if r.status_code in (401, 403):
            return CheckOutcome("down", f"API key rejected (HTTP {r.status_code})")
        if r.status_code == 429:
            # The key was accepted but the usage endpoint is throttled: credits are unknown, search still works.
            return CheckOutcome("degraded", "Usage endpoint rate-limited (HTTP 429); credit count unknown for now")
        if r.status_code != 200:
            return CheckOutcome("down", f"Usage endpoint returned HTTP {r.status_code}")
        body = r.json()
        account = body.get("account") or {}
        key = body.get("key") or {}
        used = _int_or_none(account.get("plan_usage"))
        if used is None:
            used = _int_or_none(key.get("usage")) or 0
        limit = _int_or_none(account.get("plan_limit"))
        if limit is None:
            limit = _int_or_none(key.get("limit"))
        guard = min(monthly_credit_guard, limit) if limit is not None else monthly_credit_guard
        quota = Quota(
            used=used,
            limit=limit,
            remaining=(limit - used) if limit is not None else None,
            unit="credits",
            period="month",
            guard=guard,
        )
        if used >= guard:
            return CheckOutcome("degraded", f"Credit guard reached ({used} of {guard}); web search is paused", quota)
        return CheckOutcome("ok", "Key valid; web search available", quota)

    return run


def nvidia_check(settings: Settings, models: Sequence[str], probe: NvidiaProbe = probe_nvidia) -> CheckFn:
    """Probe every NVIDIA model the role chains use. NVIDIA is optional (OpenRouter chains back it up),
    so a missing key is `degraded`; some models failing is `degraded`; all failing is `down`."""

    async def run() -> CheckOutcome:
        if not models:
            return CheckOutcome("ok", "No role chain routes to NVIDIA", models=[])
        key = settings.nvidia_api_key
        if key is None:
            raise NotConfigured("NVIDIA_API_KEY is not set on this deployment", status="degraded")
        results = await asyncio.gather(*(probe(m, key) for m in models))
        probes = [
            ModelProbe(provider=r.provider, model=r.model, ok=r.ok, latency_ms=r.latency_ms, detail=r.detail[:80])
            for r in results
        ]
        failing = [p for p in probes if not p.ok]
        n, ok = len(probes), len(probes) - len(failing)
        if not failing:
            return CheckOutcome("ok", f"All {n} configured model{'s' if n != 1 else ''} answer", models=probes)
        listing = "; ".join(f"{p.model} {p.detail}" for p in failing)
        status: Status = "down" if ok == 0 else "degraded"
        return CheckOutcome(status, f"{ok} of {n} configured models answer. Failing: {listing}", models=probes)

    return run
