"""The four live checks behind /api/health. None of them spends quota:

- database:   `select 1` on the pool
- storage:    supabase SDK `storage.list_buckets()` with the service role
- openrouter: SDK `api_keys.get_current_key_metadata()` (key metadata is free; no completion is made)
- nvidia:     one-token probe of each NVIDIA model in the role chains (`naadi_api.llm.probe_nvidia`); the
              service caches it for an hour so the public endpoint cannot burn the NVIDIA quota
- tavily:     the stored daily reading (agent_health kind tavily_usage, written by the agent system cron
              from Tavily's free usage endpoint) and our own search_usage ledger; never calls Tavily
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from openrouter import OpenRouter
from pydantic import SecretStr
from supabase import AsyncClient, acreate_client

from ..db import Database
from ..llm import ProbeResult, probe_nvidia
from ..settings import Settings
from .models import AgentRuntimeFacts, GenerationFacts, ModelProbe, Quota, Status

TAVILY_USAGE_URL = "https://api.tavily.com/usage"


@dataclass(frozen=True)
class CheckOutcome:
    status: Status
    detail: str
    quota: Quota | None = None
    free_tier: bool | None = None
    models: list[ModelProbe] | None = None
    agent: AgentRuntimeFacts | None = None
    generation: GenerationFacts | None = None


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


@dataclass(frozen=True)
class TavilyReading:
    """The latest `agent_health` row of kind `tavily_usage` (the daily system cron's reading of Tavily's usage
    endpoint), and our own `search_usage` ledger for the current UTC month."""

    reading_ok: bool | None  # None: no reading recorded yet
    detail: dict[str, Any]
    read_at: datetime | None
    ledger_month_credits: int


TavilyReader = Callable[[], Awaitable[TavilyReading]]
TAVILY_READING_STALE_AFTER = timedelta(hours=48)
_TAVILY_READING_SQL = (
    "select ok, detail, created_at from public.agent_health where kind = 'tavily_usage'"
    " order by created_at desc limit 1"
)
_TAVILY_LEDGER_SQL = (
    "select coalesce(sum(credits), 0) from public.search_usage where source = 'tavily'"
    " and created_at >= (date_trunc('month', now() at time zone 'utc') at time zone 'utc')"
)


def tavily_db_reader(db: Database) -> TavilyReader:
    async def read() -> TavilyReading:
        if not db.configured:
            raise NotConfigured("DATABASE_URL not set (the Tavily usage reading is stored there)")
        if db.pool is None:
            await db.open()
        assert db.pool is not None
        async with db.pool.connection(timeout=5.0) as conn:
            cur = await conn.execute(_TAVILY_READING_SQL)
            row = await cur.fetchone()
            cur = await conn.execute(_TAVILY_LEDGER_SQL)
            ledger = await cur.fetchone()
        credits = int(ledger[0]) if ledger else 0
        if row is None:
            return TavilyReading(None, {}, None, credits)
        return TavilyReading(bool(row[0]), dict(row[1] or {}), row[2], credits)

    return read


def tavily_check(
    settings: Settings,
    monthly_credit_guard: int,
    reader: TavilyReader,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CheckFn:
    """Tavily's state from what is already stored, never a call to Tavily: the daily `tavily_usage` reading
    (agent system cron, naadi_api/agent/graphs/tavily_usage.py) and our own ledger, which is always current.
    Used credits are the larger of the two, so the guard holds between readings."""

    async def run() -> CheckOutcome:
        if settings.tavily_api_key is None:
            raise NotConfigured("TAVILY_API_KEY not set")
        r = await reader()
        now = clock()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        d = r.detail
        limit = _int_or_none(d.get("tavily_limit")) if r.reading_ok else None
        this_month = r.read_at is not None and r.read_at >= month_start
        reported = _int_or_none(d.get("tavily_used")) if (r.reading_ok and this_month) else None
        used = max(reported or 0, r.ledger_month_credits)
        guard = min(monthly_credit_guard, limit) if limit is not None else monthly_credit_guard
        quota = Quota(
            used=used,
            limit=limit,
            remaining=(limit - used) if limit is not None else None,
            unit="credits",
            period="month",
            guard=guard,
        )
        outcome = str(d.get("outcome") or "")
        if r.reading_ok is False and outcome in ("http_401", "http_403"):
            return CheckOutcome("down", f"API key rejected (HTTP {outcome[5:]}) at the last usage reading", quota)
        if used >= guard:
            return CheckOutcome("degraded", f"Credit guard reached ({used} of {guard}); web search is paused", quota)
        if r.reading_ok is None:
            return CheckOutcome(
                "ok", f"Key set; no usage reading yet, our ledger shows {used} credits this month", quota
            )
        assert r.read_at is not None
        when = r.read_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        if now - r.read_at > TAVILY_READING_STALE_AFTER:
            return CheckOutcome("degraded", f"Last usage reading is from {when}; the daily check is not running", quota)
        if r.reading_ok is False:
            return CheckOutcome("degraded", f"Last usage reading failed ({outcome or 'unknown'}) at {when}", quota)
        return CheckOutcome("ok", f"Key valid; web search available (usage read {when})", quota)

    return run


def nvidia_check(
    settings: Settings, models: Sequence[str], probe: NvidiaProbe | None = None, timeout_s: float = 30.0
) -> CheckFn:
    """Probe every NVIDIA model the role chains use. NVIDIA is optional (OpenRouter chains back it up),
    so a missing key is `degraded`; some models failing is `degraded`; all failing is `down`."""

    async def run() -> CheckOutcome:
        if not models:
            return CheckOutcome("ok", "No role chain routes to NVIDIA", models=[])
        key = settings.nvidia_api_key
        if key is None:
            raise NotConfigured("NVIDIA_API_KEY is not set on this deployment", status="degraded")
        call: NvidiaProbe = probe or (lambda m, k: probe_nvidia(m, k, timeout_s=timeout_s))
        results = await asyncio.gather(*(call(m, key) for m in models))
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


def agent_runtime_check(db: Database | None) -> CheckFn:
    """Aegra's live objects in this process plus the age of the last hourly system ping (agent_health)."""

    async def run() -> CheckOutcome:
        from ..agent.health import runtime_status

        st = await runtime_status(db)
        facts = AgentRuntimeFacts(
            reason=st.reason,
            graphs=st.graphs,
            failing=[k for k, v in st.checks.items() if not v],
            last_cron_ping=st.last_cron_ping,
        )
        return CheckOutcome(st.status, st.detail, agent=facts)

    return run
