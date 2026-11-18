"""Transactions and errors shared by the pathway modules. No model, search or embeddings import lives here, so the
vault can use it."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
from fastapi import Depends, Request
from psycopg.rows import dict_row, tuple_row

from ..auth import CurrentUser, get_account_user
from ..auth_db import user_tx
from ..auth_limits import RateLimiter
from ..db import Database, DatabaseNotConfigured
from ..logs import get_logger

log = get_logger("naadi.pathway")


class PathwayError(Exception):
    """An error with a stable code the client words (ui_copy `pathway.error.<code>`, `vault.error.<code>`,
    `costs.error.<code>`)."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def default_limiters() -> dict[str, RateLimiter]:
    return {
        "pathway_read": RateLimiter(300, 60.0),
        "pathway_write": RateLimiter(300, 60.0),
        "costs_read": RateLimiter(300, 60.0),
        "costs_write": RateLimiter(300, 60.0),
        "vault_read": RateLimiter(120, 60.0),
        # Heavy (S22 rule: at most 30 a minute per user): every vault write creates, reads or removes a file.
        "vault_write": RateLimiter(30, 60.0),
        "vault_url": RateLimiter(60, 60.0),
        "pathway_admin": RateLimiter(30, 60.0),
    }


def limited(bucket: str) -> Any:
    async def dep(request: Request, user: CurrentUser = Depends(get_account_user)) -> CurrentUser:  # noqa: B008
        limiters: dict[str, RateLimiter] = request.app.state.rate_limiters
        limiters[bucket].hit(str(user.user_id))
        return user

    return Depends(dep)


def database(request: Request) -> Database:
    d: Database = request.app.state.db
    return d


@asynccontextmanager
async def service_tx(db: Database) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    """The API's privileged role: students cannot write these tables, so every statement filters on the caller."""
    if not db.configured:
        raise DatabaseNotConfigured("DATABASE_URL is not set")
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    async with db.pool.connection(timeout=5.0) as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]
        try:
            async with conn.transaction():
                await conn.execute("set local statement_timeout = 10000")
                yield conn
        finally:
            conn.row_factory = tuple_row


async def _guard[T](fn: Callable[[], Awaitable[T]]) -> T:
    try:
        return await fn()
    except DatabaseNotConfigured as exc:
        raise PathwayError(503, "pathway_unavailable") from exc
    except (psycopg.OperationalError, TimeoutError) as exc:
        log.warning("pathway_db_unavailable", error=type(exc).__name__)
        raise PathwayError(503, "pathway_unavailable") from exc


async def as_service[T](db: Database, fn: Callable[[psycopg.AsyncConnection[Any]], Awaitable[T]]) -> T:
    async def run() -> T:
        async with service_tx(db) as conn:
            return await fn(conn)

    return await _guard(run)


async def as_user[T](db: Database, user: CurrentUser, fn: Callable[[psycopg.AsyncConnection[Any]], Awaitable[T]]) -> T:
    async def run() -> T:
        async with user_tx(db, user, deadline_s=8.0) as conn:
            return await fn(conn)

    return await _guard(run)


def now_ms() -> int:
    return int(time.time() * 1000)


def local_today(tz: str | None, now: dt.datetime | None = None) -> dt.date:
    try:
        zone = ZoneInfo(tz or "UTC")
    except (KeyError, ValueError):
        zone = ZoneInfo("UTC")
    return (now or dt.datetime.now(dt.UTC)).astimezone(zone).date()


async def student_today(conn: psycopg.AsyncConnection[Any], user_id: Any) -> dt.date:
    cur = await conn.execute("select timezone from public.profiles where user_id = %s", (user_id,))
    row = await cur.fetchone()
    tz = row["timezone"] if row else None
    return local_today(tz)
