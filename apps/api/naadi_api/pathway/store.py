"""SQL for the pathway, the student's task state, costs and the admin actions."""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import lww, rules
from .db import PathwayError

Row = dict[str, Any]
Conn = psycopg.AsyncConnection[Any]


async def fetch(conn: Conn, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> list[Row]:
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(sql, params)
    return await cur.fetchall()


async def fetch_one(conn: Conn, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> Row | None:
    found = await fetch(conn, sql, params)
    return found[0] if found else None


# ── the pathway as the student sees it (read as the student: RLS) ──


async def load_pathway(conn: Conn) -> tuple[list[Row], list[Row], list[Row], Row | None]:
    stages = await fetch(
        conn,
        "select key, sort::int as sort, title, summary_md, official_links, last_verified"
        " from public.pathway_stages order by sort, key",
    )
    tasks = await fetch(
        conn,
        "select t.id, t.stage_key, t.sort::int as sort, t.title, t.detail_md, t.due_rule,"
        " t.cost_usd::float8 as cost_usd, t.official_url, t.last_verified, t.needs_review,"
        " coalesce(u.status, 'todo') as status, u.due, u.notes, u.client_updated_at_ms, u.updated_at"
        " from public.pathway_tasks t"
        " left join public.user_pathway_tasks u on u.task_id = t.id and u.user_id = (select auth.uid())"
        " order by t.stage_key, t.sort, t.id",
    )
    guides = await fetch(conn, "select key, stage_key, title, body_md from public.pathway_guides order by sort, key")
    profile = await fetch_one(
        conn, "select pathway_stage, timezone from public.profiles where user_id = (select auth.uid())"
    )
    return stages, tasks, guides, profile


def derive_current_stage(stages: list[Row], tasks_by_stage: dict[str, list[Row]]) -> str | None:
    """The first stage that still has a task to do or in progress."""
    for s in stages:
        if any(t["status"] in ("todo", "doing") for t in tasks_by_stage.get(s["key"], [])):
            return str(s["key"])
    return None


def shape_pathway(
    stages: list[Row],
    tasks: list[Row],
    guides: list[Row],
    profile: Row | None,
    *,
    today: dt.date,
    reverify_days: int,
    official_domains: list[str],
) -> dict[str, Any]:
    by_stage: dict[str, list[Row]] = defaultdict(list)
    for t in tasks:
        by_stage[t["stage_key"]].append(t)
    guide_of = {g["stage_key"]: g for g in guides if g["stage_key"]}
    overview = next((g for g in guides if g["stage_key"] is None and g["key"] == "overview"), None)
    chosen = profile["pathway_stage"] if profile else None
    known = {s["key"] for s in stages}
    current: str | None
    source: str | None
    if chosen in known:
        current, source = chosen, "chosen"
    else:
        current = derive_current_stage(stages, by_stage)
        source = "derived" if current else None

    out_stages: list[dict[str, Any]] = []
    for s in stages:
        task_rows = by_stage.get(s["key"], [])
        out_tasks = []
        for t in task_rows:
            reason = rules.reverify_reason(t["last_verified"], bool(t["needs_review"]), today, reverify_days)
            out_tasks.append(
                {
                    "id": t["id"],
                    "sort": t["sort"] or 0,
                    "title": t["title"] or "",
                    "detail_md": t["detail_md"] or "",
                    "due_rule": rules.parse_due_rule(t["due_rule"]),
                    "cost_usd": t["cost_usd"],
                    "official_url": t["official_url"],
                    "official": rules.is_official_url(t["official_url"], official_domains),
                    "last_verified": t["last_verified"],
                    "days_since_verified": rules.days_since(t["last_verified"], today),
                    "needs_review": bool(t["needs_review"]),
                    "reverify": reason,
                    "status": t["status"],
                    "due": t["due"],
                    "notes": t["notes"],
                    "client_updated_at_ms": t["client_updated_at_ms"],
                    "updated_at": t["updated_at"],
                }
            )
        flagged = any(t["needs_review"] for t in task_rows)
        stage_reason = "flagged" if flagged else rules.reverify_reason(s["last_verified"], False, today, reverify_days)
        g = guide_of.get(s["key"])
        out_stages.append(
            {
                "key": s["key"],
                "sort": s["sort"] or 0,
                "title": s["title"] or "",
                "summary_md": s["summary_md"] or "",
                "official_links": s["official_links"] or [],
                "last_verified": s["last_verified"],
                "reverify": stage_reason,
                "done": sum(1 for t in task_rows if t["status"] in ("done", "na")),
                "total": len(task_rows),
                "reverify_count": sum(1 for t in out_tasks if t["reverify"]),
                "spend_planned_usd": float(sum((t["cost_usd"] or 0) for t in task_rows if t["status"] != "na")),
                "guide": {"key": g["key"], "title": g["title"], "body_md": g["body_md"]} if g else None,
                "tasks": out_tasks,
            }
        )
    return {
        "today": today,
        "reverify_days": reverify_days,
        "current_stage": current,
        "current_stage_source": source,
        "overview": {"key": overview["key"], "title": overview["title"], "body_md": overview["body_md"]}
        if overview
        else None,
        "stages": out_stages,
    }


# ── the student's task state (last-writer-wins) ──

_USER_TASK = "task_id, status, due, notes, client_updated_at_ms, updated_at"


async def patch_user_task(
    conn: Conn, user_id: uuid.UUID, task_id: str, fields: dict[str, Any], stamp: int, now_ms: int
) -> tuple[Row, bool]:
    """Apply `fields` to the student's row for a task if `stamp` beats the stored stamp. Returns (row, applied)."""
    if await fetch_one(conn, "select 1 as x from public.pathway_tasks where id = %s", (task_id,)) is None:
        raise PathwayError(404, "pathway_task_not_found")
    await conn.execute(
        "insert into public.user_pathway_tasks (user_id, task_id) values (%s, %s) on conflict do nothing",
        (user_id, task_id),
    )
    cur = await fetch_one(
        conn,
        f"select {_USER_TASK} from public.user_pathway_tasks where user_id = %s and task_id = %s for update",  # noqa: S608
        (user_id, task_id),
    )
    assert cur is not None
    if not lww.incoming_wins(stamp, cur["client_updated_at_ms"], now_ms):
        return cur, False
    merged = {k: fields[k] for k in ("status", "due", "notes") if k in fields}
    new_stamp = lww.clamp_stamp(stamp, now_ms)
    sets = ", ".join(f"{k} = %s" for k in merged)
    sql = (
        f"update public.user_pathway_tasks set {sets + ', ' if sets else ''}client_updated_at_ms = %s"  # noqa: S608
        f" where user_id = %s and task_id = %s returning {_USER_TASK}"
    )
    updated = await fetch_one(conn, sql, [*merged.values(), new_stamp, user_id, task_id])
    assert updated is not None
    return updated, True


async def set_current_stage(conn: Conn, user_id: uuid.UUID, stage_key: str | None) -> None:
    if (
        stage_key is not None
        and await fetch_one(conn, "select 1 as x from public.pathway_stages where key = %s", (stage_key,)) is None
    ):
        raise PathwayError(404, "pathway_stage_not_found")
    await conn.execute("insert into public.profiles (user_id) values (%s) on conflict (user_id) do nothing", (user_id,))
    await conn.execute("update public.profiles set pathway_stage = %s where user_id = %s", (stage_key, user_id))


# ── costs ──

_COST = (
    "id, task_id, stage_key, coalesce(label, '') as label, to_char(coalesce(amount, 0), 'FM999999999990.00') as amount,"
    " currency, paid_on, client_updated_at_ms, created_at, updated_at"
)


async def resolve_stage(conn: Conn, task_id: str | None, stage_key: str | None) -> tuple[str | None, str | None]:
    """The stage a cost belongs to: its task's stage, else the chosen one (which must exist)."""
    if task_id is not None:
        row = await fetch_one(conn, "select stage_key from public.pathway_tasks where id = %s", (task_id,))
        if row is None:
            raise PathwayError(404, "pathway_task_not_found")
        return task_id, str(row["stage_key"])
    if (
        stage_key is not None
        and await fetch_one(conn, "select 1 as x from public.pathway_stages where key = %s", (stage_key,)) is None
    ):
        raise PathwayError(404, "pathway_stage_not_found")
    return None, stage_key


async def list_costs(conn: Conn, user_id: uuid.UUID) -> list[Row]:
    return await fetch(
        conn,
        f"select {_COST} from public.costs where user_id = %s"  # noqa: S608
        " order by coalesce(paid_on, date '9999-12-31') desc, created_at desc, id",
        (user_id,),
    )


async def get_cost(conn: Conn, user_id: uuid.UUID, cost_id: uuid.UUID, *, lock: bool = False) -> Row | None:
    return await fetch_one(
        conn,
        f"select {_COST} from public.costs where user_id = %s and id = %s{' for update' if lock else ''}",  # noqa: S608
        (user_id, cost_id),
    )


async def count_costs(conn: Conn, user_id: uuid.UUID) -> int:
    row = await fetch_one(conn, "select count(*)::int as n from public.costs where user_id = %s", (user_id,))
    return int(row["n"]) if row else 0


async def create_cost(
    conn: Conn,
    user_id: uuid.UUID,
    *,
    cost_id: uuid.UUID,
    task_id: str | None,
    stage_key: str | None,
    label: str,
    amount: Decimal,
    currency: str,
    paid_on: dt.date | None,
    stamp: int,
    now_ms: int,
    max_costs: int,
) -> tuple[Row, bool, bool]:
    """Insert, or on a replay of the same id apply last-writer-wins. Returns (row, created, applied)."""
    task_id, stage_key = await resolve_stage(conn, task_id, stage_key)
    new_stamp = lww.clamp_stamp(stamp, now_ms)
    owner = await fetch_one(conn, "select user_id from public.costs where id = %s", (cost_id,))
    if owner is not None and owner["user_id"] != user_id:
        raise PathwayError(409, "cost_id_conflict")
    if owner is None:
        if await count_costs(conn, user_id) >= max_costs:
            raise PathwayError(409, "cost_limit")
        row = await fetch_one(
            conn,
            "insert into public.costs (id, user_id, task_id, stage_key, label, amount, currency, paid_on,"  # noqa: S608
            " client_updated_at_ms) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
            f" returning {_COST}",
            (cost_id, user_id, task_id, stage_key, label, amount, currency, paid_on, new_stamp),
        )
        assert row is not None
        return row, True, True
    stored = await get_cost(conn, user_id, cost_id, lock=True)
    assert stored is not None
    if not lww.incoming_wins(stamp, stored["client_updated_at_ms"], now_ms):
        return stored, False, False
    row = await fetch_one(
        conn,
        "update public.costs set task_id = %s, stage_key = %s, label = %s, amount = %s, currency = %s, paid_on = %s,"  # noqa: S608
        f" client_updated_at_ms = %s where user_id = %s and id = %s returning {_COST}",
        (task_id, stage_key, label, amount, currency, paid_on, new_stamp, user_id, cost_id),
    )
    assert row is not None
    return row, False, True


async def patch_cost(
    conn: Conn, user_id: uuid.UUID, cost_id: uuid.UUID, fields: dict[str, Any], stamp: int, now_ms: int
) -> tuple[Row, bool]:
    stored = await get_cost(conn, user_id, cost_id, lock=True)
    if stored is None:
        raise PathwayError(404, "cost_not_found")
    if not lww.incoming_wins(stamp, stored["client_updated_at_ms"], now_ms):
        return stored, False
    sets: dict[str, Any] = {}
    if "task_id" in fields or "stage_key" in fields:
        task_id = fields.get("task_id", stored["task_id"] if "stage_key" not in fields else None)
        stage_key = fields.get("stage_key", stored["stage_key"])
        task_id, stage_key = await resolve_stage(conn, task_id, stage_key)
        sets["task_id"], sets["stage_key"] = task_id, stage_key
    for k in ("label", "amount", "currency", "paid_on"):
        if k in fields:
            sets[k] = fields[k]
    sets["client_updated_at_ms"] = lww.clamp_stamp(stamp, now_ms)
    assignments = ", ".join(f"{k} = %s" for k in sets)
    row = await fetch_one(
        conn,
        f"update public.costs set {assignments} where user_id = %s and id = %s returning {_COST}",  # noqa: S608
        [*sets.values(), user_id, cost_id],
    )
    assert row is not None
    return row, True


async def delete_cost(conn: Conn, user_id: uuid.UUID, cost_id: uuid.UUID, stamp: int | None, now_ms: int) -> Row | None:
    """Delete the row. Returns the stored row when a newer edit than `stamp` keeps it alive, else None."""
    stored = await get_cost(conn, user_id, cost_id, lock=True)
    if stored is None:
        return None
    if stamp is not None and not lww.incoming_wins(stamp, stored["client_updated_at_ms"], now_ms):
        return stored
    await conn.execute("delete from public.costs where user_id = %s and id = %s", (user_id, cost_id))
    return None


async def cost_summary_rows(conn: Conn, user_id: uuid.UUID) -> tuple[list[Row], list[Row], list[Row]]:
    stages = await fetch(conn, "select key from public.pathway_stages order by sort, key")
    planned = await fetch(
        conn,
        "select t.stage_key, coalesce(sum(t.cost_usd), 0)::text as planned from public.pathway_tasks t"
        " left join public.user_pathway_tasks u on u.task_id = t.id and u.user_id = %s"
        " where coalesce(u.status, 'todo') <> 'na' group by t.stage_key",
        (user_id,),
    )
    entries = await fetch(
        conn,
        "select id, stage_key, label, amount::text as amount, currency, paid_on, created_at from public.costs"
        " where user_id = %s",
        (user_id,),
    )
    return stages, planned, entries


def summarise_costs(stages: list[Row], planned: list[Row], entries: list[Row], currencies: list[str]) -> dict[str, Any]:
    """Totals by stage and currency. Planned is the task estimates in USD; paid = entries with a paid date,
    upcoming = entries without one. Nothing is converted."""
    planned_by = {r["stage_key"]: Decimal(r["planned"]) for r in planned}
    order = [str(s["key"]) for s in stages]
    paid: dict[str | None, dict[str, Decimal]] = defaultdict(dict)
    upcoming: dict[str | None, dict[str, Decimal]] = defaultdict(dict)
    count: dict[str | None, int] = defaultdict(int)
    for e in entries:
        key = e["stage_key"]
        count[key] += 1
        rules.add_money(paid[key] if e["paid_on"] else upcoming[key], e["currency"], Decimal(e["amount"] or 0))
    total_paid: dict[str, Decimal] = {}
    total_up: dict[str, Decimal] = {}
    for key in paid:
        for c, a in paid[key].items():
            rules.add_money(total_paid, c, a)
    for key in upcoming:
        for c, a in upcoming[key].items():
            rules.add_money(total_up, c, a)

    def money(m: dict[str, Decimal]) -> list[dict[str, str]]:
        return [{"currency": c, "amount": f"{a:.2f}"} for c, a in rules.money_list(m)]

    keys: list[str | None] = [k for k in order if k in planned_by or k in count]
    if None in count:
        keys.append(None)
    by_stage = [
        {
            "stage_key": k,
            "planned_usd": f"{planned_by.get(k, Decimal(0)):.2f}" if k is not None else "0.00",
            "paid": money(paid.get(k, {})),
            "upcoming": money(upcoming.get(k, {})),
            "entries": count.get(k, 0),
        }
        for k in keys
    ]
    upcoming_entries = sorted((e for e in entries if not e["paid_on"]), key=lambda e: (e["created_at"], str(e["id"])))
    nxt = upcoming_entries[0] if upcoming_entries else None
    return {
        "currencies": currencies,
        "planned_usd": f"{sum(planned_by.values(), Decimal(0)):.2f}",
        "paid": money(total_paid),
        "upcoming": money(total_up),
        "by_stage": by_stage,
        "next_fee": {
            "id": nxt["id"],
            "label": nxt["label"] or "",
            "amount": f"{Decimal(nxt['amount'] or 0):.2f}",
            "currency": nxt["currency"],
            "stage_key": nxt["stage_key"],
        }
        if nxt
        else None,
    }


# ── admin ──


async def list_checks(conn: Conn, *, only_open: bool, limit: int) -> tuple[list[Row], int]:
    where = "where c.resolved_at is null and c.changed" if only_open else ""
    items = await fetch(
        conn,
        "select c.id, c.task_id, t.title as task_title, t.stage_key, c.checked_at, c.url, c.http_status, c.changed,"  # noqa: S608
        " c.finding, c.resolved_at, t.last_verified, t.needs_review"
        " from public.pathway_task_checks c join public.pathway_tasks t on t.id = c.task_id"
        f" {where} order by c.checked_at desc, c.id desc limit %s",
        (limit,),
    )
    n = await fetch_one(
        conn, "select count(*)::int as n from public.pathway_task_checks where resolved_at is null and changed"
    )
    return items, int(n["n"]) if n else 0


async def verify_task(conn: Conn, task_id: str, admin_id: uuid.UUID, today: dt.date) -> Row:
    """An admin confirms the task is still right: last_verified = today, the flag clears, open checks resolve."""
    row = await fetch_one(
        conn,
        "update public.pathway_tasks t set last_verified = %s, needs_review = false,"
        " content_hash = coalesce((select c.content_hash from public.pathway_task_checks c where c.task_id = t.id"
        "   and c.resolved_at is null and c.content_hash is not null order by c.checked_at desc, c.id desc limit 1),"
        "   t.content_hash)"
        " where t.id = %s"
        " returning t.id, t.detail_md, t.cost_usd::float8 as cost_usd, t.official_url, t.last_verified,"
        " t.needs_review, t.stage_key",
        (today, task_id),
    )
    if row is None:
        raise PathwayError(404, "pathway_task_not_found")
    await conn.execute(
        "update public.pathway_task_checks set resolved_by = %s, resolved_at = now()"
        " where task_id = %s and resolved_at is null",
        (admin_id, task_id),
    )
    await conn.execute(
        "update public.pathway_stages s set last_verified = m.v from"
        " (select min(last_verified) as v from public.pathway_tasks where stage_key = %s) m"
        " where s.key = %s and m.v is not null and m.v > coalesce(s.last_verified, date '0001-01-01')",
        (row["stage_key"], row["stage_key"]),
    )
    return row


async def patch_task_admin(conn: Conn, task_id: str, fields: dict[str, Any]) -> Row:
    allowed = {k: v for k, v in fields.items() if k in ("detail_md", "cost_usd", "official_url")}
    assignments = ", ".join(f"{k} = %s" for k in allowed)
    row = await fetch_one(
        conn,
        f"update public.pathway_tasks set {assignments} where id = %s"  # noqa: S608
        " returning id, detail_md, cost_usd::float8 as cost_usd, official_url, last_verified, needs_review",
        [*allowed.values(), task_id],
    )
    if row is None:
        raise PathwayError(404, "pathway_task_not_found")
    return row


def jsonb(value: Any) -> Jsonb:
    return Jsonb(value)
