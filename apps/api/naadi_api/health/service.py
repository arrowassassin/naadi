"""Runs the health checks concurrently, with per-check timeouts and a single-flight TTL cache.

The cache means the public /api/health endpoint cannot be used to burn upstream quotas: however many
requests arrive, each external check runs at most once per TTL per process.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..logs import get_logger
from ..scrub import scrub
from ..settings import AppEnv
from .checks import CheckFn, CheckOutcome, NotConfigured
from .models import ComponentHealth, HealthComponents, HealthResponse, Status

log = get_logger("naadi.health")

COMPONENTS = ("database", "storage", "openrouter", "tavily")
FAILURE_TTL_CAP_S = 15.0


@dataclass
class Check:
    name: str
    run: CheckFn
    ttl_s: float
    timeout_s: float = 6.0


@dataclass
class _Entry:
    value: ComponentHealth
    expires: float


@dataclass
class HealthService:
    checks: dict[str, Check]
    version: str
    environment: AppEnv
    secrets: Sequence[str] = ()
    clock: Callable[[], float] = time.monotonic
    _cache: dict[str, _Entry] = field(default_factory=dict)
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def __post_init__(self) -> None:
        missing = set(COMPONENTS) - set(self.checks)
        if missing:
            raise ValueError(f"missing health checks: {sorted(missing)}")

    async def _execute(self, check: Check) -> ComponentHealth:
        started = time.perf_counter()
        latency: float | None
        try:
            async with asyncio.timeout(check.timeout_s):
                outcome = await check.run()
            latency = round((time.perf_counter() - started) * 1000, 1)
        except NotConfigured as exc:
            outcome, latency = CheckOutcome("down", f"Not configured: {exc}"), None
        except TimeoutError:
            outcome = CheckOutcome("down", f"Timed out after {check.timeout_s:g} s")
            latency = round((time.perf_counter() - started) * 1000, 1)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a `down` component
            msg = str(exc) or type(exc).__name__
            outcome = CheckOutcome("down", f"{type(exc).__name__}: {msg}")
            latency = round((time.perf_counter() - started) * 1000, 1)
            log.warning("health_check_failed", component=check.name, error=scrub(outcome.detail, self.secrets))
        return ComponentHealth(
            status=outcome.status,
            latency_ms=latency,
            detail=scrub(outcome.detail, self.secrets, max_len=240),
            checked_at=datetime.now(UTC),
            cached=False,
            quota=outcome.quota,
            free_tier=outcome.free_tier,
        )

    async def component(self, name: str) -> ComponentHealth:
        check = self.checks[name]
        entry = self._cache.get(name)
        if entry and entry.expires > self.clock():
            return entry.value.model_copy(update={"cached": True})
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            entry = self._cache.get(name)
            if entry and entry.expires > self.clock():
                return entry.value.model_copy(update={"cached": True})
            value = await self._execute(check)
            ttl = check.ttl_s if value.status != "down" else min(check.ttl_s, FAILURE_TTL_CAP_S)
            self._cache[name] = _Entry(value, self.clock() + ttl)
            return value

    async def health(self) -> HealthResponse:
        results = await asyncio.gather(*(self.component(n) for n in COMPONENTS))
        comps = dict(zip(COMPONENTS, results, strict=True))
        return HealthResponse(
            status=overall_status(comps),
            version=self.version,
            environment=self.environment,
            checked_at=datetime.now(UTC),
            components=HealthComponents(**comps),
        )


def overall_status(comps: dict[str, ComponentHealth]) -> Status:
    if comps["database"].status == "down":
        return "down"
    if any(c.status != "ok" for c in comps.values()):
        return "degraded"
    return "ok"
