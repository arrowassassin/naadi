"""Export-all API (segment 21), for the signed-in student:

    POST   /api/export          start the job ({include_vault}); 202 with the job. One active job at a time (409).
    GET    /api/export          the latest jobs, ready ones with signed download URLs (24 hours)
    GET    /api/export/{id}     one job (poll this while it is queued or running)
    DELETE /api/export/{id}     remove a finished job and its files

The ZIP is built on the server (`service.py`); notes and notebooks as PDFs are made on the device by S13's exporter
in the same flow. Errors carry a stable `code`; the web words them with `ui_copy`.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from ..auth import AccountUser
from ..auth_db import user_tx
from ..auth_limits import RateLimiter
from ..db import Database, DatabaseNotConfigured
from ..errors import ErrorResponse, error_response
from ..logs import get_logger
from .service import Buckets, ExportError, ExportService
from .storage import ExportStorage, target_from

log = get_logger("naadi.export.routes")
router = APIRouter(prefix="/api", tags=["export"])
GC_EVERY_S = 900
JOB_COLUMNS = "id, user_id, status, include_vault, storage_path, parts, bytes, expires_at, error, created_at"
JobId = Annotated[uuid.UUID, Path()]
_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "export_unavailable"},
}


class ExportStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_vault: bool = False


class ExportDownload(BaseModel):
    part: int
    url: str


class ExportJob(BaseModel):
    id: uuid.UUID
    status: Literal["queued", "running", "ready", "failed", "expired"]
    include_vault: bool
    parts: int
    bytes: int | None
    expires_at: dt.datetime | None
    error: str | None
    created_at: dt.datetime
    downloads: list[ExportDownload]


class ExportList(BaseModel):
    items: list[ExportJob]


def default_limiters() -> dict[str, RateLimiter]:
    return {"export_start": RateLimiter(6, 3600.0), "export_read": RateLimiter(240, 60.0)}


def _db(request: Request) -> Database:
    d: Database = request.app.state.db
    return d


async def _service(request: Request) -> ExportService:
    svc: ExportService | None = getattr(request.app.state, "export_service", None)
    if svc is None:
        svc = ExportService(_db(request), ExportStorage(target_from(request.app.state.settings)), Buckets("", "", ""))
        request.app.state.export_service = svc
    cfg = await request.app.state.app_config.get("storage")
    svc.buckets = Buckets(cfg["user_uploads"], cfg["generated_media"], cfg["vault"])
    return svc


async def _job(svc: ExportService, row: dict[str, Any]) -> ExportJob:
    downloads = await svc.downloads(row)
    return ExportJob(
        id=row["id"],
        status=row["status"],
        include_vault=row["include_vault"],
        parts=row["parts"],
        bytes=row["bytes"],
        expires_at=row["expires_at"],
        error=row["error"],
        created_at=row["created_at"],
        downloads=[ExportDownload(**d) for d in downloads],
    )


def _limit(request: Request, bucket: str, user_id: uuid.UUID) -> None:
    request.app.state.rate_limiters[bucket].hit(str(user_id))


@router.post(
    "/export",
    operation_id="startExport",
    status_code=202,
    response_model=ExportJob,
    responses={**_ERRORS, 409: {"model": ErrorResponse, "description": "export_active"}},
)
async def start_export(request: Request, user: AccountUser, body: ExportStart) -> ExportJob:
    _limit(request, "export_start", user.user_id)
    svc = await _service(request)
    row = await svc.start(user.user_id, body.include_vault)
    return await _job(svc, row)


@router.get("/export", operation_id="listExports", response_model=ExportList, responses=_ERRORS)
async def list_exports(request: Request, user: AccountUser) -> ExportList:
    _limit(request, "export_read", user.user_id)
    svc = await _service(request)
    try:
        async with user_tx(_db(request), user) as conn:
            cur = await conn.execute(
                f"select {JOB_COLUMNS} from public.export_jobs order by created_at desc limit 5"  # noqa: S608
            )
            rows = await cur.fetchall()
    except DatabaseNotConfigured as exc:
        raise ExportError(503, "export_unavailable") from exc
    return ExportList(items=[await _job(svc, r) for r in rows])


@router.get(
    "/export/{job_id}",
    operation_id="getExport",
    response_model=ExportJob,
    responses={**_ERRORS, 404: {"model": ErrorResponse, "description": "export_not_found"}},
)
async def get_export(request: Request, user: AccountUser, job_id: JobId) -> ExportJob:
    _limit(request, "export_read", user.user_id)
    svc = await _service(request)
    try:
        async with user_tx(_db(request), user) as conn:
            cur = await conn.execute(f"select {JOB_COLUMNS} from public.export_jobs where id = %s", (job_id,))  # noqa: S608
            row = await cur.fetchone()
    except DatabaseNotConfigured as exc:
        raise ExportError(503, "export_unavailable") from exc
    if row is None:
        raise ExportError(404, "export_not_found")
    return await _job(svc, row)


@router.delete(
    "/export/{job_id}",
    operation_id="deleteExport",
    status_code=204,
    responses={**_ERRORS, 404: {"model": ErrorResponse, "description": "export_not_found"}},
)
async def delete_export(request: Request, user: AccountUser, job_id: JobId) -> Response:
    _limit(request, "export_read", user.user_id)
    svc = await _service(request)
    if not await svc.delete(user.user_id, job_id):
        raise ExportError(404, "export_not_found")
    return Response(status_code=204)


async def _export_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ExportError)
    return error_response(exc.status, exc.code, exc.code)


async def _gc_loop(app: Any) -> None:
    while True:
        await asyncio.sleep(GC_EVERY_S)
        svc: ExportService | None = getattr(app.state, "export_service", None)
        if svc is None:
            continue
        try:
            stats = await svc.gc()
            if any(stats.values()):
                log.info("export.gc", **stats)
        except Exception as exc:  # noqa: BLE001 - housekeeping must never stop the loop
            log.warning("export.gc_failed", error=type(exc).__name__)


async def start_export_gc(app: Any) -> None:
    if _db_of(app).configured:
        app.state.export_gc_task = asyncio.create_task(_gc_loop(app), name="export-gc")


def _db_of(app: Any) -> Database:
    d: Database = app.state.db
    return d


async def stop_export_gc(app: Any) -> None:
    task: asyncio.Task[None] | None = getattr(app.state, "export_gc_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        app.state.export_gc_task = None
    svc: ExportService | None = getattr(app.state, "export_service", None)
    if svc is not None:
        await svc.drain()


def install_export_routes(app: Any) -> None:
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
    app.add_exception_handler(ExportError, _export_error)
    app.include_router(router)


__all__ = ["install_export_routes", "router", "start_export_gc", "stop_export_gc"]
