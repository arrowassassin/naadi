"""Shared plumbing for the admin console (segment 19).

Every admin route depends on `ConsoleAdmin`: the server-side admin gate (`require_admin`, which also checks the
session in Supabase's auth schema) plus the `admin_console` rate-limit bucket. Shared content is written on a
pooled service connection (`service_tx`), never as `authenticated`: RLS grants clients no writes on shared
tables, and the admin gate is the enforcement. Each write records an `admin_audit` row in the same transaction
(`audit`), with sha256 hashes of the row before and after, never the content itself.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Annotated, Any

import psycopg
from fastapi import Depends, HTTPException, Request
from psycopg import AsyncConnection
from psycopg.rows import dict_row, tuple_row

from ..auth import CurrentUser, require_admin
from ..auth_limits import RateLimiter
from ..db import Database, DatabaseNotConfigured
from ..errors import CodedHTTPException, ErrorResponse
from ..logs import get_logger

log = get_logger("naadi.admin")

STATEMENT_TIMEOUT_MS = 8000
# The chapter editor autosaves a draft every couple of seconds while typing, so the bucket is wider than the
# 60/min `admin` bucket of the account and config routes.
CONSOLE_LIMIT = (600, 60.0)

Row = dict[str, Any]

ADMIN_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    403: {"model": ErrorResponse, "description": "Not an admin"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Database unavailable"},
}
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse, "description": "No such item"}}
CONFLICT: dict[int | str, dict[str, Any]] = {
    409: {"model": ErrorResponse, "description": "Changed since you read it, or a publishing gate refused"}
}
INVALID: dict[int | str, dict[str, Any]] = {422: {"model": ErrorResponse, "description": "The request was not valid"}}


def default_console_limiters() -> dict[str, RateLimiter]:
    return {"admin_console": RateLimiter(*CONSOLE_LIMIT)}


async def _console_admin(request: Request, admin: Annotated[CurrentUser, Depends(require_admin)]) -> CurrentUser:
    request.app.state.rate_limiters["admin_console"].hit(str(admin.user_id))
    return admin


ConsoleAdmin = Annotated[CurrentUser, Depends(_console_admin)]


def db_of(request: Request) -> Database:
    d: Database = request.app.state.db
    return d


UNAVAILABLE = "The database cannot be reached right now. Try again in a moment."


@asynccontextmanager
async def service_tx(db: Database, *, deadline_s: float = 10.0) -> AsyncIterator[AsyncConnection[Any]]:
    """A pooled backend connection inside one transaction (dict rows). Commits when the block exits cleanly."""
    if not db.configured:
        raise DatabaseNotConfigured("DATABASE_URL is not set")
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    async with db.pool.connection(timeout=deadline_s) as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]
        try:
            async with asyncio.timeout(deadline_s), conn.transaction():
                await conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                yield conn
        finally:
            conn.row_factory = tuple_row


async def run_tx[T](request: Request, fn: Callable[[AsyncConnection[Any]], Awaitable[T]]) -> T:
    """`fn` inside `service_tx`; database outages become a 503 with the app's error shape."""
    try:
        async with service_tx(db_of(request)) as conn:
            return await fn(conn)
    except DatabaseNotConfigured as exc:
        raise HTTPException(503, UNAVAILABLE) from exc
    except (psycopg.OperationalError, TimeoutError) as exc:
        log.warning("admin_db_unavailable", error=type(exc).__name__)
        raise HTTPException(503, UNAVAILABLE) from exc


async def rows(conn: AsyncConnection[Any], query: Any, params: Sequence[Any] | Mapping[str, Any] = ()) -> list[Row]:
    cur = await conn.execute(query, params)
    return [dict(r) for r in await cur.fetchall()]


async def one(conn: AsyncConnection[Any], query: Any, params: Sequence[Any] | Mapping[str, Any] = ()) -> Row | None:
    found = await rows(conn, query, params)
    return found[0] if found else None


def _default(o: Any) -> Any:
    if isinstance(o, dt.datetime | dt.date):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    if isinstance(o, decimal.Decimal):
        return str(o)
    if isinstance(o, bytes | bytearray | memoryview):
        return hashlib.sha256(bytes(o)).hexdigest()
    raise TypeError(type(o).__name__)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default)


def row_hash(value: Mapping[str, Any] | None) -> str | None:
    """sha256 of a row snapshot in canonical JSON (key order and spacing never change the hash)."""
    if value is None:
        return None
    return hashlib.sha256(canonical_json(dict(value)).encode()).hexdigest()


async def audit(
    conn: AsyncConnection[Any],
    admin: CurrentUser,
    action: str,
    ref_type: str,
    ref_id: str,
    *,
    before: Mapping[str, Any] | None = None,
    after: Mapping[str, Any] | None = None,
    note: str | None = None,
) -> None:
    """One `admin_audit` row, in the caller's transaction. `action` is dotted (`section.publish`)."""
    await conn.execute(
        "insert into public.admin_audit (actor_user_id, action, ref_type, ref_id, before_hash, after_hash, note)"
        " values (%s, %s, %s, %s, %s, %s, %s)",
        (admin.user_id, action, ref_type, ref_id, row_hash(before), row_hash(after), note),
    )
    log.info("admin_write", action=action, ref_type=ref_type, ref_id=ref_id)


def conflict(code: str, message: str) -> CodedHTTPException:
    return CodedHTTPException(409, code, message)


def not_found(what: str = "item") -> HTTPException:
    return HTTPException(404, f"No such {what}.")


def ensure_limiters(app: Any) -> None:
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_console_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
