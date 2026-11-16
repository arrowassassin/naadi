"""Budget guards for model calls: per-provider rpm buckets, daily caps with the chat/job split, the per-turn
stop, and the `llm_usage` ledger they are counted from.

- **Rate:** one token bucket per provider, process-wide and async-safe (the Space runs a single worker
  process, which the in-memory Aegra broker requires anyway). Capacity = rpm, refilled continuously.
- **Daily cap:** counted from `public.llm_usage` rows since 00:00 UTC (every attempt is a row, written
  *before* the request as a reservation and completed afterwards), so restarts and several processes
  share one count. Reservation takes a transaction-level advisory lock per provider, so two concurrent
  calls can never both take the last slot. Chat may use `reserve_for_chat` of each provider's cap, jobs
  the rest.
- **Per turn:** a counter per run id; the tool loop stops at `max_llm_calls_per_turn`.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from .db import Database
from .llm_config import AgentConfig, ProviderName, Purpose

BudgetReason = Literal["daily_cap", "rate_limited", "turn_limit"]


def next_utc_midnight(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


class BudgetExhausted(RuntimeError):
    """A guard refused the call. Graphs degrade to course retrieval and the UI shows `reset_at`."""

    def __init__(self, reason: BudgetReason, reset_at: datetime, *, purpose: Purpose | None = None) -> None:
        super().__init__(f"{reason}; resets at {reset_at.isoformat()}")
        self.reason: BudgetReason = reason
        self.reset_at = reset_at
        self.purpose = purpose

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "reset_at": self.reset_at.isoformat(), "purpose": self.purpose}


class TurnLimitReached(BudgetExhausted):
    def __init__(self, limit: int) -> None:
        super().__init__("turn_limit", datetime.now(UTC))
        self.limit = limit


class TokenBucket:
    """`rate_per_min` tokens per minute, burst = `rate_per_min`. Never blocks; callers decide to wait."""

    def __init__(self, rate_per_min: int, *, clock: Any = time.monotonic) -> None:
        self.rate_per_min = rate_per_min
        self._capacity = float(rate_per_min)
        self._tokens = float(rate_per_min)
        self._per_s = rate_per_min / 60.0
        self._clock = clock
        self._t = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._t) * self._per_s)
        self._t = now

    async def try_take(self) -> bool:
        async with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    async def refund(self) -> None:
        async with self._lock:
            self._refill()
            self._tokens = min(self._capacity, self._tokens + 1.0)

    def wait_s(self) -> float:
        """Seconds until one token is available (0 when one is available now)."""
        self._refill()
        return 0.0 if self._tokens >= 1.0 else (1.0 - self._tokens) / self._per_s


class RateBuckets:
    def __init__(self, *, clock: Any = time.monotonic) -> None:
        self._clock = clock
        self._buckets: dict[str, TokenBucket] = {}

    def get(self, provider: ProviderName, rpm: int) -> TokenBucket:
        b = self._buckets.get(provider)
        if b is None or b.rate_per_min != rpm:  # rpm edited in app_config: start a fresh bucket
            b = TokenBucket(rpm, clock=self._clock)
            self._buckets[provider] = b
        return b


@dataclass(frozen=True)
class UsageRow:
    provider: ProviderName
    purpose: Purpose
    role: str
    model: str
    user_id: uuid.UUID | None
    run_id: str | None


class UsageLedger(Protocol):
    async def reserve(self, row: UsageRow, cap: int) -> int | None:
        """Insert an in-flight row if today's count for (provider, purpose) is below `cap`; else None."""

    async def finish(
        self,
        usage_id: int,
        *,
        ok: bool,
        status: int | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        latency_ms: int,
    ) -> None: ...

    async def count_today(self, provider: ProviderName, purpose: Purpose | None = None) -> int: ...


def _lock_key(provider: str) -> int:
    return int.from_bytes(hashlib.sha256(f"naadi.llm_usage.{provider}".encode()).digest()[:8], "big", signed=True)


_TODAY = "created_at >= (date_trunc('day', now() at time zone 'utc') at time zone 'utc')"


class PgUsageLedger:
    """`public.llm_usage` through the service connection (the table is closed to clients)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def _pool(self) -> Any:
        if self._db.pool is None:
            await self._db.open()
        if self._db.pool is None:
            raise RuntimeError("DATABASE_URL is not set: the LLM budget ledger needs the database")
        return self._db.pool

    async def reserve(self, row: UsageRow, cap: int) -> int | None:
        if cap <= 0:
            return None
        pool = await self._pool()
        async with pool.connection(timeout=5.0) as conn, asyncio.timeout(5.0), conn.transaction():
            await conn.execute("select pg_advisory_xact_lock(%s)", (_lock_key(row.provider),))
            cur = await conn.execute(
                f"select count(*) from public.llm_usage where provider = %s and purpose = %s and {_TODAY}",  # noqa: S608 - constant fragment
                (row.provider, row.purpose),
            )
            got = await cur.fetchone()
            if got is None or int(got[0]) >= cap:
                return None
            cur = await conn.execute(
                "insert into public.llm_usage (user_id, provider, purpose, role, model, run_id)"
                " values (%s, %s, %s, %s, %s, %s) returning id",
                (row.user_id, row.provider, row.purpose, row.role, row.model, row.run_id),
            )
            new = await cur.fetchone()
            return int(new[0]) if new else None

    async def finish(
        self,
        usage_id: int,
        *,
        ok: bool,
        status: int | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        latency_ms: int,
    ) -> None:
        pool = await self._pool()
        async with pool.connection(timeout=5.0) as conn, asyncio.timeout(5.0):
            await conn.execute(
                "update public.llm_usage set ok = %s, status = %s, prompt_tokens = %s, completion_tokens = %s,"
                " latency_ms = %s where id = %s",
                (ok, status, prompt_tokens, completion_tokens, max(latency_ms, 0), usage_id),
            )

    async def count_today(self, provider: ProviderName, purpose: Purpose | None = None) -> int:
        pool = await self._pool()
        async with pool.connection(timeout=5.0) as conn, asyncio.timeout(5.0):
            if purpose is None:
                cur = await conn.execute(
                    f"select count(*) from public.llm_usage where provider = %s and {_TODAY}",  # noqa: S608
                    (provider,),
                )
            else:
                cur = await conn.execute(
                    f"select count(*) from public.llm_usage where provider = %s and purpose = %s and {_TODAY}",  # noqa: S608
                    (provider, purpose),
                )
            got = await cur.fetchone()
        return int(got[0]) if got else 0


def provider_daily_cap(cfg: AgentConfig, provider: ProviderName, env_openrouter_cap: int | None) -> int:
    """The provider's whole-day cap. OpenRouter: the lowest of providers.openrouter.daily_cap,
    budget.daily_request_cap and OPENROUTER_DAILY_CAP (the account-wide free limit)."""
    spec = cfg.models.providers[provider]
    caps = [c for c in (spec.daily_cap,) if c is not None]
    if provider == "openrouter":
        caps.append(cfg.budget.daily_request_cap)
        if env_openrouter_cap is not None:
            caps.append(env_openrouter_cap)
    elif not caps:
        caps.append(cfg.budget.daily_request_cap)
    return max(0, min(caps))


def purpose_cap(cfg: AgentConfig, provider: ProviderName, purpose: Purpose, env_openrouter_cap: int | None) -> int:
    return int(provider_daily_cap(cfg, provider, env_openrouter_cap) * cfg.budget.share(purpose))


class TurnCounter:
    def __init__(self) -> None:
        self.calls = 0

    def take(self, limit: int) -> None:
        if self.calls >= limit:
            raise TurnLimitReached(limit)
        self.calls += 1


class TurnRegistry:
    """One `TurnCounter` per run id (a run is one student turn). Bounded LRU so it never grows unbounded."""

    def __init__(self, max_runs: int = 2048) -> None:
        self._max = max_runs
        self._runs: OrderedDict[str, TurnCounter] = OrderedDict()

    def for_run(self, run_id: str) -> TurnCounter:
        c = self._runs.get(run_id)
        if c is None:
            c = TurnCounter()
            self._runs[run_id] = c
            while len(self._runs) > self._max:
                self._runs.popitem(last=False)
        else:
            self._runs.move_to_end(run_id)
        return c

    def end(self, run_id: str) -> None:
        self._runs.pop(run_id, None)


__all__ = [
    "BudgetExhausted",
    "BudgetReason",
    "PgUsageLedger",
    "RateBuckets",
    "TokenBucket",
    "TurnCounter",
    "TurnLimitReached",
    "TurnRegistry",
    "UsageLedger",
    "UsageRow",
    "next_utc_midnight",
    "provider_daily_cap",
    "purpose_cap",
]
