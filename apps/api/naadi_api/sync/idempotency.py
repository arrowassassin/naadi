"""`Idempotency-Key` for every write the offline outbox replays (segment 18).

A write route opts in with two things, both checked at startup by `check_routes`:

    router = APIRouter(prefix="/api", route_class=IdempotentRoute)
    @router.post("/x", dependencies=[IDEMPOTENT])

- No header: exactly the route's own behaviour (nothing is read or stored).
- A malformed key: 422 `idempotency_key_invalid` (the route does not run).
- First request with a key: the key is reserved for the caller (`sync_ops.status = 0`), the route runs, and its
  final response is stored: every 2xx, and every 4xx except 401, 408 and 429 (those can succeed on a retry).
  Anything else (5xx, a crash, a body that is not JSON) releases the key so the retry runs the route again.
- The same key again from the same user: the stored status and body, with `Idempotent-Replayed: true`, without
  running the route. While the first request is still running: 409 `idempotency_in_progress` with Retry-After.
  The same key on another method or path: 422 `idempotency_key_reused`.
- Keys are per user (another user's key is a different row) and kept 24 hours (`sync/gc.py`).

Stored bodies stay in `sync_ops` (no client grant); nothing here logs a body, a path or a key.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

import psycopg
from fastapi import Depends, FastAPI, Header, Request
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts
from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb
from starlette.responses import Response

from ..auth import CurrentUser, get_account_user
from ..db import Database, DatabaseNotConfigured
from ..errors import CodedHTTPException
from ..logs import get_logger

log = get_logger("naadi.sync")

HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"
RETRY_AFTER_S = 2
# A reservation older than this belongs to a request that died (its worker restarted); a retry takes it over.
STALE_RESERVATION_S = 60
RETENTION_HOURS = 24
NOT_STORED = frozenset({401, 408, 429})


def storable(status: int) -> bool:
    return 200 <= status < 300 or (400 <= status < 500 and status not in NOT_STORED)


# ── The store ──


@dataclass(frozen=True, slots=True)
class Stored:
    status: int
    body: Any  # parsed JSON, or None for an empty body (204)


@dataclass(frozen=True, slots=True)
class Reserved:
    pass


@dataclass(frozen=True, slots=True)
class Busy:
    pass


@dataclass(frozen=True, slots=True)
class Reused:
    pass


Outcome = Stored | Reserved | Busy | Reused


class OpStore(Protocol):
    async def reserve(self, user_id: uuid.UUID, op_id: uuid.UUID, method: str, path: str) -> Outcome: ...

    async def finish(self, user_id: uuid.UUID, op_id: uuid.UUID, status: int, body: Any) -> None: ...

    async def release(self, user_id: uuid.UUID, op_id: uuid.UUID) -> None: ...


_RESERVE = f"""
insert into public.sync_ops (user_id, op_id, method, path, status) values (%(uid)s, %(op)s, %(method)s, %(path)s, 0)
on conflict (user_id, op_id) do update set method = excluded.method, path = excluded.path, status = 0, body = null,
  created_at = now()
where sync_ops.created_at < now() - interval '{RETENTION_HOURS} hours'
   or (sync_ops.status = 0 and sync_ops.created_at < now() - interval '{STALE_RESERVATION_S} seconds'
       and sync_ops.method = excluded.method and sync_ops.path = excluded.path)
returning 1 as reserved
"""  # noqa: S608 - module constants only
_EXISTING = "select method, path, status, body from public.sync_ops where user_id = %(uid)s and op_id = %(op)s"


class PgOpStore:
    """`sync_ops` on the API's privileged connection (clients have no grant); each call is its own transaction."""

    def __init__(self, db: Database) -> None:
        self.db = db

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        if not self.db.configured:
            raise DatabaseNotConfigured("DATABASE_URL is not set")
        if self.db.pool is None:
            await self.db.open()
        assert self.db.pool is not None
        async with self.db.pool.connection(timeout=5.0) as conn:
            conn.row_factory = dict_row  # type: ignore[assignment]
            try:
                async with conn.transaction():
                    await conn.execute("set local statement_timeout = 5000")
                    yield conn
            finally:
                conn.row_factory = tuple_row

    async def reserve(self, user_id: uuid.UUID, op_id: uuid.UUID, method: str, path: str) -> Outcome:
        p = {"uid": user_id, "op": op_id, "method": method, "path": path}
        async with self._tx() as conn:
            cur = await conn.execute(_RESERVE, p)
            if await cur.fetchone() is not None:
                return Reserved()
            cur = await conn.execute(_EXISTING, p)
            row = await cur.fetchone()
        if row is None:  # deleted between the two statements (GC): the retry reserves it
            return Busy()
        return outcome_of(dict(row), method, path)

    async def finish(self, user_id: uuid.UUID, op_id: uuid.UUID, status: int, body: Any) -> None:
        async with self._tx() as conn:
            await conn.execute(
                "update public.sync_ops set status = %s, body = %s where user_id = %s and op_id = %s and status = 0",
                (status, Jsonb(body) if body is not None else None, user_id, op_id),
            )

    async def release(self, user_id: uuid.UUID, op_id: uuid.UUID) -> None:
        async with self._tx() as conn:
            await conn.execute(
                "delete from public.sync_ops where user_id = %s and op_id = %s and status = 0", (user_id, op_id)
            )


def outcome_of(row: dict[str, Any], method: str, path: str) -> Outcome:
    """What a key that is already taken means for this request (the store's row for it)."""
    if row["method"] != method or row["path"] != path:
        return Reused()
    if int(row["status"]) == 0:
        return Busy()
    return Stored(int(row["status"]), row["body"])


# ── The dependency and the route class ──


class IdempotentReplay(Exception):
    """Raised by the dependency to answer with the stored response instead of running the route."""

    def __init__(self, stored: Stored) -> None:
        super().__init__("replay")
        self.stored = stored


def replay_response(stored: Stored) -> Response:
    headers = {REPLAYED_HEADER: "true", "cache-control": "no-store"}
    if stored.body is None:
        return Response(status_code=stored.status, headers=headers)
    payload = json.dumps(stored.body, separators=(",", ":"), ensure_ascii=False).encode()
    return Response(payload, status_code=stored.status, media_type="application/json", headers=headers)


@dataclass(slots=True)
class _Pending:
    store: OpStore
    user_id: uuid.UUID
    op_id: uuid.UUID

    async def finish(self, response: Response) -> None:
        status = response.status_code
        if not storable(status):
            await self.release()
            return
        raw = getattr(response, "body", None)
        try:
            body = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            await self.release()  # not a JSON response: never replayed from the store
            return
        try:
            await self.store.finish(self.user_id, self.op_id, status, body)
        except (DatabaseNotConfigured, psycopg.Error, TimeoutError) as exc:
            # The write itself is done; a retry after the stale window runs the (idempotent) route again.
            log.warning("idempotency_store_failed", error=type(exc).__name__)

    async def release(self) -> None:
        try:
            await self.store.release(self.user_id, self.op_id)
        except (DatabaseNotConfigured, psycopg.Error, TimeoutError) as exc:
            log.warning("idempotency_release_failed", error=type(exc).__name__)


def op_store(request: Request) -> OpStore:
    store: OpStore | None = getattr(request.app.state, "sync_op_store", None)
    if store is None:
        store = PgOpStore(request.app.state.db)
        request.app.state.sync_op_store = store
    return store


def parse_key(raw: str) -> uuid.UUID:
    try:
        key = uuid.UUID(raw.strip())
    except (ValueError, AttributeError) as exc:
        raise CodedHTTPException(422, "idempotency_key_invalid", "idempotency_key_invalid") from exc
    if len(raw.strip()) != 36:  # the canonical 8-4-4-4-12 form only (no braces, urn: or bare hex)
        raise CodedHTTPException(422, "idempotency_key_invalid", "idempotency_key_invalid")
    return key


async def idempotency_key(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_account_user)],
    key: Annotated[
        str | None,
        Header(
            alias=HEADER,
            description="Optional client op id (uuid). A repeat with the same key returns the stored response "
            "(`Idempotent-Replayed: true`) without running the write again; kept 24 hours",
            max_length=64,
        ),
    ] = None,
) -> None:
    if key is None:
        return
    op_id = parse_key(key)
    store = op_store(request)
    try:
        outcome = await store.reserve(user.user_id, op_id, request.method, request.url.path)
    except DatabaseNotConfigured as exc:
        raise CodedHTTPException(503, "sync_unavailable", "sync_unavailable") from exc
    except (psycopg.OperationalError, TimeoutError) as exc:
        log.warning("idempotency_db_unavailable", error=type(exc).__name__)
        raise CodedHTTPException(503, "sync_unavailable", "sync_unavailable") from exc
    if isinstance(outcome, Stored):
        log.info("idempotent_replay", status=outcome.status)
        raise IdempotentReplay(outcome)
    if isinstance(outcome, Reused):
        raise CodedHTTPException(422, "idempotency_key_reused", "idempotency_key_reused")
    if isinstance(outcome, Busy):
        busy = CodedHTTPException(409, "idempotency_in_progress", "idempotency_in_progress")
        busy.headers = {"Retry-After": str(RETRY_AFTER_S)}
        raise busy
    request.state.idempotency = _Pending(store, user.user_id, op_id)


IDEMPOTENT = Depends(idempotency_key)

ExceptionHandler = Callable[[Request, Exception], Awaitable[Response]]


def _handler_for(request: Request, exc: Exception) -> ExceptionHandler | None:
    handlers: dict[Any, ExceptionHandler] = request.app.exception_handlers
    for cls in type(exc).__mro__:
        if cls in handlers:
            return handlers[cls]
    return None


class IdempotentRoute(APIRoute):
    """Stores the final response of a request that reserved an `Idempotency-Key` (see the module docstring).
    Routes without the IDEMPOTENT dependency, and requests without the header, pass through untouched."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        inner = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                response = await inner(request)
            except IdempotentReplay as replay:
                return replay_response(replay.stored)
            except Exception as exc:
                pending: _Pending | None = getattr(request.state, "idempotency", None)
                if pending is None:
                    raise
                to_response = _handler_for(request, exc)
                if to_response is None:  # a 500: the retry runs the route again
                    await pending.release()
                    raise
                response = await to_response(request, exc)
                await pending.finish(response)
                return response
            pending = getattr(request.state, "idempotency", None)
            if pending is not None:
                await pending.finish(response)
            return response

        return handler


def _uses_idempotency(dependant: Dependant) -> bool:
    return any(d.call is idempotency_key or _uses_idempotency(d) for d in dependant.dependencies)


def _api_routes(app: FastAPI) -> list[tuple[APIRoute, str]]:
    """Every APIRoute of the app with its effective path (FastAPI keeps included routers as contexts)."""
    out: list[tuple[APIRoute, str]] = []
    for ctx in iter_route_contexts(app.routes):
        r = ctx.original_route
        if isinstance(r, APIRoute):
            out.append((r, ctx.path or r.path))
    return out


def idempotent_paths(app: FastAPI) -> list[tuple[str, str]]:
    """(method, path) of every route that accepts an Idempotency-Key."""
    out: list[tuple[str, str]] = []
    for r, path in _api_routes(app):
        if _uses_idempotency(r.dependant):
            out.extend((m, path) for m in sorted(r.methods or ()))
    return out


def check_routes(app: FastAPI) -> None:
    """Every route with the dependency must also store its responses (an IdempotentRoute)."""
    for r, path in _api_routes(app):
        if _uses_idempotency(r.dependant) and not isinstance(r, IdempotentRoute):
            raise RuntimeError(f"{path} takes an Idempotency-Key but is not an IdempotentRoute")


__all__ = [
    "HEADER",
    "IDEMPOTENT",
    "REPLAYED_HEADER",
    "Busy",
    "IdempotentReplay",
    "IdempotentRoute",
    "OpStore",
    "PgOpStore",
    "Reserved",
    "Reused",
    "Stored",
    "check_routes",
    "idempotency_key",
    "idempotent_paths",
    "outcome_of",
    "parse_key",
    "replay_response",
    "storable",
]
