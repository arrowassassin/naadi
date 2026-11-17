"""Ink sync API (segment 12b): load a surface's layer, send changes, merged per item with last-writer-wins.

    GET  /api/ink/{surface_type}/{surface_id}?since=<rev>
    POST /api/ink/{surface_type}/{surface_id}/changes   {base_rev, items[]}

Request bodies are never logged (ink is note content); logs carry counts only. Responses carry structured fields;
the client words them with ui_copy.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Query, Request
from psycopg.rows import dict_row, tuple_row
from pydantic import ValidationError
from starlette.exceptions import HTTPException

from ..auth import CurrentUser, get_account_user
from ..auth_db import user_tx
from ..auth_limits import RateLimiter
from ..db import Database, DatabaseNotConfigured
from ..errors import ErrorResponse, error_response
from ..logs import get_logger
from .models import (
    INK_MAX_ITEMS,
    MAX_CHANGE_BYTES,
    MAX_CHANGE_ITEMS,
    InkChangesIn,
    InkChangesOut,
    InkLayerOut,
    InkRejected,
    normalise_ints,
    parse_item,
)
from .store import (
    FUTURE_SKEW_MS,
    TOMBSTONE_HORIZON_DAYS,
    Surface,
    SurfaceNotFound,
    TooManyItems,
    apply_changes,
    purge_tombstones,
    read_layer,
    resolve_surface,
)

log = get_logger("naadi.ink")
router = APIRouter(prefix="/api", tags=["ink"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    404: {"model": ErrorResponse, "description": "Unknown surface, or not visible to the caller"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Database unavailable"},
}

GC_INTERVAL_S = 24 * 3600.0
GC_FIRST_DELAY_S = 600.0
GC_LOCK_KEY = 0x6E6B_6763  # pg advisory lock id for the purge: one worker runs it


def default_limiters() -> dict[str, RateLimiter]:
    # A device flushes at most every 800 ms while drawing and refetches every 30 s per mounted surface.
    return {"ink_read": RateLimiter(600, 60.0), "ink_write": RateLimiter(300, 60.0)}


def _limited(bucket: str) -> Any:
    async def dep(request: Request, user: Annotated[CurrentUser, Depends(get_account_user)]) -> CurrentUser:
        limiters: dict[str, RateLimiter] = request.app.state.rate_limiters
        limiters[bucket].hit(str(user.user_id))
        return user

    return Depends(dep)


def _db(request: Request) -> Database:
    d: Database = request.app.state.db
    return d


def now_ms() -> int:
    return int(time.time() * 1000)


@asynccontextmanager
async def _service_tx(db: Database) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    """The API's privileged role (clients cannot write ink tables); every statement filters on the caller."""
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
        raise HTTPException(503) from exc
    except (psycopg.OperationalError, TimeoutError) as exc:
        log.warning("ink_db_unavailable", error=type(exc).__name__)
        raise HTTPException(503) from exc


async def _surface(db: Database, user: CurrentUser, surface_type: str, surface_id: str) -> Surface:
    async def run() -> Surface:
        async with user_tx(db, user) as conn:
            return await resolve_surface(conn, surface_type, surface_id)

    try:
        return await _guard(run)
    except SurfaceNotFound as exc:
        raise HTTPException(404) from exc


@router.get(
    "/ink/{surface_type}/{surface_id}",
    operation_id="getInkLayer",
    summary="The caller's ink on one surface: a full load, or the changes since a sync cursor",
    response_model=InkLayerOut,
    responses=_ERRORS,
)
async def get_ink_layer(
    request: Request,
    surface_type: str,
    surface_id: str,
    user: Annotated[CurrentUser, _limited("ink_read")],
    since: Annotated[int | None, Query(ge=0, description="`rev` from the last response; omit for a full load")] = None,
) -> InkLayerOut:
    db = _db(request)
    surface = await _surface(db, user, surface_type, surface_id)

    async def run() -> InkLayerOut:
        async with user_tx(db, user) as conn:  # reads under RLS
            r = await read_layer(conn, user.user_id, surface, since)
        return InkLayerOut(
            layer_id=r.layer_id,
            rev=r.rev,
            items=r.items,
            reset=r.reset,
            hash=r.hash,
            item_count=r.item_count,
            outdated=r.outdated,
            server_now=now_ms(),
        )

    return await _guard(run)


async def _body_within_limit(request: Request) -> None:
    """413 before parsing: the declared length first, then the bytes actually read."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_CHANGE_BYTES:
        raise HTTPException(413)
    if len(await request.body()) > MAX_CHANGE_BYTES:
        raise HTTPException(413)


def _prepare(raw: list[Any], server_now: int) -> tuple[list[dict[str, Any]], list[InkRejected], set[str]]:
    """Validate each item (one bad item does not sink the batch), write integral numbers as integers, clamp clocks
    ahead of the server, and keep one version per id (the newer by the merge order; on a full tie the first sent).
    Returns the items, the rejections and the ids whose stamp was clamped (their stored copy goes back in `remote`,
    so the device adopts the server's stamp instead of keeping its future one)."""
    rejected: list[InkRejected] = []
    by_id: dict[str, dict[str, Any]] = {}
    clamped: set[str] = set()
    for item in raw:
        item_id = item.get("id") if isinstance(item, dict) else None
        try:
            parse_item(item)
        except ValidationError:
            rejected.append(InkRejected(id=item_id if isinstance(item_id, str) else None, reason="invalid"))
            continue
        it = normalise_ints(item)
        was_clamped = it["updatedAt"] > server_now + FUTURE_SKEW_MS
        if was_clamped:
            it["updatedAt"] = server_now
        prev = by_id.get(it["id"])
        if prev is not None:
            rejected.append(InkRejected(id=it["id"], reason="duplicate"))
            newer = it["updatedAt"] > prev["updatedAt"] or (
                it["updatedAt"] == prev["updatedAt"] and bool(it.get("deleted")) and not prev.get("deleted")
            )
            if not newer:
                continue
        by_id[it["id"]] = it
        if was_clamped:
            clamped.add(it["id"])
        else:
            clamped.discard(it["id"])
    return list(by_id.values()), rejected, clamped


@router.post(
    "/ink/{surface_type}/{surface_id}/changes",
    operation_id="postInkChanges",
    summary="Send ink upserts and tombstones; merged per item (last writer wins, tombstone on a tie); idempotent",
    response_model=InkChangesOut,
    responses={
        **_ERRORS,
        409: {"model": ErrorResponse, "description": f"The layer would hold more than {INK_MAX_ITEMS} items"},
        413: {"model": ErrorResponse, "description": "The request body is larger than 2 MB"},
        422: {"model": ErrorResponse, "description": f"Not a change set (more than {MAX_CHANGE_ITEMS} items?)"},
    },
    dependencies=[Depends(_body_within_limit)],
)
async def post_ink_changes(
    request: Request,
    surface_type: str,
    surface_id: str,
    body: InkChangesIn,
    user: Annotated[CurrentUser, _limited("ink_write")],
) -> Any:
    db = _db(request)
    surface = await _surface(db, user, surface_type, surface_id)
    server_now = now_ms()
    items, rejected, clamped = _prepare(body.items, server_now)

    async def run() -> InkChangesOut:
        async with _service_tx(db) as conn:
            r = await apply_changes(conn, user.user_id, surface, body.base_rev, items, echo=clamped)
        stale = {i: InkRejected(id=i, reason="stale") for i in r.stale}
        log.info(
            "ink_changes",
            surface_type=surface.type,
            sent=len(body.items),
            applied=len(r.applied),
            stale=len(stale),
            rejected=len(rejected),
            remote=len(r.remote),
            clamped=len(clamped),
        )
        return InkChangesOut(
            layer_id=r.layer_id,
            rev=r.rev,
            applied=r.applied,
            rejected=[*rejected, *stale.values()],
            remote=r.remote,
            reset=r.reset,
            hash=r.hash,
            item_count=r.item_count,
            server_now=server_now,
        )

    try:
        return await _guard(run)
    except TooManyItems:
        return error_response(409, code="ink_item_limit")


# ── Daily tombstone purge ──


async def run_purge(db: Database) -> int | None:
    """One purge pass; None when another worker holds the lock."""
    async with _service_tx(db) as conn:
        cur = await conn.execute("select pg_try_advisory_xact_lock(%s) as ok", (GC_LOCK_KEY,))
        row = await cur.fetchone()
        if not row or not row["ok"]:
            return None
        return await purge_tombstones(conn, days=TOMBSTONE_HORIZON_DAYS)


async def _gc_loop(db: Database) -> None:
    await asyncio.sleep(GC_FIRST_DELAY_S + random.uniform(0, 60))  # noqa: S311 - jitter, not security
    while True:
        try:
            removed = await run_purge(db)
            log.info("ink_tombstones_purged", removed=removed if removed is not None else 0, skipped=removed is None)
        except Exception as exc:  # noqa: BLE001 - the loop must survive a bad night; retried tomorrow
            log.warning("ink_purge_failed", error=type(exc).__name__)
        await asyncio.sleep(GC_INTERVAL_S)


async def start_ink_gc(app: Any) -> None:
    db: Database = app.state.db
    if not db.configured:
        return
    app.state.ink_gc_task = asyncio.create_task(_gc_loop(db), name="ink-tombstone-gc")


async def stop_ink_gc(app: Any) -> None:
    task: asyncio.Task[None] | None = getattr(app.state, "ink_gc_task", None)
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    app.state.ink_gc_task = None


def install_ink_routes(app: Any) -> None:
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
    app.include_router(router)


__all__ = ["install_ink_routes", "router", "run_purge", "start_ink_gc", "stop_ink_gc"]
