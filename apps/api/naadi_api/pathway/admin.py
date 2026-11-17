"""Admin side of the pathway (segment 17).

    GET    /api/admin/pathway/checks?open=1        what the weekly check found (S19 shows this in its queues)
    POST   /api/admin/pathway/tasks/{id}/verify    sets last_verified to today, clears needs_review, resolves checks
    PATCH  /api/admin/pathway/tasks/{id}           detail, cost, official URL

`last_verified` changes only here, by an admin's confirmation: never by the weekly check.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Query, Request

from ..auth import CurrentUser, require_admin
from ..errors import ErrorResponse
from . import store
from .db import PathwayError, as_service, database
from .models import AdminTaskOut, CheckList, CheckOut, TaskAdminPatch, TaskId
from .routes import official_domains
from .rules import is_official_url

router = APIRouter(prefix="/api/admin/pathway", tags=["admin"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    403: {"model": ErrorResponse, "description": "This needs an admin account"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "pathway_unavailable"},
}


def _admin(bucket: str) -> Any:
    """Admin gate (server-side), then the per-admin limiter."""

    async def dep(request: Request, user: Annotated[CurrentUser, Depends(require_admin)]) -> CurrentUser:
        request.app.state.rate_limiters[bucket].hit(str(user.user_id))
        return user

    return Depends(dep)


@router.get(
    "/checks",
    operation_id="listPathwayChecks",
    summary="Official-page checks, newest first (`open=1`: changed, moved or unreachable and not yet resolved)",
    response_model=CheckList,
    responses=_ERRORS,
)
async def list_checks(
    request: Request,
    user: Annotated[CurrentUser, _admin("pathway_admin")],
    open: Annotated[bool, Query(description="Only checks that still need an admin")] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> CheckList:
    async def run(conn: psycopg.AsyncConnection[Any]) -> CheckList:
        items, open_count = await store.list_checks(conn, only_open=open, limit=limit)
        return CheckList(items=[CheckOut.model_validate(i) for i in items], open_count=open_count)

    return await as_service(database(request), run)


@router.post(
    "/tasks/{task_id}/verify",
    operation_id="verifyPathwayTask",
    summary="Confirm a task is still right: last_verified = today, needs_review cleared, open checks resolved",
    response_model=AdminTaskOut,
    responses={**_ERRORS, 404: {"model": ErrorResponse, "description": "pathway_task_not_found"}},
)
async def verify_task(
    request: Request, task_id: TaskId, user: Annotated[CurrentUser, _admin("pathway_admin")]
) -> AdminTaskOut:
    async def run(conn: psycopg.AsyncConnection[Any]) -> AdminTaskOut:
        row = await store.verify_task(conn, task_id, user.user_id, dt.datetime.now(dt.UTC).date())
        return AdminTaskOut.model_validate(row)

    return await as_service(database(request), run)


@router.patch(
    "/tasks/{task_id}",
    operation_id="patchPathwayTaskAdmin",
    summary="Edit a task's detail, cost estimate or official link (does not change last_verified)",
    response_model=AdminTaskOut,
    responses={
        **_ERRORS,
        404: {"model": ErrorResponse, "description": "pathway_task_not_found"},
        422: {"model": ErrorResponse, "description": "validation_error or pathway_url_not_official"},
    },
)
async def patch_task(
    request: Request, task_id: TaskId, body: TaskAdminPatch, user: Annotated[CurrentUser, _admin("pathway_admin")]
) -> AdminTaskOut:
    fields = body.model_dump(exclude_unset=True)
    for k in ("detail_md", "cost_usd", "official_url"):
        if k in fields and fields[k] is None:
            raise PathwayError(422, "validation_error")
    if "official_url" in fields and not is_official_url(fields["official_url"], await official_domains(request)):
        raise PathwayError(422, "pathway_url_not_official")

    async def run(conn: psycopg.AsyncConnection[Any]) -> AdminTaskOut:
        return AdminTaskOut.model_validate(await store.patch_task_admin(conn, task_id, fields))

    return await as_service(database(request), run)
