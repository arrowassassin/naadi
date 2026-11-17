"""Admin seed job: run the importer inside the Space, so no database or service key ever leaves it.

    POST /api/admin/seed            {dry_run, only, force} -> 202 {run}
    GET  /api/admin/seed/runs       latest runs
    GET  /api/admin/seed/runs/{id}  one run with its report

The run is a `seed_runs` row (any API process can read its status) plus a worker thread in the process that
accepted it. At most one run is `running` (unique index + the importer's advisory lock). A `running` row left
by a process that died (lock free, row older than STALE_AFTER) is marked failed before a new run starts.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid
from typing import Annotated, Any, Literal, cast

import psycopg
from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ..auth import AdminUser
from ..db import Database
from ..embeddings.jobs import schedule_reindex
from ..errors import ErrorResponse
from ..logs import get_logger
from ..scrub import scrub
from ..settings import Settings
from .cli import storage_target
from .data import SeedDataError, default_data_dir
from .importer import GROUPS, LOCK_KEY, SeedFailed, SeedLocked, SeedOptions, run_seed

log = get_logger("naadi.seed.job")

STALE_AFTER = dt.timedelta(minutes=15)
DB_DEADLINE_S = 5.0

router = APIRouter(prefix="/api/admin/seed", tags=["admin"])

Group = Literal["sources", "taxonomy", "figures", "chapters", "questions", "cards", "pathway", "plan", "app_config"]


class SeedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool = Field(default=True, description="Write nothing; report what a run would change")
    only: list[Group] = Field(default_factory=lambda: [cast(Group, g) for g in GROUPS], min_length=1)
    force: bool = Field(default=False, description="Also overwrite admin-edited sections and app_config keys")


class SeedRun(BaseModel):
    id: uuid.UUID
    status: Literal["running", "succeeded", "failed"]
    options: dict[str, JsonValue]
    started_by: uuid.UUID | None
    started_at: dt.datetime
    finished_at: dt.datetime | None
    error: str | None
    report: dict[str, JsonValue] | None = Field(description="The verification report (counts, checks)")


class SeedRunList(BaseModel):
    items: list[SeedRun]


_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    403: {"model": ErrorResponse, "description": "Not an admin"},
    503: {"model": ErrorResponse, "description": "Database unavailable"},
}

_COLS = "id, status, options, started_by, started_at, finished_at, error, report"


async def _conn(db: Database) -> Any:
    if not db.configured:
        raise HTTPException(503, "The database is not configured.")
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    return db.pool.connection(timeout=DB_DEADLINE_S)


async def _mark_stale(conn: psycopg.AsyncConnection[Any]) -> None:
    """Fail `running` rows whose process is gone (nobody holds the import lock)."""
    cur = await conn.execute("select pg_try_advisory_lock(%s)", (LOCK_KEY,))
    row = await cur.fetchone()
    if not row or not row[0]:
        return  # an import is really running somewhere
    try:
        await conn.execute(
            "update public.seed_runs set status = 'failed', finished_at = now(),"
            " error = 'interrupted: the API process stopped during the run'"
            " where status = 'running' and started_at < now() - %s",
            (STALE_AFTER,),
        )
    finally:
        await conn.execute("select pg_advisory_unlock(%s)", (LOCK_KEY,))


def _execute(settings: Settings, req: SeedRequest) -> tuple[str, dict[str, Any] | None, str | None]:
    """Run the import synchronously (worker thread). Returns (status, report, error)."""
    if settings.database_url is None:
        return "failed", None, "DATABASE_URL is not set"
    storage = storage_target(settings, None)
    if "figures" in req.only and storage is None:
        return "failed", None, "figures need SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY on the Space"
    opts = SeedOptions(
        database_url=settings.database_url.get_secret_value(),
        data_dir=default_data_dir(),
        config_dir=settings.config_dir,
        storage=storage,
        dry_run=req.dry_run,
        only=tuple(req.only),
        force=req.force,
    )
    secrets = settings.secret_values()
    try:
        report = run_seed(opts)
    except SeedFailed as exc:
        return "failed", exc.report.model_dump(mode="json"), scrub(str(exc), secrets, max_len=2000)
    except SeedDataError as exc:
        return "failed", None, scrub("invalid seed bundle: " + "; ".join(exc.problems), secrets, max_len=2000)
    except SeedLocked as exc:
        return "failed", None, str(exc)
    except Exception as exc:  # recorded on the run; the job must always finish its row
        log.exception("seed_job_crashed", error=type(exc).__name__)
        return "failed", None, scrub(f"{type(exc).__name__}: {exc}", secrets, max_len=2000)
    return "succeeded", report.model_dump(mode="json"), None


async def _run(app: Any, run_id: uuid.UUID, req: SeedRequest) -> None:
    settings: Settings = app.state.settings
    db: Database = app.state.db
    status, report, error = await asyncio.to_thread(_execute, settings, req)
    async with await _conn(db) as conn:
        await conn.execute(
            "update public.seed_runs set status = %s, finished_at = now(), report = %s::jsonb, error = %s"
            " where id = %s",
            (status, json.dumps(report) if report is not None else None, error, run_id),
        )
    if status == "succeeded" and not req.dry_run and "app_config" in req.only:
        svc = getattr(app.state, "app_config", None)
        if svc is not None:
            svc.invalidate()
    if status == "succeeded" and not req.dry_run and {"chapters", "questions", "pathway"} & set(req.only):
        # Search follows the content: reconcile the index (unchanged chunks cost a read, not a model call).
        schedule_reindex(app)
    log.info("seed_job_finished", run_id=str(run_id), status=status, dry_run=req.dry_run)


def _tasks(app: Any) -> set[asyncio.Task[None]]:
    """Running job tasks of this process (strong references, so they are not garbage-collected)."""
    tasks: set[asyncio.Task[None]] | None = getattr(app.state, "seed_tasks", None)
    if tasks is None:
        tasks = set()
        app.state.seed_tasks = tasks
    return tasks


def _run_model(row: dict[str, Any]) -> SeedRun:
    return SeedRun.model_validate(row)


@router.post(
    "",
    operation_id="startSeed",
    summary="Admin only: start a seed import job in the background (dry run by default)",
    status_code=202,
    response_model=SeedRun,
    responses={**_ERRORS, 409: {"model": ErrorResponse, "description": "A seed job is already running"}},
)
async def start_seed(request: Request, admin: AdminUser, body: SeedRequest) -> JSONResponse:
    limiters = getattr(request.app.state, "rate_limiters", None)
    if limiters and "admin" in limiters:
        limiters["admin"].hit(str(admin.user_id))
    db: Database = request.app.state.db
    try:
        async with await _conn(db) as conn:
            await _mark_stale(conn)
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                f"insert into public.seed_runs (started_by, options) values (%s, %s::jsonb) returning {_COLS}",  # noqa: S608 - fixed column list
                (admin.user_id, body.model_dump_json()),
            )
            row = await cur.fetchone()
    except psycopg.errors.UniqueViolation as exc:
        raise HTTPException(409, "A seed job is already running. Wait for it to finish.") from exc
    except (psycopg.OperationalError, TimeoutError) as exc:
        raise HTTPException(503, "The database cannot be reached right now. Try again in a moment.") from exc
    assert row is not None
    run = _run_model(row)
    tasks = _tasks(request.app)
    task = asyncio.create_task(_run(request.app, run.id, body), name=f"seed-{run.id}")
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    log.info("seed_job_started", run_id=str(run.id), dry_run=body.dry_run, groups=body.only)
    return JSONResponse(run.model_dump(mode="json"), status_code=202)


@router.get(
    "/runs",
    operation_id="listSeedRuns",
    summary="Admin only: the latest seed jobs, newest first",
    response_model=SeedRunList,
    responses=_ERRORS,
)
async def list_runs(request: Request, admin: AdminUser) -> SeedRunList:
    try:
        async with await _conn(request.app.state.db) as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(f"select {_COLS} from public.seed_runs order by started_at desc limit 20")  # noqa: S608
            rows = await cur.fetchall()
    except (psycopg.OperationalError, TimeoutError) as exc:
        raise HTTPException(503, "The database cannot be reached right now. Try again in a moment.") from exc
    return SeedRunList(items=[_run_model(r) for r in rows])


@router.get(
    "/runs/{run_id}",
    operation_id="getSeedRun",
    summary="Admin only: one seed job with its report",
    response_model=SeedRun,
    responses={**_ERRORS, 404: {"model": ErrorResponse, "description": "No such run"}},
)
async def get_run(request: Request, admin: AdminUser, run_id: Annotated[uuid.UUID, Path()]) -> SeedRun:
    try:
        async with await _conn(request.app.state.db) as conn:
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(f"select {_COLS} from public.seed_runs where id = %s", (run_id,))  # noqa: S608
            row = await cur.fetchone()
    except (psycopg.OperationalError, TimeoutError) as exc:
        raise HTTPException(503, "The database cannot be reached right now. Try again in a moment.") from exc
    if row is None:
        raise HTTPException(404, "No such seed run.")
    return _run_model(row)


async def drain_seed_tasks(app: Any, timeout_s: float = 10.0) -> None:
    """On shutdown: give running jobs a moment; an unfinished one is marked failed by the next start."""
    tasks = _tasks(app)
    if tasks:
        await asyncio.wait(tasks, timeout=timeout_s)
