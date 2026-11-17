"""Daily clean-up of the sync tables: stored idempotent responses after 24 hours, tombstones after 90 days.

Runs in the API process like the ink tombstone purge (`ink/routes.py`): a background task started at startup,
first pass ~10 minutes in, then every 24 hours; a transaction-level advisory lock lets one worker do it when
several run. A tombstone older than 90 days no longer decides anything: an outbox op older than that cannot
exist (the offline week plus the client's 31-day review horizon), so dropping it cannot resurrect a row.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from dataclasses import dataclass
from typing import Any

import psycopg

from ..db import Database, DatabaseNotConfigured
from ..logs import get_logger
from .idempotency import RETENTION_HOURS

log = get_logger("naadi.sync")

TOMBSTONE_DAYS = 90
GC_INTERVAL_S = 24 * 3600.0
GC_FIRST_DELAY_S = 600.0
GC_LOCK_KEY = 0x7379_6E63  # "sync": its own advisory lock id (the ink purge uses another)
BATCH = 5000


@dataclass(frozen=True, slots=True)
class Purged:
    ops: int
    tombstones: int


async def run_gc(db: Database) -> Purged | None:
    """One pass; None when another worker holds the lock. Deletes in batches so a backlog never holds long locks."""
    if not db.configured:
        raise DatabaseNotConfigured("DATABASE_URL is not set")
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    async with db.pool.connection(timeout=10.0) as conn, conn.transaction():
        await conn.execute("set local statement_timeout = 60000")
        cur = await conn.execute("select pg_try_advisory_xact_lock(%s)", (GC_LOCK_KEY,))
        row = await cur.fetchone()
        if not row or not row[0]:
            return None
        ops = await _purge(conn, _PURGE_OPS)
        tombs = await _purge(conn, _PURGE_TOMBSTONES)
        return Purged(ops, tombs)


_PURGE_OPS = (
    "delete from public.sync_ops where ctid = any(array(select ctid from public.sync_ops"  # noqa: S608 - constants
    f" where created_at < now() - interval '{RETENTION_HOURS} hours' limit {BATCH}))"
)
_PURGE_TOMBSTONES = (
    "delete from public.sync_tombstones where ctid = any(array(select ctid from public.sync_tombstones"  # noqa: S608
    f" where created_at < now() - interval '{TOMBSTONE_DAYS} days' limit {BATCH}))"
)


async def _purge(conn: psycopg.AsyncConnection[Any], query: str) -> int:
    total = 0
    while True:
        cur = await conn.execute(query)
        n = int(cur.rowcount)
        total += n
        if n < BATCH:
            return total


async def _loop(db: Database) -> None:
    await asyncio.sleep(GC_FIRST_DELAY_S + random.uniform(0, 60))  # noqa: S311 - jitter, not security
    while True:
        try:
            done = await run_gc(db)
            log.info(
                "sync_gc",
                ops=done.ops if done else 0,
                tombstones=done.tombstones if done else 0,
                skipped=done is None,
            )
        except Exception as exc:  # noqa: BLE001 - the loop must survive a bad night; retried tomorrow
            log.warning("sync_gc_failed", error=type(exc).__name__)
        await asyncio.sleep(GC_INTERVAL_S)


async def start_sync_gc(app: Any) -> None:
    db: Database = app.state.db
    if not db.configured:
        return
    app.state.sync_gc_task = asyncio.create_task(_loop(db), name="sync-gc")


async def stop_sync_gc(app: Any) -> None:
    task: asyncio.Task[None] | None = getattr(app.state, "sync_gc_task", None)
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    app.state.sync_gc_task = None


__all__ = ["TOMBSTONE_DAYS", "Purged", "run_gc", "start_sync_gc", "stop_sync_gc"]
