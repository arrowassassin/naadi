"""Database side of the planner: load the plan for a rebalance, write and apply proposals, last-writer-wins status
changes and student items.

Every function takes an open connection and the student's id and names the user in each statement, so it works both
as the API's privileged role and inside the `planner` graph. Applying a proposal is one transaction.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from ..config_api.schemas import PlannerRules
from .planner_models import StudentItemIn
from .rebalance import MOVABLE_STATUSES, Proposal, RDay, RebalanceInput, RItem, missed_before, rebalance
from .store import rows

Row = dict[str, Any]
Conn = psycopg.AsyncConnection[Any]

ITEM_SELECT = (
    "id, day, kind, title, ref_type, ref_id, target_minutes, target_count, status, origin, sort, plan_day,"
    " moved_from, client_updated_at_ms"
)
DAY_SELECT = (
    "day_no, date, weekday, week, phase, chapter_ref, topic, target_hours::float8 as target_hours, extra,"
    " (self_assessment is not null) as assessment, self_assessment as assessment_title"
)
SERVER_MS = "(extract(epoch from clock_timestamp()) * 1000)::bigint"
# A device clock may run a little ahead of the server's; a stamp further ahead would freeze the item (every honest
# later change would lose), so the stored stamp is never more than this far past the server's own clock.
MAX_STAMP_SKEW_MS = 300_000
REBALANCE_SELECT = "id, created_at, trigger, status, applied_at, thread_id, proposal"
Outcome = Literal["applied", "stale", "gone"]


def zone(tz: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz or "UTC")
    except (KeyError, ValueError):
        return ZoneInfo("UTC")


def local_now(tz: str | None, now: dt.datetime | None = None) -> dt.datetime:
    return (now or dt.datetime.now(dt.UTC)).astimezone(zone(tz))


@dataclass(frozen=True, slots=True)
class PlanData:
    plan: Row
    days: list[Row]
    items: list[Row]
    profile: Row


async def load_plan(conn: Conn, user_id: uuid.UUID) -> PlanData | None:
    plans = await rows(
        conn,
        "select id, template_id, created_at, inputs from public.study_plans where user_id = %s and active",
        (user_id,),
    )
    if not plans:
        return None
    plan = plans[0]
    profile = (
        await rows(
            conn,
            "select timezone, exam_target, exam_date, daily_hours::float8 as daily_hours,"
            " study_days::int[] as study_days from public.profiles where user_id = %s",
            (user_id,),
        )
        or [{}]
    )[0]
    days = await rows(
        conn,
        f"select {DAY_SELECT} from public.study_plan_days where user_id = %s and plan_id = %s order by day_no",  # noqa: S608
        (user_id, plan["id"]),
    )
    if not days:
        return None
    items = await rows(
        conn,
        f"select {ITEM_SELECT} from public.plan_items where user_id = %s"  # noqa: S608
        " and (plan_id = %s or (plan_id is null and day between %s and %s))"
        " order by day, sort nulls last, id",
        (user_id, plan["id"], days[0]["date"], days[-1]["date"]),
    )
    return PlanData(plan, days, items, profile)


def exam_date_of(data: PlanData) -> dt.date:
    raw = data.plan["inputs"].get("exam_date")
    if raw:
        return dt.date.fromisoformat(str(raw))
    fallback: dt.date = data.profile.get("exam_date") or (data.days[-1]["date"] + dt.timedelta(days=1))
    return fallback


def study_weekdays(data: PlanData) -> frozenset[int]:
    from_inputs = data.plan["inputs"].get("study_days") or data.profile.get("study_days") or []
    return frozenset(int(d) for d in from_inputs)


def rebalance_input(data: PlanData, rules: PlannerRules, now: dt.datetime | None = None) -> RebalanceInput:
    local = local_now(data.profile.get("timezone"), now)
    return RebalanceInput(
        days=[
            RDay(d["date"], d["day_no"], d["target_hours"], bool(d["assessment"]), d["chapter_ref"], d["topic"])
            for d in data.days
        ],
        items=[
            RItem(
                str(i["id"]),
                i["day"],
                i["kind"] or "read",
                i["status"],
                i["sort"],
                i["target_minutes"],
                i["title"],
                i["origin"],
            )
            for i in data.items
        ],
        today=local.date(),
        missed_before=missed_before(local, rules.missed_after_hours),
        exam_date=exam_date_of(data),
        study_days=study_weekdays(data),
        daily_hours=float(data.profile.get("daily_hours") or data.plan["inputs"].get("daily_hours") or 8),
        max_items_per_day_factor=rules.max_items_per_day_factor,
    )


def behind_count(data: PlanData, rules: PlannerRules, now: dt.datetime | None = None) -> int:
    cutoff = missed_before(local_now(data.profile.get("timezone"), now), rules.missed_after_hours)
    return sum(1 for i in data.items if i["status"] in MOVABLE_STATUSES and i["day"] < cutoff)


async def current_proposal(conn: Conn, user_id: uuid.UUID) -> Row | None:
    found = await rows(
        conn,
        f"select {REBALANCE_SELECT} from public.plan_rebalances where user_id = %s and status = 'proposed'",  # noqa: S608
        (user_id,),
    )
    return found[0] if found else None


async def propose(
    conn: Conn,
    user_id: uuid.UUID,
    rules: PlannerRules,
    *,
    trigger: Literal["missed", "student", "coach"],
    thread_id: str | None = None,
    now: dt.datetime | None = None,
) -> tuple[Row | None, Proposal | None]:
    """Compute the proposal for the student's plan and store it (expiring the one before). Returns the stored row
    and the proposal, or (None, proposal) when nothing is missed and nothing needs saying. The same proposal
    twice returns the row that already exists, so a nightly pass does not churn."""
    # One proposal at a time per student: concurrent proposals (nightly pass and a tap) queue here.
    await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"plan_rebalance:{user_id}",))
    data = await load_plan(conn, user_id)
    if data is None:
        return None, None
    proposal = rebalance(rebalance_input(data, rules, now))
    existing = await current_proposal(conn, user_id)
    if proposal.empty:
        if existing is not None:
            await conn.execute(
                "update public.plan_rebalances set status = 'expired' where id = %s and user_id = %s",
                (existing["id"], user_id),
            )
        return None, proposal
    body = proposal.to_json()
    if existing is not None and existing["proposal"].get("fingerprint") == proposal.fingerprint:
        if thread_id and existing["thread_id"] != thread_id:
            await conn.execute(
                "update public.plan_rebalances set thread_id = %s where id = %s and user_id = %s",
                (thread_id, existing["id"], user_id),
            )
            existing = {**existing, "thread_id": thread_id}
        return existing, proposal
    if existing is not None:
        await conn.execute(
            "update public.plan_rebalances set status = 'expired' where id = %s and user_id = %s",
            (existing["id"], user_id),
        )
    created = await rows(
        conn,
        f"insert into public.plan_rebalances (user_id, plan_id, trigger, proposal, thread_id) values (%s,%s,%s,%s,%s)"  # noqa: S608
        f" returning {REBALANCE_SELECT}",
        (user_id, data.plan["id"], trigger, Jsonb(body), thread_id),
    )
    return created[0], proposal


async def get_rebalance(conn: Conn, user_id: uuid.UUID, rebalance_id: uuid.UUID) -> Row | None:
    found = await rows(
        conn,
        f"select {REBALANCE_SELECT} from public.plan_rebalances where id = %s and user_id = %s",  # noqa: S608
        (rebalance_id, user_id),
    )
    return found[0] if found else None


async def decline(conn: Conn, user_id: uuid.UUID, rebalance_id: uuid.UUID) -> bool:
    done = await rows(
        conn,
        "update public.plan_rebalances set status = 'declined' where id = %s and user_id = %s and status = 'proposed'"
        " returning id",
        (rebalance_id, user_id),
    )
    return bool(done)


async def apply(
    conn: Conn, user_id: uuid.UUID, rebalance_id: uuid.UUID, now: dt.datetime | None = None
) -> tuple[Outcome, int, int]:
    """Apply a proposed rebalance in the caller's transaction: (outcome, moved, dropped).

    `stale` when any item it would touch has changed since it was proposed (done, moved, re-planned): nothing is
    changed and the proposal is expired, so the student sees a fresh one. `gone` when it is not a proposal of
    theirs any more (already applied, declined, expired)."""
    await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"plan_rebalance:{user_id}",))
    locked = await rows(
        conn,
        "select id, proposal from public.plan_rebalances where id = %s and user_id = %s and status = 'proposed'"
        " for update",
        (rebalance_id, user_id),
    )
    if not locked:
        return "gone", 0, 0
    row = locked[0]
    moves = row["proposal"]["moves"]
    drops = row["proposal"]["drops"]
    ids = [uuid.UUID(m["item_id"]) for m in moves] + [uuid.UUID(d["item_id"]) for d in drops]
    current = {
        r["id"]: r
        for r in await rows(
            conn,
            "select id, day, status from public.plan_items where user_id = %s and id = any(%s) for update",
            (user_id, ids),
        )
    }
    expected = [(uuid.UUID(m["item_id"]), dt.date.fromisoformat(m["from_day"])) for m in moves] + [
        (uuid.UUID(d["item_id"]), dt.date.fromisoformat(d["day"])) for d in drops
    ]
    tz = (await rows(conn, "select timezone from public.profiles where user_id = %s", (user_id,)) or [{}])[0]
    today = local_now(tz.get("timezone"), now).date()
    # Stale: an item changed since the proposal, or its destination day has passed (a proposal made at 23:30 and
    # accepted after midnight must not put items on a day that is already over).
    if any(dt.date.fromisoformat(m["to_day"]) < today for m in moves) or any(
        i not in current or current[i]["day"] != day or current[i]["status"] not in MOVABLE_STATUSES
        for i, day in expected
    ):
        await conn.execute(
            "update public.plan_rebalances set status = 'expired' where id = %s and user_id = %s",
            (rebalance_id, user_id),
        )
        return "stale", 0, 0
    async with conn.cursor() as cur:
        await cur.executemany(
            "update public.plan_items set day = %s, sort = %s, moved_from = %s, rebalance_id = %s"
            " where id = %s and user_id = %s",
            [
                (
                    dt.date.fromisoformat(m["to_day"]),
                    m["sort"],
                    dt.date.fromisoformat(m["from_day"]),
                    rebalance_id,
                    uuid.UUID(m["item_id"]),
                    user_id,
                )
                for m in moves
            ],
        )
        await cur.executemany(
            "update public.plan_items set status = 'skipped', rebalance_id = %s where id = %s and user_id = %s",
            [(rebalance_id, uuid.UUID(d["item_id"]), user_id) for d in drops],
        )
    await conn.execute(
        "update public.plan_rebalances set status = 'accepted', applied_at = now() where id = %s and user_id = %s",
        (rebalance_id, user_id),
    )
    return "applied", len(moves), len(drops)


async def patch_status(
    conn: Conn, user_id: uuid.UUID, item_id: uuid.UUID, status: str, client_updated_at_ms: int
) -> tuple[bool, Row] | None:
    """Last-writer-wins per row: a strictly newer stamp wins, an equal or older one changes nothing and the stored
    row is returned (a tie goes to the server). None when the item is not the student's."""
    stamp = f"least(%s::bigint, {SERVER_MS} + {MAX_STAMP_SKEW_MS})"
    done = await rows(
        conn,
        f"update public.plan_items set status = %s, client_updated_at_ms = {stamp} where id = %s and user_id = %s"  # noqa: S608
        f" and (client_updated_at_ms is null or client_updated_at_ms < {stamp}) returning {ITEM_SELECT}",
        (status, client_updated_at_ms, item_id, user_id, client_updated_at_ms),
    )
    if done:
        return True, done[0]
    stored = await rows(
        conn,
        f"select {ITEM_SELECT} from public.plan_items where id = %s and user_id = %s",  # noqa: S608
        (item_id, user_id),
    )
    return (False, stored[0]) if stored else None


class ItemsRefused(ValueError):
    """A student item the plan cannot take; `code` says why (the client words it)."""

    def __init__(self, code: Literal["no_plan", "outside_plan", "assessment_day"]):
        super().__init__(code)
        self.code = code


async def create_items(conn: Conn, user_id: uuid.UUID, items: Sequence[StudentItemIn]) -> list[Row]:
    """Student items (`origin = 'user'`) on days of the active plan, after what the day already holds. In-process
    entry for placements (a confirmed timetable) and the route behind `POST /api/plan/items`."""
    await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"plan_items:{user_id}",))
    data = await load_plan(conn, user_id)
    if data is None:
        raise ItemsRefused("no_plan")
    span = {d["date"]: d for d in data.days}
    nxt: dict[dt.date, int] = {}
    for i in data.items:
        nxt[i["day"]] = max(nxt.get(i["day"], 0), (i["sort"] if i["sort"] is not None else -1) + 1)
    out: list[Row] = []
    for it in items:
        day = span.get(it.day)
        if day is None:
            raise ItemsRefused("outside_plan")
        if day["assessment"] or it.day == data.days[-1]["date"]:
            raise ItemsRefused("assessment_day")  # assessment and rest days take nothing new
        sort = nxt.get(it.day, 0)
        nxt[it.day] = sort + 1
        made = await rows(
            conn,
            f"insert into public.plan_items (user_id, day, kind, title, ref_type, ref_id, target_minutes,"  # noqa: S608
            f" target_count, origin, sort, client_updated_at_ms) values (%s,%s,%s,%s,%s,%s,%s,%s,'user',%s,"
            f" {SERVER_MS}) returning {ITEM_SELECT}",
            (user_id, it.day, it.kind, it.title, it.ref_type, it.ref_id, it.target_minutes, it.target_count, sort),
        )
        out.append(made[0])
    return out
