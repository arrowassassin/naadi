"""Agent runtime health: the `agent_health` table (ping results) and the `agent_runtime` status.

`agent_health` is server-side only (RLS forced, no client privileges). The hourly system cron writes one
row per ping; `/api/health` reports `agent_runtime` from Aegra's live objects plus the age of the last
cron ping.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from ..db import Database
from ..logs import get_logger

log = get_logger("naadi.agent.health")

Trigger = Literal["interactive", "background", "cron"]
CRON_STALE_AFTER = timedelta(hours=2)


async def record_ping(
    db: Database,
    *,
    trigger: Trigger,
    ok: bool,
    run_id: str | None,
    provider: str | None,
    model: str | None,
    latency_ms: int | None,
    detail: dict[str, Any],
) -> int:
    if db.pool is None:
        await db.open()
    if db.pool is None:
        raise RuntimeError("DATABASE_URL is not set")
    async with db.pool.connection(timeout=5.0) as conn, asyncio.timeout(5.0):
        cur = await conn.execute(
            "insert into public.agent_health (kind, trigger, ok, run_id, provider, model, latency_ms, detail)"
            " values ('ping', %s, %s, %s, %s, %s, %s, %s::jsonb) returning id",
            (trigger, ok, run_id, provider, model, latency_ms, json.dumps(detail)),
        )
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def record_health(
    db: Database,
    *,
    kind: str,
    trigger: Trigger,
    ok: bool,
    run_id: str | None,
    detail: dict[str, Any],
) -> int:
    """One `agent_health` row of another kind (e.g. the daily `tavily_usage` reading)."""
    if db.pool is None:
        await db.open()
    if db.pool is None:
        raise RuntimeError("DATABASE_URL is not set")
    async with db.pool.connection(timeout=5.0) as conn, asyncio.timeout(5.0):
        cur = await conn.execute(
            "insert into public.agent_health (kind, trigger, ok, run_id, detail) values (%s, %s, %s, %s, %s::jsonb)"
            " returning id",
            (kind, trigger, ok, run_id, json.dumps(detail)),
        )
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def last_ping(db: Database, trigger: Trigger | None = None) -> dict[str, Any] | None:
    if db.pool is None:
        await db.open()
    if db.pool is None:
        return None
    async with db.pool.connection(timeout=5.0) as conn, asyncio.timeout(5.0):
        cur = await conn.execute(
            "select trigger, ok, provider, model, latency_ms, created_at from public.agent_health"
            " where kind = 'ping' and (%s::text is null or trigger = %s) order by created_at desc limit 1",
            (trigger, trigger),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    keys = ("trigger", "ok", "provider", "model", "latency_ms", "created_at")
    return dict(zip(keys, row, strict=True))


@dataclass
class RuntimeStatus:
    status: Literal["ok", "degraded", "down"]
    detail: str
    graphs: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    last_cron_ping: datetime | None = None


async def runtime_status(db: Database | None) -> RuntimeStatus:
    """Inspect the Aegra objects of this process. `down` when not hosted by Aegra (plain uvicorn)."""
    try:
        from aegra_api.core.database import db_manager
        from aegra_api.services.broker import broker_manager
        from aegra_api.services.cron_scheduler import cron_scheduler
        from aegra_api.services.langgraph_service import get_langgraph_service
    except ImportError:
        return RuntimeStatus("down", "Aegra is not installed")
    from . import host

    if not host.HOSTED:
        return RuntimeStatus("down", "The agent runtime is not running in this process")
    checks: dict[str, bool] = {}
    graphs = sorted(get_langgraph_service().list_graphs())
    checks["graphs_loaded"] = "ping" in graphs
    try:
        engine = db_manager.get_engine()
        from sqlalchemy import text

        started = time.perf_counter()
        async with asyncio.timeout(4.0), engine.connect() as conn:
            await conn.execute(text("select 1"))
        checks["database"] = True
        log.debug("agent_runtime.db_ping", ms=round((time.perf_counter() - started) * 1000, 1))
    except Exception:  # noqa: BLE001 - a status bit, detail is not needed here
        checks["database"] = False
    checks["broker"] = _task_alive(broker_manager, "_cleanup_task")
    checks["cron_scheduler"] = _task_alive(cron_scheduler, "_task")
    last: datetime | None = None
    if db is not None and db.configured:
        try:
            row = await last_ping(db, "cron")
            last = row["created_at"] if row else None
        except Exception:  # noqa: BLE001
            last = None
    if not all(checks.values()):
        bad = ", ".join(k for k, v in checks.items() if not v)
        return RuntimeStatus("down", f"Not ok: {bad}", graphs, checks, last)
    if last is None:
        return RuntimeStatus("degraded", "Runtime up; no cron ping recorded yet", graphs, checks, last)
    if datetime.now(UTC) - last > CRON_STALE_AFTER:
        return RuntimeStatus(
            "degraded", f"Runtime up; last cron ping {last.isoformat(timespec='minutes')}", graphs, checks, last
        )
    return RuntimeStatus(
        "ok",
        f"{len(graphs)} graphs, crons firing (last ping {last.isoformat(timespec='minutes')})",
        graphs,
        checks,
        last,
    )


def _task_alive(obj: Any, attr: str) -> bool:
    """True when the object's background task is running (or the object keeps none, e.g. a no-op broker)."""
    if not hasattr(obj, attr):
        return True
    task = getattr(obj, attr)
    return task is not None and not task.done()
