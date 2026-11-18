"""`/api/admin/youtube`: the candidate queue for admins (the console of segment 19 renders it).

Admin-gated by `require_admin` on the server; every response carries codes and counts, never English sentences.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse

from ..auth import AdminUser
from ..db import Database, DatabaseNotConfigured
from ..errors import ErrorResponse, error_response
from . import queue
from .client import GoogleYouTube, Quota, YouTubeApi, YouTubeError
from .models import ID_PATTERN, AddIn, ApproveIn, ApproveOut, CandidateOut, CandidatesOut, CandidateStatus

router = APIRouter(prefix="/api/admin/youtube", tags=["admin"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    403: {"model": ErrorResponse, "description": "Not an admin"},
    404: {"model": ErrorResponse, "description": "Candidate or section not found (video_* code)"},
    422: {"model": ErrorResponse, "description": "Not valid (video_* code)"},
    503: {"model": ErrorResponse, "description": "Database or YouTube unavailable (video_unavailable_service)"},
}
MediaId = Annotated[str, Path(pattern=ID_PATTERN)]


def _db(request: Request) -> Database:
    d: Database = request.app.state.db
    return d


def _api(request: Request) -> YouTubeApi:
    """A fresh client per add (20 units at most); tests put a fake on `app.state.youtube_api`."""
    fake = getattr(request.app.state, "youtube_api", None)
    if fake is not None:
        return fake  # type: ignore[no-any-return]
    key = request.app.state.settings.youtube_api_key
    if key is None:
        raise queue.QueueError(503, "video_unavailable_service")
    return GoogleYouTube(key.get_secret_value(), Quota(20))


@router.get(
    "/candidates",
    operation_id="listYoutubeCandidates",
    summary="Video candidates by status (pending first for review)",
    response_model=CandidatesOut,
    responses={**_ERRORS},
)
async def candidates(
    request: Request,
    admin: AdminUser,
    status: CandidateStatus = "pending",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> CandidatesOut:
    return await queue.list_candidates(_db(request), status=status, limit=limit, offset=offset)


@router.post(
    "/candidates/{media_id}/approve",
    operation_id="approveYoutubeCandidate",
    summary="Approve: students see the video in the section, optionally from start_s to end_s",
    response_model=ApproveOut,
    responses={**_ERRORS, 409: {"model": ErrorResponse, "description": "Video unavailable or rejected"}},
)
async def approve(request: Request, admin: AdminUser, media_id: MediaId, body: ApproveIn) -> ApproveOut:
    return await queue.approve(
        _db(request),
        media_id,
        reviewer=admin.user_id,
        section_id=body.section_id,
        start_s=body.start_s,
        end_s=body.end_s,
    )


@router.post(
    "/candidates/{media_id}/reject",
    operation_id="rejectYoutubeCandidate",
    summary="Reject: never proposed again; the stored title is dropped",
    status_code=204,
    responses={**_ERRORS},
)
async def reject(request: Request, admin: AdminUser, media_id: MediaId) -> None:
    await queue.reject(_db(request), media_id, reviewer=admin.user_id)


@router.post(
    "/add",
    operation_id="addYoutubeVideo",
    summary="Add a video by link (checked with the YouTube API: public and embeddable)",
    response_model=CandidateOut,
    status_code=201,
    responses={**_ERRORS, 409: {"model": ErrorResponse, "description": "Rejected earlier"}},
)
async def add(request: Request, admin: AdminUser, body: AddIn) -> CandidateOut:
    return await queue.add_by_url(
        _db(request),
        _api(request),
        body.url,
        body.section_id,
        reviewer=admin.user_id,
        approve_now=body.approve,
        start_s=body.start_s,
        end_s=body.end_s,
    )


async def _queue_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, queue.QueueError)
    return error_response(exc.status, exc.code, exc.code)


async def _service_error(_: Request, exc: Exception) -> JSONResponse:
    return error_response(503, "video_unavailable_service", "video_unavailable_service")


def install_youtube_routes(app: Any) -> None:
    app.add_exception_handler(queue.QueueError, _queue_error)
    app.add_exception_handler(YouTubeError, _service_error)
    app.add_exception_handler(DatabaseNotConfigured, _service_error)
    app.include_router(router)


__all__ = ["install_youtube_routes", "router"]
