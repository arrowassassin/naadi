"""`assistant_threads`: one row per Aegra thread (migration 0033), written by the tutor graph, listed, renamed and
deleted through `/api/assistant/threads`.

Past chats are kept until the student deletes them: Aegra's checkpointer TTL (`keep_latest`, aegra.json) prunes
older intermediate checkpoints but always keeps a thread's latest state. Deleting a thread cancels its active runs,
deletes its checkpoints and its Aegra thread row (owned by the same user), then this row.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..db import Database
from ..logs import get_logger

log = get_logger("naadi.assistant.threads")
TITLE_MAX = 80


def title_from(text: str) -> str | None:
    """The first question, one line, at most 80 characters (word boundary), without the quoted selection."""
    first = text.split("\n\nSelected text:", 1)[0]
    line = " ".join(first.split())
    if not line:
        return None
    if len(line) <= TITLE_MAX:
        return line
    cut = line[: TITLE_MAX - 1].rsplit(" ", 1)[0] or line[: TITLE_MAX - 1]
    return cut + "…"


async def _pool(db: Database) -> Any:
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    return db.pool


async def record_turn(
    db: Database,
    *,
    user_id: uuid.UUID,
    aegra_thread_id: uuid.UUID,
    title: str | None,
    page_ref: dict[str, str] | None,
    reflect_minutes: int | None,
) -> bool:
    """Insert or touch the thread's row (service role; filtered on the run's user). Returns True when the
    background memory pass is due for this thread (and claims it, so concurrent turns run it once)."""
    pool = await _pool(db)
    async with pool.connection(timeout=5.0) as conn, conn.transaction():
        cur = await conn.cursor(row_factory=dict_row).execute(
            "insert into public.assistant_threads (user_id, aegra_thread_id, title, page_ref, last_message_at)"
            " values (%s, %s, %s, %s, now())"
            " on conflict (aegra_thread_id) do update set last_message_at = now()"
            " where assistant_threads.user_id = excluded.user_id"
            " returning id",
            (user_id, aegra_thread_id, title, Jsonb(page_ref) if page_ref else None),
        )
        if await cur.fetchone() is None:
            log.warning("assistant_thread.owner_mismatch")
            return False
        if reflect_minutes is None:  # the turn has just started: list the thread, nothing to reflect on yet
            return False
        cur = await conn.execute(
            "update public.assistant_threads set memory_reflected_at = now()"
            " where aegra_thread_id = %s and user_id = %s"
            " and (memory_reflected_at is null or memory_reflected_at < now() - make_interval(mins => %s))"
            " returning id",
            (aegra_thread_id, user_id, reflect_minutes),
        )
        return await cur.fetchone() is not None


async def list_threads(conn: Any, q: str | None, limit: int) -> list[dict[str, Any]]:
    """As the student (RLS): their threads, newest first, optionally filtered by title."""
    pattern = None
    if q and q.strip():
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    cur = await conn.execute(
        "select id, aegra_thread_id, title, created_at, last_message_at, page_ref from public.assistant_threads"
        " where aegra_thread_id is not null and (%(p)s::text is null or title ilike %(p)s)"
        " order by coalesce(last_message_at, created_at) desc, id limit %(n)s",
        {"p": pattern, "n": limit},
    )
    return [dict(r) for r in await cur.fetchall()]


async def rename(conn: Any, thread_id: uuid.UUID, title: str) -> dict[str, Any] | None:
    cur = await conn.execute(
        "update public.assistant_threads set title = %s where id = %s"
        " returning id, aegra_thread_id, title, created_at, last_message_at, page_ref",
        (title, thread_id),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def owned_aegra_id(conn: Any, thread_id: uuid.UUID) -> uuid.UUID | None:
    cur = await conn.execute("select aegra_thread_id from public.assistant_threads where id = %s", (thread_id,))
    row = await cur.fetchone()
    return row["aegra_thread_id"] if row else None


class RuntimeNotHosted(RuntimeError):
    """The Naadi app is running without the Aegra runtime (plain `naadi_api.main`)."""


async def delete_aegra_thread(user_id: uuid.UUID, aegra_thread_id: uuid.UUID) -> None:
    """Cancel the thread's active runs, delete its checkpoints and its Aegra row (only the same user's)."""
    try:
        from aegra_api.core.active_runs import active_runs
        from aegra_api.core.database import db_manager
        from aegra_api.core.orm import Run as RunORM
        from aegra_api.core.orm import Thread as ThreadORM
        from aegra_api.services.streaming_service import streaming_service
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession
    except ImportError as exc:  # pragma: no cover - aegra is a dependency
        raise RuntimeNotHosted from exc
    try:
        engine = db_manager.get_engine()
        checkpointer = db_manager.get_checkpointer()
    except Exception as exc:
        raise RuntimeNotHosted from exc
    tid = str(aegra_thread_id)
    async with AsyncSession(engine) as session:
        thread = await session.scalar(
            select(ThreadORM).where(ThreadORM.thread_id == tid, ThreadORM.user_id == str(user_id))
        )
        runs = (
            await session.scalars(
                select(RunORM).where(
                    RunORM.thread_id == tid,
                    RunORM.user_id == str(user_id),
                    RunORM.status.in_(["pending", "running"]),
                )
            )
        ).all()
        for run in runs:
            await streaming_service.cancel_run(run.run_id)
            task = active_runs.pop(run.run_id, None)
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
        if thread is not None:
            await checkpointer.adelete_thread(tid)
            await session.delete(thread)
            await session.commit()


async def delete_row(db: Database, user_id: uuid.UUID, thread_id: uuid.UUID) -> None:
    pool = await _pool(db)
    async with pool.connection(timeout=5.0) as conn, conn.transaction():
        await conn.execute("delete from public.assistant_threads where id = %s and user_id = %s", (thread_id, user_id))


def iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None
