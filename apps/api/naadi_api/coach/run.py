"""One coach pass: for each student with an active plan, compute facts, word up to three suggestions (one
`structured` LLM call through `naadi_api/llm.py`, one retry on a malformed reply), validate, store.

- Facts are read as the student (RLS, `auth.uid()` set to them); suggestions are written with the privileged
  role for that student only.
- Budget: the job purpose's daily reserve is enforced by the gateway. The first `BudgetExhausted` stops model
  calls for the rest of the pass; every remaining student still gets rules-based suggestions.
- Idempotent per (student, date): tonight's unanswered (`new`) rows are replaced; accepted or dismissed ones stay.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import psycopg
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb

from ..analytics.student import coach_facts
from ..auth_db import STATEMENT_TIMEOUT_MS, claims_json
from ..config_api.schemas import CoachRules, PlannerRules
from ..db import Database
from ..llm import CallContext, LLMUnavailable
from ..llm_budget import BudgetExhausted
from ..llm_privacy import PrivacyUnavailable
from ..logs import get_logger
from ..plan import planner_store
from .suggest import (
    SYSTEM_PROMPT,
    Candidate,
    InvalidOutput,
    Section,
    Written,
    candidates,
    parse_llm,
    prompt_payload,
    rules,
)

log = get_logger("naadi.coach")
ROLE = "structured"
MAX_LLM_ATTEMPTS = 2  # one call per student per night, plus one retry on a malformed reply


class Gateway(Protocol):
    async def ainvoke(self, role: str, messages: list[BaseMessage], ctx: CallContext, **kwargs: Any) -> Any: ...


@asynccontextmanager
async def student_conn(db: Database, user_id: uuid.UUID) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    """A transaction as `authenticated` with `auth.uid()` = the student: the nightly job reads exactly what the
    student could read. (A system job has no session to check, so this is not `user_tx`.)"""
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    async with db.pool.connection(timeout=10.0) as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]
        try:
            async with conn.transaction():
                await conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                await conn.execute("set local role authenticated")
                await conn.execute(
                    "select set_config('request.jwt.claims', %s, true), set_config('request.jwt.claim.sub', %s, true),"
                    " set_config('request.jwt.claim.role', 'authenticated', true)",
                    (claims_json(user_id), str(user_id)),
                )
                yield conn
        finally:
            conn.row_factory = tuple_row


@asynccontextmanager
async def service_conn(db: Database) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    async with db.pool.connection(timeout=10.0) as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]
        try:
            async with conn.transaction():
                await conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                yield conn
        finally:
            conn.row_factory = tuple_row


# Least recently coached first (never coached before anyone), so max_users_per_run rotates through everyone.
_STUDENTS = """
select p.user_id, p.timezone, p.exam_target, s.id as plan_id from public.profiles p
join public.study_plans s on s.user_id = p.user_id and s.active
order by s.last_coached_at nulls first, p.user_id limit %s
"""

# One model attempt per student per local date, whatever the number of runs (a second cron fire, a manual run,
# a retried pass): every call for that date carries the same run id in llm_usage.
_ATTEMPTED = "select exists (select 1 from public.llm_usage where user_id = %s and run_id = %s) as tried"
_HAS_LLM_ROWS = (
    "select exists (select 1 from public.coach_suggestions where user_id = %s and for_date = %s and source = 'llm')"
    " as has"
)


_TRY_LOCK = "select pg_try_advisory_xact_lock(hashtextextended(%s, 0)) as got"
_LOCK = "select pg_advisory_xact_lock(hashtextextended(%s, 0))"
_CLAIM = "select last_coached_at from public.study_plans where id = %s for update"
_MARK_COACHED = "update public.study_plans set last_coached_at = %s where id = %s"


def attempt_key(for_date: dt.date) -> str:
    return f"coach:{for_date.isoformat()}"


_SECTION = """
select s.chapter_id, s.id as section_id, s.heading from public.chapter_sections s
join public.chapters c on c.id = s.chapter_id
left join public.reading_positions rp on rp.section_id = s.id and rp.user_id = (select auth.uid())
where c.exam = %s and c.kind = 'chapter' and %s = any(s.taxonomy_ids)
order by (rp.completed_at is not null), c.sort nulls last, s.sort limit 1
"""


@dataclass
class PassStats:
    students: int = 0
    rows: int = 0
    llm_calls: int = 0
    llm_students: int = 0
    rules_students: int = 0
    dropped: int = 0
    budget_spent: bool = False
    errors: int = 0
    rebalances: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def note(self, reason: str) -> None:
        self.reasons[reason] = self.reasons.get(reason, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "students": self.students,
            "rows": self.rows,
            "llm_calls": self.llm_calls,
            "llm_students": self.llm_students,
            "rules_students": self.rules_students,
            "dropped": self.dropped,
            "budget_spent": self.budget_spent,
            "errors": self.errors,
            "rebalances": self.rebalances,
            "reasons": dict(sorted(self.reasons.items())),
        }


async def word(
    gateway: Gateway | None,
    offered: list[Candidate],
    *,
    user_id: uuid.UUID,
    run_id: str | None,
    limit: int,
    stats: PassStats,
) -> list[Written]:
    """Model-worded suggestions, or the rules wording when the model is unavailable, spent or invalid."""
    if gateway is None or stats.budget_spent:
        stats.note("budget_spent" if stats.budget_spent else "no_gateway")
        return rules(offered, limit=limit)
    ctx = CallContext(purpose="job", user_id=user_id, run_id=run_id)
    messages: list[BaseMessage] = [
        SystemMessage(SYSTEM_PROMPT.format(limit=limit)),
        HumanMessage(prompt_payload(offered)),
    ]
    for attempt in range(MAX_LLM_ATTEMPTS):
        try:
            stats.llm_calls += 1
            reply = await gateway.ainvoke(ROLE, messages, ctx)
        except BudgetExhausted as exc:
            # Only the spent daily job reserve ends model use for the rest of the pass. A rate limit (the
            # gateway has already waited its bounded rate_wait_s) or a turn cap affects this student only.
            if exc.reason == "daily_cap":
                stats.budget_spent = True
            stats.note(f"budget_{exc.reason}")
            return rules(offered, limit=limit)
        except (LLMUnavailable, PrivacyUnavailable) as exc:
            stats.note(type(exc).__name__)
            return rules(offered, limit=limit)
        try:
            kept, dropped = parse_llm(getattr(reply, "content", reply), offered, limit=limit)
        except InvalidOutput:
            stats.note("invalid_output")
            if attempt + 1 < MAX_LLM_ATTEMPTS:
                continue
            return rules(offered, limit=limit)
        stats.dropped += dropped
        if kept:
            stats.llm_students += 1
            return kept
        stats.note("all_dropped")
        return rules(offered, limit=limit)
    return rules(offered, limit=limit)


async def store(
    conn: psycopg.AsyncConnection[Any], user_id: uuid.UUID, for_date: dt.date, written: list[Written], *, limit: int
) -> int:
    await conn.execute(
        "delete from public.coach_suggestions where user_id = %s and for_date = %s and status = 'new'",
        (user_id, for_date),
    )
    cur = await conn.execute(
        "select count(*)::int as n from public.coach_suggestions where user_id = %s and for_date = %s",
        (user_id, for_date),
    )
    row: Any = await cur.fetchone()
    room = max(0, limit - int(row["n"]))
    n = 0
    for w in written[:room]:
        await conn.execute(
            "insert into public.coach_suggestions (user_id, for_date, kind, title, body, facts, action, source)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s)",
            (user_id, for_date, w.kind, w.title, w.body, Jsonb(w.facts), Jsonb(w.action), w.source),
        )
        n += 1
    return n


async def coach_student(
    db: Database,
    gateway: Gateway | None,
    rules_cfg: CoachRules,
    student: dict[str, Any],
    *,
    now: dt.datetime,
    run_id: str | None,
    stats: PassStats,
) -> int:
    uid: uuid.UUID = student["user_id"]
    try:
        zone = ZoneInfo(student["timezone"] or "UTC")
    except (KeyError, ValueError):
        zone = ZoneInfo("UTC")
    today = now.astimezone(zone).date()
    exam = student["exam_target"]
    async with student_conn(db, uid) as conn:
        facts = await coach_facts(
            conn,
            exam,
            now=now,
            today=today,
            window_days=rules_cfg.window_days,
            min_attempts=rules_cfg.min_attempts,
            weak_below=rules_cfg.weak_below,
        )
        section: Section | None = None
        if facts.weak_systems:
            cur = await conn.execute(_SECTION, (exam, facts.weak_systems[0][0]))
            found = await cur.fetchone()
            if found:
                section = Section(str(found["chapter_id"]), str(found["section_id"]), str(found["heading"]))
    offered = candidates(
        facts, catch_up_questions=rules_cfg.catch_up_questions, section=section, limit=rules_cfg.max_suggestions + 1
    )
    key = attempt_key(today)
    lock_key = f"coach:{uid}:{today.isoformat()}"
    # Two overlapping passes (a second cron fire, a manual run) must not both call the model for this student
    # tonight, and no transaction stays open while the model answers. So: (1) a short transaction under the
    # advisory lock on coach:<user>:<date> checks tonight's state and claims the student (last_coached_at), then
    # commits; (2) the model is called with no transaction open; (3) a second short transaction, under the same
    # lock, writes the rows unless another pass already wrote model-worded rows for the date.
    async with service_conn(db) as conn:
        got = await (await conn.execute(_TRY_LOCK, (lock_key,))).fetchone()
        if not (got and got["got"]):
            stats.note("locked")
            return 0
        plan = await (await conn.execute(_CLAIM, (student["plan_id"],))).fetchone()
        last = plan["last_coached_at"] if plan else None
        claimed = last is not None and last.astimezone(zone).date() == today
        tried = (await (await conn.execute(_ATTEMPTED, (uid, key))).fetchone() or {"tried": False})["tried"]
        has_llm = (await (await conn.execute(_HAS_LLM_ROWS, (uid, today))).fetchone() or {"has": False})["has"]
        await conn.execute(_MARK_COACHED, (now, student["plan_id"]))
    if has_llm:
        stats.note("already_coached")  # tonight's model-worded rows stand; no second call, nothing rewritten
        return 0
    if claimed:
        stats.note("claimed")  # an earlier or overlapping pass took this student tonight
        return 0
    if not offered:
        written: list[Written] = []
    elif tried:
        stats.note("llm_already_tried")  # tonight's attempt is in llm_usage and gave nothing usable
        written = rules(offered, limit=rules_cfg.max_suggestions)
    else:
        written = await word(gateway, offered, user_id=uid, run_id=key, limit=rules_cfg.max_suggestions, stats=stats)
    if written and written[0].source == "rules":
        stats.rules_students += 1
    async with service_conn(db) as conn:
        await conn.execute(_LOCK, (lock_key,))
        again = await (await conn.execute(_HAS_LLM_ROWS, (uid, today))).fetchone()
        if again and again["has"]:
            stats.note("written_by_other")
            return 0
        return await store(conn, uid, today, written, limit=rules_cfg.max_suggestions)


async def propose_rebalance(
    db: Database, user_id: uuid.UUID, rules: PlannerRules, *, now: dt.datetime, stats: PassStats
) -> None:
    """Missed items tonight: store a rebalance proposal (deterministic, no model call) for the planner screen and
    Today to show. Nothing is applied: the student confirms through the `planner` graph."""
    try:
        async with service_conn(db) as conn:
            row, _ = await planner_store.propose(conn, user_id, rules, trigger="coach", now=now)
        if row is not None:
            stats.rebalances += 1
    except (psycopg.Error, TimeoutError) as exc:
        stats.errors += 1
        log.warning("coach.rebalance_failed", error=type(exc).__name__)


async def coach_pass(
    db: Database,
    gateway: Gateway | None,
    rules_cfg: CoachRules,
    *,
    now: dt.datetime | None = None,
    run_id: str | None = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    planner_rules: PlannerRules | None = None,
) -> PassStats:
    stats = PassStats()
    log.info("coach.pass_start", run_id=run_id)
    if not rules_cfg.enabled:
        stats.note("disabled")
        return stats
    now = now or dt.datetime.now(dt.UTC)
    async with service_conn(db) as conn:
        cur = await conn.execute(_STUDENTS, (rules_cfg.max_users_per_run,))
        students = [dict(r) for r in await cur.fetchall()]
    for s in students:
        stats.students += 1
        try:
            stats.rows += await coach_student(db, gateway, rules_cfg, s, now=now, run_id=run_id, stats=stats)
        except (psycopg.Error, TimeoutError) as exc:
            stats.errors += 1
            log.warning("coach.student_failed", error=type(exc).__name__)
        if planner_rules is not None:
            await propose_rebalance(db, s["user_id"], planner_rules, now=now, stats=stats)
        await sleep(0)
    log.info("coach.pass", **{k: v for k, v in stats.as_dict().items() if k != "reasons"})
    return stats
