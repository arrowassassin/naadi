"""Agent operations for admins (segment 19): Aegra runs and crons, `llm_usage` against the caps, Tavily credits,
`agent_health`. Metadata only: never a run's input, output, messages, prompts or error text.

Runs and crons are read from Aegra's own tables (`runs`, `crons`, `assistant`) in the shared database: the Agent
Protocol has no "list every run" call (runs are listed per thread). Cancelling goes through `langgraph-sdk`
against the Aegra server this API is mounted in (Space host: `naadi_api.agent.host`), with the admin's own bearer
token, so Aegra's auth handler decides as for any other client. Without Aegra (plain `naadi_api.main`, tests)
the tables are absent and the endpoints report `available: false`.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal, get_args

from fastapi import APIRouter, HTTPException, Path, Query, Request
from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from ..llm_budget import provider_daily_cap
from ..llm_config import ProviderName, RuntimeConfig
from ..logs import get_logger
from .common import ADMIN_ERRORS, NOT_FOUND, ConsoleAdmin, audit, one, rows, run_tx

log = get_logger("naadi.admin.agent_ops")

router = APIRouter(prefix="/api/admin/agent", tags=["admin"])

RUN_STATUSES = ("pending", "running", "success", "error", "timeout", "interrupted")
RunStatus = Literal["pending", "running", "success", "error", "timeout", "interrupted"]
RUN_ID = r"^[A-Za-z0-9-]{8,64}$"
GRAPH_ID = r"^[a-z][a-z0-9_]{0,47}$"


class AgentRun(BaseModel):
    run_id: str
    thread_id: str
    graph_id: str | None
    status: str
    has_error: bool
    system: bool = Field(description="Started by a system cron (not a person)")
    created_at: dt.datetime
    updated_at: dt.datetime


class AgentRunList(BaseModel):
    available: bool
    items: list[AgentRun]


class AgentCronLastRun(BaseModel):
    status: str
    at: dt.datetime


class AgentCron(BaseModel):
    cron_id: str
    graph_id: str | None
    key: str | None = Field(description="Naadi's cron key (metadata.naadi_key), e.g. ping")
    schedule: str
    enabled: bool
    next_run_date: dt.datetime | None
    last_run: AgentCronLastRun | None


class AgentCronList(BaseModel):
    available: bool
    items: list[AgentCron]


class UsageDay(BaseModel):
    date: dt.date
    provider: str
    purpose: str
    calls: int
    ok: int
    failed: int


class UsageCap(BaseModel):
    provider: str
    purpose: Literal["chat", "job", "total"]
    cap: int


class TavilyCredits(BaseModel):
    month_credits: int
    today_credits: int
    monthly_guard: int


class HealthRow(BaseModel):
    kind: str
    trigger: str
    ok: bool
    latency_ms: int | None
    created_at: dt.datetime


class AgentUsage(BaseModel):
    days: list[UsageDay]
    today: list[UsageDay]
    caps: list[UsageCap]
    tavily: TavilyCredits
    health: list[HealthRow]


class CancelOut(BaseModel):
    run_id: str
    status: str


async def _aegra_tables(conn: AsyncConnection[Any]) -> bool:
    r = await one(
        conn,
        "select to_regclass('public.runs') is not null and to_regclass('public.crons') is not null"
        " and to_regclass('public.assistant') is not null as ok",
    )
    return bool(r and r["ok"])


@router.get(
    "/runs",
    operation_id="listAdminAgentRuns",
    summary="Admin only: recent agent runs (metadata only)",
    response_model=AgentRunList,
    responses=ADMIN_ERRORS,
)
async def list_runs(
    request: Request,
    admin: ConsoleAdmin,
    graph: Annotated[str | None, Query(pattern=GRAPH_ID)] = None,
    status: Annotated[RunStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AgentRunList:
    async def run(conn: AsyncConnection[Any]) -> AgentRunList:
        if not await _aegra_tables(conn):
            return AgentRunList(available=False, items=[])
        sql = (
            "select r.run_id, r.thread_id, a.graph_id, r.status, (r.error_message is not null) as has_error,"
            " (r.user_id !~ '^[0-9a-f-]{36}$') as system, r.created_at, r.updated_at"
            " from public.runs r left join public.assistant a on a.assistant_id = r.assistant_id where true"
        )
        params: dict[str, Any] = {"limit": limit}
        if graph:
            sql += " and a.graph_id = %(graph)s"
            params["graph"] = graph
        if status:
            sql += " and r.status = %(status)s"
            params["status"] = status
        found = await rows(conn, sql + " order by r.created_at desc limit %(limit)s", params)
        return AgentRunList(available=True, items=[AgentRun(**r) for r in found])

    return await run_tx(request, run)


@router.get(
    "/crons",
    operation_id="listAdminAgentCrons",
    summary="Admin only: agent crons with the next run and the last result",
    response_model=AgentCronList,
    responses=ADMIN_ERRORS,
)
async def list_crons(request: Request, admin: ConsoleAdmin) -> AgentCronList:
    async def run(conn: AsyncConnection[Any]) -> AgentCronList:
        if not await _aegra_tables(conn):
            return AgentCronList(available=False, items=[])
        found = await rows(
            conn,
            "select c.cron_id, a.graph_id, c.metadata ->> 'naadi_key' as key, c.schedule, c.enabled, c.next_run_date,"
            " lr.status as last_status, lr.created_at as last_at"
            " from public.crons c left join public.assistant a on a.assistant_id = c.assistant_id"
            " left join lateral (select r.status, r.created_at from public.runs r"
            "   where r.assistant_id = c.assistant_id and r.user_id = c.user_id"
            "     and (c.thread_id is null or r.thread_id = c.thread_id)"
            "   order by r.created_at desc limit 1) lr on true"
            " order by c.next_run_date nulls last, c.cron_id",
        )
        return AgentCronList(
            available=True,
            items=[
                AgentCron(
                    cron_id=r["cron_id"],
                    graph_id=r["graph_id"],
                    key=r["key"],
                    schedule=r["schedule"],
                    enabled=r["enabled"],
                    next_run_date=r["next_run_date"],
                    last_run=AgentCronLastRun(status=r["last_status"], at=r["last_at"]) if r["last_status"] else None,
                )
                for r in found
            ],
        )

    return await run_tx(request, run)


@router.post(
    "/runs/{thread_id}/{run_id}/cancel",
    operation_id="cancelAdminAgentRun",
    summary="Admin only: cancel a pending or running agent run",
    response_model=CancelOut,
    responses={**ADMIN_ERRORS, **NOT_FOUND},
)
async def cancel_run(
    request: Request,
    admin: ConsoleAdmin,
    thread_id: Annotated[str, Path(pattern=RUN_ID)],
    run_id: Annotated[str, Path(pattern=RUN_ID)],
) -> CancelOut:
    async def check(conn: AsyncConnection[Any]) -> dict[str, Any]:
        if not await _aegra_tables(conn):
            raise HTTPException(503, "The agent runtime is not running on this server.")
        r = await one(conn, "select status from public.runs where run_id = %s and thread_id = %s", (run_id, thread_id))
        if r is None:
            raise HTTPException(404, "No such run.")
        return r

    current = await run_tx(request, check)
    if current["status"] not in ("pending", "running"):
        return CancelOut(run_id=run_id, status=current["status"])
    canceller = getattr(request.app.state, "admin_run_canceller", None)
    try:
        if canceller is not None:
            await canceller(thread_id, run_id)
        else:
            await _sdk_cancel(request, thread_id, run_id)
    except HTTPException:
        raise
    except Exception as exc:  # the runtime refused or is down; nothing changed on our side
        log.warning("agent_cancel_failed", error=type(exc).__name__)
        raise HTTPException(503, "The agent runtime did not accept the cancel. Try again in a moment.") from exc

    async def record(conn: AsyncConnection[Any]) -> CancelOut:
        r = await one(conn, "select status from public.runs where run_id = %s", (run_id,))
        await audit(conn, admin, "agent.run_cancel", "agent_run", run_id, before=current, after=r)
        return CancelOut(run_id=run_id, status=str(r["status"]) if r else "interrupted")

    return await run_tx(request, record)


async def _sdk_cancel(request: Request, thread_id: str, run_id: str) -> None:
    from langgraph_sdk import get_client

    port = (request.scope.get("server") or ("127.0.0.1", 7860))[1]
    auth = request.headers.get("authorization")
    if not auth:
        raise HTTPException(401, "Sign in again.")
    client = get_client(url=f"http://127.0.0.1:{port}", headers={"Authorization": auth})
    await client.runs.cancel(thread_id, run_id, wait=False)


@router.get(
    "/usage",
    operation_id="getAdminAgentUsage",
    summary="Admin only: model calls per day × provider × purpose against the caps, Tavily credits, agent health",
    response_model=AgentUsage,
    responses=ADMIN_ERRORS,
)
async def usage(request: Request, admin: ConsoleAdmin, days: Annotated[int, Query(ge=1, le=60)] = 14) -> AgentUsage:
    settings = request.app.state.settings

    async def run(
        conn: AsyncConnection[Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
        per_day = await rows(
            conn,
            "select (created_at at time zone 'utc')::date as date, provider, purpose, count(*)::int as calls,"
            " count(*) filter (where ok)::int as ok, count(*) filter (where ok is false)::int as failed"
            " from public.llm_usage where created_at >= (now() at time zone 'utc')::date - (%s - 1) * interval '1 day'"
            " group by 1, 2, 3 order by 1 desc, 2, 3",
            (days,),
        )
        tav = await one(
            conn,
            "select coalesce(sum(credits) filter (where created_at >= (date_trunc('month', now() at time zone 'utc')"
            " at time zone 'utc')), 0)::int as month, coalesce(sum(credits) filter (where created_at >="
            " (date_trunc('day', now() at time zone 'utc') at time zone 'utc')), 0)::int as today"
            " from public.search_usage where source = 'tavily'",
        )
        health = await rows(
            conn,
            "select distinct on (kind, trigger) kind, trigger, ok, latency_ms, created_at from public.agent_health"
            " order by kind, trigger, created_at desc",
        )
        return per_day, tav, health

    per_day, tav, health = await run_tx(request, run)
    caps: list[UsageCap] = []
    guard = 0
    try:
        cfg = await RuntimeConfig(request.app.state.db, settings.config_dir).get()
        env_cap = getattr(settings, "openrouter_daily_cap", None)
        for provider in get_args(ProviderName):
            total = provider_daily_cap(cfg, provider, env_cap)
            caps.append(UsageCap(provider=provider, purpose="total", cap=total))
            for purpose in ("chat", "job"):
                caps.append(UsageCap(provider=provider, purpose=purpose, cap=int(total * cfg.budget.share(purpose))))
        guard = int(cfg.search.monthly_credit_guard)
    except Exception as exc:  # caps are context; usage still shows without them  # noqa: BLE001
        log.warning("agent_usage_caps_unavailable", error=type(exc).__name__)
    today = dt.datetime.now(dt.UTC).date()
    items = [UsageDay(**r) for r in per_day]
    return AgentUsage(
        days=items,
        today=[d for d in items if d.date == today],
        caps=caps,
        tavily=TavilyCredits(
            month_credits=int(tav["month"]) if tav else 0,
            today_credits=int(tav["today"]) if tav else 0,
            monthly_guard=guard,
        ),
        health=[HealthRow(**h) for h in health],
    )
