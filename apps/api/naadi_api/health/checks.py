"""The four live checks behind /api/health. None of them spends quota:

- database:   `select 1` on the pool
- storage:    supabase SDK `storage.list_buckets()` with the service role
- openrouter: SDK `api_keys.get_current_key_metadata()` (key metadata is free; no completion is made)
- tavily:     GET https://api.tavily.com/usage (free; reports credits used this month)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx
from openrouter import OpenRouter
from supabase import AsyncClient, acreate_client

from ..db import Database
from ..settings import Settings
from .models import Quota, Status

TAVILY_USAGE_URL = "https://api.tavily.com/usage"


@dataclass(frozen=True)
class CheckOutcome:
    status: Status
    detail: str
    quota: Quota | None = None
    free_tier: bool | None = None


CheckFn = Callable[[], Awaitable[CheckOutcome]]


class NotConfigured(Exception):
    """Raised when the settings a check needs are absent; reported as `down` with the variable names."""


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
        return CheckOutcome("ok", f"{used} of {guard} credits used this month (guard)", quota)

    return run
