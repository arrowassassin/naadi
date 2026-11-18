"""One approval-queue registry for every queue (generated media, YouTube candidates, AI questions, pathway checks,
reported issues). Contract: ledger 20261117T042408-seg19-admin-91a115 + amendment 20261117T043050-seg19-admin-0e47ff.

Owners register a kind at import time of a module `main.py` imports:

    register_queue("youtube_candidates", list_fn, approve_fn, reject_fn, ref_type="media_asset")

- `list_fn(ctx, *, limit, cursor) -> QueuePage`
- `approve_fn(ctx, item_id, note, params) -> QueueDecision`, `reject_fn(ctx, item_id, note) -> QueueDecision`
- optional `count_fn(ctx) -> int` (default: `list_fn(limit=1).total`)

`ctx.conn` is an open service-role transaction (dict rows). The registry writes the `admin_audit` row for the
decision in that transaction and commits after the function returns; any exception rolls everything back.
Raise `GateRefused(reason, ref_id)` (409) or `ItemGone()` (404, also for an item that was already decided).
Items carry structured `detail` only, never English sentences: the web view formats them with ui_copy.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, HTTPException, Path, Query, Request
from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ..auth import CurrentUser
from ..errors import ErrorResponse
from .common import ADMIN_ERRORS, CONFLICT, NOT_FOUND, ConsoleAdmin, audit, run_tx
from .licence import GateRefused

KIND = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
RefType = Literal[
    "section",
    "question",
    "media",
    "chapter",
    "generated_media",
    "pathway_task",
    "youtube_candidate",
    "report",
]


class ItemGone(Exception):
    """The item does not exist (any more) or was already decided: 404."""


class QueueRef(BaseModel):
    type: RefType
    id: str = Field(min_length=1, max_length=200)


class QueueItem(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    kind: str
    created_at: dt.datetime
    title: str | None = Field(default=None, description="The content's own title (not UI copy)")
    ref: QueueRef | None = Field(default=None, description="Deep link target")
    detail: dict[str, JsonValue] = Field(default_factory=dict, description="Structured, kind-specific fields")


class QueuePage(BaseModel):
    items: list[QueueItem]
    next_cursor: str | None = None
    total: int = Field(ge=0)


class QueueDecision(BaseModel):
    id: str
    status: Literal["approved", "rejected"]
    before: dict[str, Any] | None = Field(default=None, exclude=True)
    after: dict[str, Any] | None = Field(default=None, exclude=True)


@dataclass(frozen=True)
class QueueContext:
    app: Any
    admin: CurrentUser
    conn: AsyncConnection[Any]


class ListFn(Protocol):
    def __call__(self, ctx: QueueContext, *, limit: int, cursor: str | None) -> Awaitable[QueuePage]: ...


ApproveFn = Callable[[QueueContext, str, str | None, dict[str, JsonValue]], Awaitable[QueueDecision]]
RejectFn = Callable[[QueueContext, str, str | None], Awaitable[QueueDecision]]
CountFn = Callable[[QueueContext], Awaitable[int]]


@dataclass(frozen=True)
class QueueSpec:
    kind: str
    list_fn: ListFn
    approve_fn: ApproveFn | None
    reject_fn: RejectFn | None
    count_fn: CountFn | None
    ref_type: str
    path: str | None = None
    filters: tuple[str, ...] = ()


_REGISTRY: dict[str, QueueSpec] = {}


def register_queue(
    kind: str,
    list_fn: ListFn,
    approve_fn: ApproveFn | None,
    reject_fn: RejectFn | None,
    *,
    ref_type: str,
    count_fn: CountFn | None = None,
    path: str | None = None,
    filters: tuple[str, ...] = (),
) -> None:
    """Register a queue. A queue decided on its own admin page (S21's two-reviewer review) passes
    `approve_fn=None, reject_fn=None, path="/admin/review"`: the hub shows its count and first page and links there."""
    if (approve_fn is None) != (reject_fn is None):
        raise ValueError("a queue has both approve_fn and reject_fn, or neither (decided on its own page)")
    if approve_fn is None and not path:
        raise ValueError("a queue without approve/reject needs the path of the page where it is decided")
    if path is not None and not re.match(r"^/admin(/[a-z0-9_-]+)+$", path):
        raise ValueError(f"path {path!r} must be an /admin/... route")
    if any(not re.match(r"^[a-z][a-z0-9_]{0,47}$", f) for f in filters):
        raise ValueError("filters are snake_case keys")
    if not KIND.match(kind):
        raise ValueError(f"queue kind {kind!r} must match {KIND.pattern}")
    if not re.match(r"^[a-z][a-z0-9_]{0,47}$", ref_type):
        raise ValueError(f"ref_type {ref_type!r} is not an admin_audit ref_type")
    if kind in _REGISTRY:
        raise ValueError(f"queue kind {kind!r} is already registered")
    _REGISTRY[kind] = QueueSpec(kind, list_fn, approve_fn, reject_fn, count_fn, ref_type, path, tuple(filters))


def unregister_queue(kind: str) -> None:
    """Tests only."""
    _REGISTRY.pop(kind, None)


def registered_kinds() -> list[str]:
    return sorted(_REGISTRY)


def _spec(kind: str) -> QueueSpec:
    spec = _REGISTRY.get(kind)
    if spec is None:
        raise HTTPException(404, "No such queue.")
    return spec


# ── HTTP ──

router = APIRouter(prefix="/api/admin/queues", tags=["admin"])
KindPath = Annotated[str, Path(pattern=KIND.pattern, description="Queue kind, e.g. generated_media")]
ItemPath = Annotated[str, Path(min_length=1, max_length=200)]


class QueueCount(BaseModel):
    kind: str
    count: int = Field(ge=0)
    decides_here: bool = Field(description="Approve / reject in the queues hub (false: decided on `path`)")
    path: str | None = Field(description="Admin page where this queue is worked, when it has its own")
    filters: list[str]


class QueueCounts(BaseModel):
    items: list[QueueCount]


class QueueApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=1000)
    params: dict[str, JsonValue] = Field(default_factory=dict, description="Kind-specific approve options")


class QueueReject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=1000)


async def _count(spec: QueueSpec, ctx: QueueContext) -> int:
    if spec.count_fn is not None:
        return await spec.count_fn(ctx)
    return (await spec.list_fn(ctx, limit=1, cursor=None)).total


@router.get(
    "",
    operation_id="listAdminQueues",
    summary="Admin only: pending items per registered approval queue",
    response_model=QueueCounts,
    responses=ADMIN_ERRORS,
)
async def list_queues(request: Request, admin: ConsoleAdmin) -> QueueCounts:
    async def run(conn: AsyncConnection[Any]) -> QueueCounts:
        ctx = QueueContext(request.app, admin, conn)
        items = []
        for k in registered_kinds():
            spec = _REGISTRY[k]
            items.append(
                QueueCount(
                    kind=k,
                    count=await _count(spec, ctx),
                    decides_here=spec.approve_fn is not None,
                    path=spec.path,
                    filters=list(spec.filters),
                )
            )
        return QueueCounts(items=items)

    return await run_tx(request, run)


@router.get(
    "/{kind}",
    operation_id="getAdminQueue",
    summary="Admin only: one page of pending items in a queue, oldest first",
    response_model=QueuePage,
    responses={**ADMIN_ERRORS, **NOT_FOUND},
)
async def get_queue(
    request: Request,
    admin: ConsoleAdmin,
    kind: KindPath,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
) -> QueuePage:
    spec = _spec(kind)

    async def run(conn: AsyncConnection[Any]) -> QueuePage:
        return await spec.list_fn(QueueContext(request.app, admin, conn), limit=limit, cursor=cursor)

    return await run_tx(request, run)


async def _decide(
    request: Request, admin: CurrentUser, spec: QueueSpec, action: str, note: str | None, fn: Callable[..., Any]
) -> QueueDecision:
    async def run(conn: AsyncConnection[Any]) -> QueueDecision:
        ctx = QueueContext(request.app, admin, conn)
        try:
            decision: QueueDecision = await fn(ctx)
        except ItemGone as exc:
            raise HTTPException(404, "This item is no longer waiting for a decision.") from exc
        await audit(
            conn,
            admin,
            f"queue.{spec.kind}.{action}",
            spec.ref_type,
            decision.id,
            before=decision.before,
            after=decision.after,
            note=note,
        )
        return decision

    return await run_tx(request, run)


@router.post(
    "/{kind}/{item_id}/approve",
    operation_id="approveAdminQueueItem",
    summary="Admin only: approve one queued item (publishing gates apply)",
    response_model=QueueDecision,
    responses={
        **ADMIN_ERRORS,
        **NOT_FOUND,
        **CONFLICT,
        405: {"model": ErrorResponse, "description": "Decided on its own page"},
    },
)
async def approve(
    request: Request, admin: ConsoleAdmin, kind: KindPath, item_id: ItemPath, body: QueueApprove
) -> QueueDecision:
    spec = _spec(kind)
    fn = spec.approve_fn
    if fn is None:
        raise HTTPException(405, "This queue is decided on its own page.")
    return await _decide(
        request, admin, spec, "approve", body.note, lambda ctx: fn(ctx, item_id, body.note, body.params)
    )


@router.post(
    "/{kind}/{item_id}/reject",
    operation_id="rejectAdminQueueItem",
    summary="Admin only: reject one queued item",
    response_model=QueueDecision,
    responses={**ADMIN_ERRORS, **NOT_FOUND, 405: {"model": ErrorResponse, "description": "Decided on its own page"}},
)
async def reject(
    request: Request, admin: ConsoleAdmin, kind: KindPath, item_id: ItemPath, body: QueueReject
) -> QueueDecision:
    spec = _spec(kind)
    rfn = spec.reject_fn
    if rfn is None:
        raise HTTPException(405, "This queue is decided on its own page.")
    return await _decide(request, admin, spec, "reject", body.note, lambda ctx: rfn(ctx, item_id, body.note))


__all__ = [
    "GateRefused",
    "ItemGone",
    "QueueContext",
    "QueueDecision",
    "QueueItem",
    "QueuePage",
    "QueueRef",
    "register_queue",
    "registered_kinds",
    "router",
]
