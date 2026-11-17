"""Generation API (segment 15b).

    GET    /api/generation/status               state (not_configured | disabled | ready | paused), reset time, usage
    GET    /api/generated                       the caller's generated items (filters: kind, before, limit)
    GET    /api/generated/{id}                  one item with a fresh signed URL
    DELETE /api/generated/{id}                  the row and its private file (a published copy stays)
    GET    /api/admin/generated?pending=1       ready images waiting for approval (admin)
    POST   /api/admin/generated/{id}/approve    publish as a shared chapter figure (admin)
    POST   /api/generated/stub                  non-production stub only: run a generation against the fake client

Generation itself runs only through the tutor tools (confirm first). Errors carry a stable `code` the client words
with ui_copy (`generation.error.<code>`); prompts and files are never logged.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse, Response

from ..auth import AdminUser, CurrentUser, get_account_user
from ..auth_limits import RateLimiter
from ..db import DatabaseNotConfigured
from ..errors import ErrorResponse, error_response
from ..logs import get_logger
from .models import (
    LIST_LIMIT_DEFAULT,
    LIST_LIMIT_MAX,
    AdminGeneratedList,
    GeneratedApproveBody,
    GeneratedApproveOut,
    GeneratedItem,
    GeneratedKind,
    GeneratedList,
    GenerationStatusOut,
    StubRequest,
)
from .service import GenerationError, GenerationService

log = get_logger("naadi.generation")
router = APIRouter(prefix="/api", tags=["generation"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Database unavailable (code generation_unavailable)"},
}
_ADMIN_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse, "description": "Not an admin"},
    404: {"model": ErrorResponse, "description": "generated_not_found or section_not_found"},
    409: {"model": ErrorResponse, "description": "generated_not_ready"},
    422: {"model": ErrorResponse, "description": "validation_error or generated_video_not_publishable"},
}

MediaId = Annotated[uuid.UUID, Path(description="Generated media id")]


def default_limiters() -> dict[str, RateLimiter]:
    return {"generation_read": RateLimiter(240, 60.0), "generation_write": RateLimiter(30, 60.0)}


def limited(bucket: str) -> Any:
    async def dep(request: Request, user: Annotated[CurrentUser, Depends(get_account_user)]) -> CurrentUser:
        limiters: dict[str, RateLimiter] = request.app.state.rate_limiters
        limiters[bucket].hit(str(user.user_id))
        return user

    return Depends(dep)


def _service(request: Request) -> GenerationService:
    svc: GenerationService = request.app.state.generation
    return svc


async def _guard[T](fn: Callable[[], Awaitable[T]]) -> T:
    try:
        return await fn()
    except DatabaseNotConfigured as exc:
        raise GenerationError(503, "generation_unavailable") from exc


@router.get(
    "/generation/status",
    response_model=GenerationStatusOut,
    summary="Generation state, reset time and today's usage for the caller",
    responses=_ERRORS,
)
async def generation_status(
    request: Request, user: Annotated[CurrentUser, limited("generation_read")]
) -> GenerationStatusOut:
    s = request.app.state.settings
    enabled = await request.app.state.app_config.feature_enabled(
        "generation", is_admin=user.is_admin, app_env=s.app_env
    )
    return await _guard(lambda: _service(request).status(user.user_id, enabled=enabled))


@router.get(
    "/generated", response_model=GeneratedList, summary="The caller's generated images and clips", responses=_ERRORS
)
async def list_generated(
    request: Request,
    user: Annotated[CurrentUser, limited("generation_read")],
    kind: Annotated[GeneratedKind | None, Query()] = None,
    before: Annotated[datetime | None, Query(description="Return items older than this timestamp")] = None,
    limit: Annotated[int, Query(ge=1, le=LIST_LIMIT_MAX)] = LIST_LIMIT_DEFAULT,
) -> GeneratedList:
    items, nxt = await _guard(lambda: _service(request).list_items(user.user_id, kind=kind, before=before, limit=limit))
    return GeneratedList(items=items, next_before=nxt)


@router.get(
    "/generated/{media_id}",
    response_model=GeneratedItem,
    summary="One generated item (fresh signed URL)",
    responses={**_ERRORS, 404: {"model": ErrorResponse, "description": "generated_not_found"}},
)
async def get_generated(
    request: Request, media_id: MediaId, user: Annotated[CurrentUser, limited("generation_read")]
) -> GeneratedItem:
    item = await _guard(lambda: _service(request).get(user.user_id, media_id))
    if item is None:
        raise GenerationError(404, "generated_not_found")
    return item


@router.delete(
    "/generated/{media_id}",
    status_code=204,
    summary="Delete a generated item and its private file",
    responses={**_ERRORS, 404: {"model": ErrorResponse, "description": "generated_not_found"}},
)
async def delete_generated(
    request: Request, media_id: MediaId, user: Annotated[CurrentUser, limited("generation_write")]
) -> Response:
    if not await _guard(lambda: _service(request).delete(user.user_id, media_id)):
        raise GenerationError(404, "generated_not_found")
    return Response(status_code=204)


@router.get(
    "/admin/generated",
    response_model=AdminGeneratedList,
    summary="Ready generated images waiting for approval (admin)",
    responses={**_ERRORS, **_ADMIN_ERRORS},
)
async def admin_list_generated(
    request: Request,
    admin: AdminUser,
    pending: Annotated[bool, Query(description="Only items not yet approved (the default list)")] = True,
) -> AdminGeneratedList:
    del admin, pending  # approved items are listed in S19's queues; this list is the pending one
    return AdminGeneratedList(items=await _guard(lambda: _service(request).list_pending()))


@router.post(
    "/admin/generated/{media_id}/approve",
    response_model=GeneratedApproveOut,
    summary="Publish a generated image as a shared chapter figure (admin)",
    responses={**_ERRORS, **_ADMIN_ERRORS},
)
async def admin_approve_generated(
    request: Request, media_id: MediaId, body: GeneratedApproveBody, admin: AdminUser
) -> GeneratedApproveOut:
    out = await _guard(lambda: _service(request).approve(media_id, admin.user_id, body.section_id, body.caption))
    log.info("generated_media_approved")
    return out


stub_router = APIRouter(prefix="/api", tags=["generation"], include_in_schema=False)


@stub_router.post("/generated/stub", response_model=GeneratedItem)
async def stub_generate(
    request: Request, body: StubRequest, user: Annotated[CurrentUser, limited("generation_write")]
) -> GeneratedItem:
    """Registered only when GENERATION_FAKE is on outside production (e2e, local): same service, fake client."""
    return await _service(request).run(user.user_id, body.kind, body.prompt, body.seconds)


@stub_router.get("/generated/stub-file/{bucket}/{path:path}")
async def stub_file(request: Request, bucket: str, path: str) -> Response:
    """Serves the in-memory store's bytes from the app's own origin (the CSP allows it), only in the stub."""
    store = _service(request)._store
    try:
        data, mime = store.objects[(bucket, path)]  # type: ignore[attr-defined]
    except (KeyError, AttributeError):
        return Response(status_code=404)
    return Response(content=data, media_type=mime, headers={"cache-control": "no-store"})


async def _generation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, GenerationError)
    return error_response(exc.status, exc.code, exc.code)


def install_generation_routes(app: Any) -> None:
    from .factory import build_generation_service

    settings = app.state.settings
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
    app.state.generation = build_generation_service(settings, app.state.db, app.state.app_config)
    app.add_exception_handler(GenerationError, _generation_error)
    app.include_router(router)
    if settings.generation_fake and settings.app_env != "prod":
        app.include_router(stub_router)


__all__ = ["install_generation_routes", "router"]
