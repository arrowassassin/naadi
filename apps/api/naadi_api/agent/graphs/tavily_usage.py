"""`tavily_usage`: once a day, reconcile Tavily's own usage reading with Naadi's ledger.

Run by a system cron (naadi_api.agent.host). It reads Tavily's usage endpoint once, counts this month's credits
in `search_usage`, and records both in `agent_health` (kind `tavily_usage`). It never blocks anything: the
monthly stop is enforced from our ledger in search.py, and a failed or throttled reading is just recorded.
Admins may run it by hand; students may not.
"""

from __future__ import annotations

import asyncio
from typing import Any, NotRequired, TypedDict

import httpx
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from naadi_api.agent.health import record_health
from naadi_api.agent.identity import RunIdentityError, run_identity
from naadi_api.agent.runtime import agent_db, get_runtime
from naadi_api.health.checks import TAVILY_USAGE_URL


class UsageState(TypedDict):
    ok: NotRequired[bool]
    detail: NotRequired[dict[str, Any]]
    recorded_id: NotRequired[int]


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


async def our_month_credits() -> int:
    db = agent_db()
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    async with db.pool.connection(timeout=5.0) as conn, asyncio.timeout(5.0):
        cur = await conn.execute(
            "select coalesce(sum(credits), 0) from public.search_usage where source = 'tavily'"
            " and created_at >= (date_trunc('month', now() at time zone 'utc') at time zone 'utc')"
        )
        row = await cur.fetchone()
    return int(row[0]) if row else 0


async def read(state: UsageState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    if not (who.is_admin or who.is_system):
        raise RunIdentityError("the tavily_usage graph is admin-only")
    settings = get_runtime().settings
    ledger = await our_month_credits()
    if settings.tavily_api_key is None:
        return {"ok": False, "detail": {"outcome": "not_configured", "ledger_month_credits": ledger}}
    # tavily-python has no usage method; this documented endpoint is free (spends no credit).
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.get(
                TAVILY_USAGE_URL, headers={"Authorization": f"Bearer {settings.tavily_api_key.get_secret_value()}"}
            )
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "detail": {"outcome": "unreachable", "error": type(exc).__name__, "ledger_month_credits": ledger},
        }
    if r.status_code != 200:
        return {"ok": False, "detail": {"outcome": f"http_{r.status_code}", "ledger_month_credits": ledger}}
    body = r.json()
    account = body.get("account") or {}
    key = body.get("key") or {}
    used = _int(account.get("plan_usage"))
    if used is None:
        used = _int(key.get("usage"))
    limit = _int(account.get("plan_limit"))
    if limit is None:
        limit = _int(key.get("limit"))
    return {
        "ok": True,
        "detail": {
            "outcome": "ok",
            "tavily_used": used,
            "tavily_limit": limit,
            "ledger_month_credits": ledger,
            "drift": (used - ledger) if used is not None else None,
        },
    }


async def record(state: UsageState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    rid = await record_health(
        agent_db(),
        kind="tavily_usage",
        trigger="cron" if who.is_system else "background",
        ok=bool(state.get("ok")),
        run_id=who.run_id,
        detail=state.get("detail") or {},
    )
    return {"recorded_id": rid}


def build() -> StateGraph[UsageState]:
    g: StateGraph[UsageState] = StateGraph(UsageState)
    g.add_node("read", read)
    g.add_node("record", record)
    g.add_edge(START, "read")
    g.add_edge("read", "record")
    g.add_edge("record", END)
    return g


graph = build()
