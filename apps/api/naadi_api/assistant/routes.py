"""`/api/assistant/*`: the thread list (rename, delete, search by title) and the memory list (Settings → Assistant).

Signed-in and account-checked; the `assistant` feature flag must be on (404 `assistant_off`); during a timed/exam
session every route answers 409 `exam_active` (the assistant is silent in exam mode, server-side). Thread rows are
read and renamed as the student (RLS); deletion also removes the Aegra thread and its checkpoints.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..auth import CurrentUser, get_account_user
from ..auth_limits import RateLimiter
from ..config_api.schemas import AssistantConfig
from ..config_api.service import AppConfigService
from ..db import Database
from ..errors import CodedHTTPException, ErrorResponse
from ..logs import get_logger
from ..qbank.exam_state import active_exam
from ..qbank.routes import as_service, as_user
from . import memory as mem
from . import threads

log = get_logger("naadi.assistant.routes")
router = APIRouter(prefix="/api/assistant", tags=["assistant"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    404: {"model": ErrorResponse, "description": "assistant_off, or not found"},
    409: {"model": ErrorResponse, "description": "exam_active: a timed/exam session is running"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "assistant_unavailable"},
}


class PageRefOut(BaseModel):
    route: str | None = None
    type: str | None = None
    id: str | None = None


class ThreadOut(BaseModel):
    id: uuid.UUID
    aegra_thread_id: uuid.UUID
    title: str | None
    created_at: datetime
    last_message_at: datetime | None
    page_ref: PageRefOut | None


class ThreadsOut(BaseModel):
    items: list[ThreadOut]


class ThreadRename(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def _one_line(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("empty title")
        return v


class MemoryOut(BaseModel):
    key: str
    fact: str
    created_at: datetime | None


class MemoriesOut(BaseModel):
    items: list[MemoryOut]
    suggestions: list[MemoryOut]
    max_items: int


class SuggestionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["keep", "discard"]


def default_limiters() -> dict[str, RateLimiter]:
    return {"assistant_read": RateLimiter(240, 60.0), "assistant_write": RateLimiter(60, 60.0)}


def _db(request: Request) -> Database:
    d: Database = request.app.state.db
    return d


def _config(request: Request) -> AppConfigService:
    c: AppConfigService = request.app.state.app_config
    return c


def gate(bucket: str) -> Any:
    """Signed in, rate-limited, assistant on for this user, no exam session running."""

    async def dep(request: Request, user: Annotated[CurrentUser, Depends(get_account_user)]) -> CurrentUser:
        limiters: dict[str, RateLimiter] = request.app.state.rate_limiters
        limiters[bucket].hit(str(user.user_id))
        cfg = _config(request)
        on = await cfg.feature_enabled("assistant", is_admin=user.is_admin, app_env=request.app.state.settings.app_env)
        if not on:
            raise CodedHTTPException(404, "assistant_off")
        rules = await cfg.get("exam_rules")

        async def check(conn: psycopg.AsyncConnection[Any]) -> bool:
            return await active_exam(conn, user.user_id, rules=rules) is not None

        if await as_service(_db(request), check):
            raise CodedHTTPException(409, "exam_active")
        return user

    return Depends(dep)


def _thread(r: dict[str, Any]) -> ThreadOut:
    ref = r.get("page_ref")
    return ThreadOut(
        id=r["id"],
        aegra_thread_id=r["aegra_thread_id"],
        title=r["title"],
        created_at=r["created_at"],
        last_message_at=r["last_message_at"],
        page_ref=PageRefOut.model_validate(ref) if isinstance(ref, dict) else None,
    )


@router.get("/threads", operation_id="listAssistantThreads", response_model=ThreadsOut, responses=_ERRORS)
async def list_threads(
    request: Request,
    user: Annotated[CurrentUser, gate("assistant_read")],
    q: Annotated[str | None, Query(max_length=120, description="Match in the title")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ThreadsOut:
    async def run(conn: psycopg.AsyncConnection[Any]) -> list[dict[str, Any]]:
        return await threads.list_threads(conn, q, limit)

    return ThreadsOut(items=[_thread(r) for r in await as_user(_db(request), user, run)])


@router.patch("/threads/{thread_id}", operation_id="renameAssistantThread", response_model=ThreadOut, responses=_ERRORS)
async def rename_thread(
    request: Request,
    thread_id: Annotated[uuid.UUID, Path()],
    body: ThreadRename,
    user: Annotated[CurrentUser, gate("assistant_write")],
) -> ThreadOut:
    async def run(conn: psycopg.AsyncConnection[Any]) -> dict[str, Any] | None:
        return await threads.rename(conn, thread_id, body.title)

    row = await as_user(_db(request), user, run)
    if row is None:
        raise CodedHTTPException(404, "not_found")
    return _thread(row)


@router.delete("/threads/{thread_id}", operation_id="deleteAssistantThread", status_code=204, responses=_ERRORS)
async def delete_thread(
    request: Request, thread_id: Annotated[uuid.UUID, Path()], user: Annotated[CurrentUser, gate("assistant_write")]
) -> Response:
    async def run(conn: psycopg.AsyncConnection[Any]) -> uuid.UUID | None:
        return await threads.owned_aegra_id(conn, thread_id)

    aegra_id = await as_user(_db(request), user, run)
    if aegra_id is None:
        raise CodedHTTPException(404, "not_found")
    try:
        await threads.delete_aegra_thread(user.user_id, aegra_id)
    except threads.RuntimeNotHosted as exc:
        raise CodedHTTPException(503, "assistant_unavailable") from exc
    await threads.delete_row(_db(request), user.user_id, thread_id)
    log.info("assistant_thread.deleted")
    return Response(status_code=204)


def _store() -> Any:
    try:
        from aegra_api.core.database import db_manager

        return db_manager.get_store()
    except Exception as exc:
        raise CodedHTTPException(503, "assistant_unavailable") from exc


async def _assistant_cfg(request: Request) -> AssistantConfig:
    return AssistantConfig.model_validate(await _config(request).get("assistant"))


def _mem(m: mem.Memory) -> MemoryOut:
    return MemoryOut(key=m.key, fact=m.fact, created_at=m.created_at)


@router.get("/memories", operation_id="listAssistantMemories", response_model=MemoriesOut, responses=_ERRORS)
async def list_memories(request: Request, user: Annotated[CurrentUser, gate("assistant_read")]) -> MemoriesOut:
    store = _store()
    cfg = await _assistant_cfg(request)
    items = await mem.list_memories(store, user.user_id)
    suggestions = await mem.list_memories(store, user.user_id, mem.SUGGESTIONS, limit=50)
    return MemoriesOut(
        items=[_mem(m) for m in items], suggestions=[_mem(m) for m in suggestions], max_items=cfg.memory_max_items
    )


@router.delete("/memories/{key}", operation_id="deleteAssistantMemory", status_code=204, responses=_ERRORS)
async def delete_memory(
    request: Request,
    key: Annotated[str, Path(min_length=36, max_length=36)],
    user: Annotated[CurrentUser, gate("assistant_write")],
) -> Response:
    if not await mem.forget(_store(), user.user_id, key):
        raise CodedHTTPException(404, "not_found")
    return Response(status_code=204)


@router.post(
    "/memories/suggestions/{key}", operation_id="decideAssistantMemorySuggestion", status_code=204, responses=_ERRORS
)
async def decide_suggestion(
    request: Request,
    key: Annotated[str, Path(min_length=36, max_length=36)],
    body: SuggestionDecision,
    user: Annotated[CurrentUser, gate("assistant_write")],
) -> Response:
    store = _store()
    if body.decision == "discard":
        ok = await mem.forget(store, user.user_id, key, mem.SUGGESTIONS)
    else:
        cfg = await _assistant_cfg(request)
        try:
            ok = await mem.keep_suggestion(store, user.user_id, key, max_items=cfg.memory_max_items)
        except mem.MemoryFull as exc:
            raise CodedHTTPException(409, "memory_full") from exc
        except ValueError as exc:
            raise CodedHTTPException(422, "memory_rejected") from exc
    if not ok:
        raise CodedHTTPException(404, "not_found")
    return Response(status_code=204)


def install_assistant_routes(app: Any) -> None:
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
    app.include_router(router)


__all__ = ["install_assistant_routes", "router"]
