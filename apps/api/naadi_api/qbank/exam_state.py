"""`active_exam`: the caller's timed/exam session that is running, paused or on a break, if any.

Read by the shell (exam chrome, assistant hidden) and by the assistant's guard (segment 14: the tutor graph
refuses while an exam-mode session is active). Runs with the privileged role and filters on `user_id`; it
advances the clock like every other timed request, so an expired session is never reported as active.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

import psycopg

from . import clock, exam
from .clock import Session

ACTIVE_STATES = ("running", "paused", "break")


async def active_exam(
    conn: psycopg.AsyncConnection[Any],
    user_id: uuid.UUID,
    *,
    rules: dict[str, Any],
    now: dt.datetime | None = None,
    modes: tuple[str, ...] = ("timed", "exam"),
) -> tuple[uuid.UUID, Literal["timed", "exam"], Session] | None:
    """Newest first; the first session whose clock is running, paused or on a break."""
    at = now or exam.default_now()
    cur = await conn.execute(
        "select distinct b.id, b.created_at from public.blocks b join public.block_sections s"
        " on s.block_id = b.id and s.user_id = b.user_id and s.ended_at is null"
        " where b.user_id = %s and b.finished_at is null and b.config->>'mode' = any(%s)"
        " order by b.created_at desc limit 5",
        (user_id, list(modes)),
    )
    candidates = [r["id"] if isinstance(r, dict) else r[0] for r in await cur.fetchall()]
    for block_id in candidates:
        block = await exam.lock_block(conn, block_id, user_id)
        if block is None:
            continue
        session = await exam.sync(conn, block, user_id, rules, at)
        if clock.snapshot(session, at).state in ACTIVE_STATES:
            mode: Literal["timed", "exam"] = "exam" if block["config"]["mode"] == "exam" else "timed"
            return block_id, mode, session
    return None


__all__ = ["ACTIVE_STATES", "active_exam"]
