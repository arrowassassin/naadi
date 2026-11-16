"""Timed and exam blocks: the database side of the session clock, answers, gating and grading (segment 09).

Everything here runs with the pool's privileged role inside one transaction that holds the block row
(`select ... for update`), and every statement filters on the verified caller's id. The rules:

- Timing lives in `block_sections` (select-only for clients). `sync` loads the rows, applies the clock
  (`clock.advance`), writes back what changed and grades the block the moment its last section ends.
- Answers are recorded but never graded while the block runs (`attempts.correct` stays NULL), so neither the
  answer columns nor the attempt-gated flashcards open before the whole block is finished.
- Questions are served only for the section that is running; answers and explanations only once the whole
  block is finished.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any

import psycopg

from ..errors import CodedHTTPException
from . import clock
from .clock import ClockError, Section, Session
from .exam_models import BlockClock, SectionLayout

Row = dict[str, Any]
Now = Callable[[], dt.datetime]

# 409 codes the client maps to ui_copy (practice.exam.error.<code>).
CONFLICT_MESSAGES = {
    "pause_exam_mode": "Pause is off in exam mode.",
    "not_started": "This section has not started.",
    "not_current": "This section is not the current one.",
    "section_ended": "This section has ended. Answers can no longer change.",
    "paused": "The block is paused. Resume to continue.",
    "on_break": "You are on a break. Start the next section to continue.",
    "finished": "This block is finished.",
    "no_such_section": "This block has no such section.",
    "not_timed": "This block is not timed.",
    "not_finished": "Results are ready once the whole block is finished.",
}


def conflict(code: str) -> CodedHTTPException:
    return CodedHTTPException(409, code, CONFLICT_MESSAGES.get(code, "This action is not possible now."))


def default_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def is_timed(cfg_mode: str) -> bool:
    return cfg_mode in ("timed", "exam")


def grace_ms(rules: dict[str, Any]) -> int:
    return int(rules.get("expiry_grace_seconds", 2)) * 1000


def break_s(rules: dict[str, Any]) -> int:
    return int(rules.get("break_minutes", 45)) * 60


def plan_sections(count: int, sections: int, seconds_per_section: int) -> list[SectionLayout]:
    return [
        SectionLayout(n=i + 1, first_index=i * count, count=count, time_limit_s=seconds_per_section)
        for i in range(sections)
    ]


# ── Loading and saving ──

_SECTIONS = """
select n, first_index, count, time_limit_s, started_at, deadline_at, paused_at, paused_total_ms, ended_at,
  ended_reason
from public.block_sections where block_id = %s and user_id = %s order by n
"""


async def _rows(conn: psycopg.AsyncConnection[Any], query: str, params: tuple[Any, ...] = ()) -> list[Row]:
    cur = await conn.execute(query, params)
    return [dict(r) for r in await cur.fetchall()]


def session_of(cfg: dict[str, Any], break_used_ms: int, rows: list[Row], rules: dict[str, Any], total: int) -> Session:
    """Build the clock session from the block's layout and its rows; a layout section without a row is missing."""
    by_n = {int(r["n"]): r for r in rows}
    sections: list[Section] = []
    # A timed block without a layout (written before segment 09) is one section with no row: expired.
    layouts = cfg.get("sections") or [{"n": 1, "first_index": 0, "count": max(1, total), "time_limit_s": 1}]
    for lay in layouts:
        r = by_n.get(int(lay["n"]))
        base = Section(
            n=int(lay["n"]),
            first_index=int(lay["first_index"]),
            count=int(lay["count"]),
            time_limit_s=int(lay["time_limit_s"]),
        )
        if r is None:
            base.missing = True
        else:
            base.started_at = r["started_at"]
            base.deadline_at = r["deadline_at"]
            base.paused_at = r["paused_at"]
            base.paused_total_ms = int(r["paused_total_ms"])
            base.ended_at = r["ended_at"]
            base.ended_reason = r["ended_reason"]
        sections.append(base)
    return Session(
        sections=sections,
        break_s=int(cfg.get("break_s") or 0),
        break_used_ms=int(break_used_ms),
        pausable=cfg.get("mode") == "timed",
        grace_ms=grace_ms(rules),
    )


async def save(conn: psycopg.AsyncConnection[Any], block_id: uuid.UUID, user_id: uuid.UUID, session: Session) -> None:
    for s in session.sections:
        if s.n not in session.changed and not s.missing:
            continue
        await conn.execute(
            """
            insert into public.block_sections (block_id, user_id, n, first_index, count, time_limit_s, started_at,
              deadline_at, paused_at, paused_total_ms, ended_at, ended_reason)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (block_id, n) do update set started_at = excluded.started_at,
              deadline_at = excluded.deadline_at, paused_at = excluded.paused_at,
              paused_total_ms = excluded.paused_total_ms, ended_at = excluded.ended_at,
              ended_reason = excluded.ended_reason
            where block_sections.user_id = excluded.user_id
            """,
            (
                block_id,
                user_id,
                s.n,
                s.first_index,
                s.count,
                s.time_limit_s,
                s.started_at,
                s.deadline_at,
                s.paused_at,
                s.paused_total_ms,
                s.ended_at,
                s.ended_reason,
            ),
        )
        s.missing = False
    if session.break_changed:
        await conn.execute(
            "update public.blocks set break_used_ms = %s where id = %s and user_id = %s",
            (session.break_used_ms, block_id, user_id),
        )
    session.changed.clear()
    session.break_changed = False


async def create_sections(
    conn: psycopg.AsyncConnection[Any], block_id: uuid.UUID, user_id: uuid.UUID, layout: list[SectionLayout]
) -> None:
    for lay in layout:
        await conn.execute(
            "insert into public.block_sections (block_id, user_id, n, first_index, count, time_limit_s)"
            " values (%s, %s, %s, %s, %s, %s)",
            (block_id, user_id, lay.n, lay.first_index, lay.count, lay.time_limit_s),
        )


_LOCK = (
    "select id, user_id, config, question_ids, started_at, finished_at, score, created_at, break_used_ms"
    " from public.blocks where id = %s and user_id = %s for update"
)


async def lock_block(conn: psycopg.AsyncConnection[Any], block_id: uuid.UUID, user_id: uuid.UUID) -> Row | None:
    rows = await _rows(conn, _LOCK, (block_id, user_id))
    return rows[0] if rows else None


async def sync(
    conn: psycopg.AsyncConnection[Any],
    block: Row,
    user_id: uuid.UUID,
    rules: dict[str, Any],
    now: dt.datetime,
    *,
    grade_now: bool = True,
) -> Session:
    """Load, advance and persist the session; grade the block when its last section has ended (unless
    `grade_now` is false: an answer inside the grace window is recorded first, then `persist` grades)."""
    rows = await _rows(conn, _SECTIONS, (block["id"], user_id))
    session = session_of(block["config"], block.get("break_used_ms") or 0, rows, rules, len(block["question_ids"]))
    clock.advance(session, now)
    if grade_now:
        await persist(conn, block, user_id, session, now)
    else:
        await save(conn, block["id"], user_id, session)
    return session


async def persist(
    conn: psycopg.AsyncConnection[Any], block: Row, user_id: uuid.UUID, session: Session, now: dt.datetime
) -> None:
    await save(conn, block["id"], user_id, session)
    if session.finished and block["finished_at"] is None:
        ended = max((s.ended_at for s in session.sections if s.ended_at is not None), default=now)
        block["score"] = await grade(conn, block, user_id, ended)
        block["finished_at"] = ended


async def apply(
    conn: psycopg.AsyncConnection[Any],
    block: Row,
    user_id: uuid.UUID,
    rules: dict[str, Any],
    now: dt.datetime,
    action: Callable[[Session], None],
) -> Session:
    """`sync`, then one student transition (start/pause/resume/end/finish), persisted; ClockError -> 409."""
    session = await sync(conn, block, user_id, rules, now)
    try:
        action(session)
    except ClockError as exc:
        raise conflict(exc.code) from exc
    await persist(conn, block, user_id, session, now)
    return session


def clock_out(session: Session, now: dt.datetime) -> BlockClock:
    snap = clock.snapshot(session, now)
    return BlockClock(
        server_now=snap.server_now,
        state=snap.state,
        section_n=snap.section_n,
        sections=snap.sections,
        deadline_at=snap.deadline_at,
        remaining_ms=snap.remaining_ms,
        break_remaining_ms=snap.break_remaining_ms,
        paused=snap.paused,
        time_limit_ms=snap.time_limit_ms,
        break_left_ms=snap.break_remaining_ms if snap.break_remaining_ms is not None else session.break_left_ms,
        pausable=session.pausable,
        grace_ms=session.grace_ms,
    )


# ── Grading ──


async def grade(conn: psycopg.AsyncConnection[Any], block: Row, user_id: uuid.UUID, finished_at: dt.datetime) -> float:
    """Grade every answer of the block, record unanswered questions as omitted (wrong), set score and finish."""
    bid = block["id"]
    mode = block["config"]["mode"]
    await conn.execute(
        "update public.attempts a set correct = (a.chosen = q.answer) from public.questions q"
        " where a.block_id = %s and a.user_id = %s and q.id = a.question_id and a.chosen is not null",
        (bid, user_id),
    )
    # A flag-only row of a question never answered: omitted counts as a miss (the flashcard gate stays closed:
    # it needs a choice).
    await conn.execute(
        "update public.attempts set correct = false where block_id = %s and user_id = %s and chosen is null",
        (bid, user_id),
    )
    await conn.execute(
        "insert into public.attempts (user_id, question_id, block_id, chosen, correct, flagged, mode)"
        " select %s, q.qid, %s, null, false, false, %s from unnest(%s::text[]) as q(qid)"
        " where exists (select 1 from public.questions x where x.id = q.qid)"
        " and not exists (select 1 from public.attempts a where a.block_id = %s and a.question_id = q.qid)",
        (user_id, bid, mode, list(block["question_ids"]), bid),
    )
    rows = await _rows(
        conn,
        "select count(*) filter (where correct) as right from public.attempts where block_id = %s and user_id = %s",
        (bid, user_id),
    )
    total = len(block["question_ids"])
    score = round(100.0 * int(rows[0]["right"]) / total, 1) if total else 0.0
    await conn.execute(
        "update public.blocks set finished_at = %s, score = %s where id = %s and user_id = %s",
        (finished_at, score, bid, user_id),
    )
    return score


# ── Answers ──


async def record_attempt(
    conn: psycopg.AsyncConnection[Any],
    block: Row,
    user_id: uuid.UUID,
    session: Session,
    question_id: str,
    chosen: str,
    seconds: int,
    flagged: bool,
    now: dt.datetime,
) -> Row:
    """Record (or change) the answer to a question of the running section. Never grades; returns the row."""
    ids: list[str] = list(block["question_ids"])
    index = ids.index(question_id)
    try:
        section = clock.answer_window(session, index, now)
    except ClockError as exc:
        raise conflict(exc.code) from exc
    secs = max(0, min(seconds, clock.elapsed_s(section, now)))
    mode = block["config"]["mode"]
    existing = await _rows(
        conn,
        "select id, chosen, first_chosen, changes from public.attempts"
        " where block_id = %s and question_id = %s and user_id = %s",
        (block["id"], question_id, user_id),
    )
    if not existing:
        return (
            await _rows(
                conn,
                "insert into public.attempts (user_id, question_id, block_id, chosen, first_chosen, changes, seconds,"
                " flagged, mode, answered_at) values (%s, %s, %s, %s, %s, 0, %s, %s, %s, %s)"
                " returning id, chosen, first_chosen, changes, seconds, flagged",
                (user_id, question_id, block["id"], chosen, chosen, secs, flagged, mode, now),
            )
        )[0]
    row = existing[0]
    if row["chosen"] is None:
        changes, first = 0, chosen
    else:
        changes = int(row["changes"]) + (1 if row["chosen"] != chosen else 0)
        first = row["first_chosen"] or row["chosen"]
    return (
        await _rows(
            conn,
            "update public.attempts set chosen = %s, first_chosen = %s, changes = %s, seconds = %s,"
            " flagged = flagged or %s, answered_at = %s, mode = %s where id = %s"
            " returning id, chosen, first_chosen, changes, seconds, flagged",
            (chosen, first, changes, secs, flagged, now, mode, row["id"]),
        )
    )[0]


def check_flag(session: Session, index: int, finished: bool, now: dt.datetime) -> None:
    """Flags may change while the question's section runs, and in review once the whole block is finished."""
    if finished:
        return
    try:
        clock.answer_window(session, index, now)
    except ClockError as exc:
        raise conflict(exc.code) from exc


def question_gate(session: Session, index: int, finished: bool) -> int:
    """Serve question `index` only while its section runs (or once the block is finished); returns its section."""
    s = session.section_of(index)
    if s is None:
        raise conflict("no_such_section")
    if finished:
        return s.n
    current = session.current()
    if current is None:
        raise conflict("finished")
    if current is not s:
        raise conflict("section_ended" if s.ended else "not_current")
    if not s.started:
        prev = session.previous(s)
        raise conflict("on_break" if prev is not None else "not_started")
    if s.paused_at is not None:
        raise conflict("paused")
    return s.n


def mock_layout(question_ids: list[str], blocks: list[int], minutes_per_block: int) -> list[SectionLayout]:
    """Sections of a mock: consecutive runs of the seeded `block` number, in order (block 1 then 2)."""
    out: list[SectionLayout] = []
    first = 0
    i = 0
    while i < len(question_ids):
        b = blocks[i]
        j = i
        while j < len(question_ids) and blocks[j] == b:
            j += 1
        out.append(SectionLayout(n=len(out) + 1, first_index=first, count=j - i, time_limit_s=minutes_per_block * 60))
        first += j - i
        i = j
    return out
