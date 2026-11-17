"""Planner routes (segment 16): the week view, status changes (last-writer-wins), student items, rebalance proposals
and the calendar feed.

Every response carries structured fields; the web app words them from `ui_copy`. Reads run as the student (RLS);
writes use the API's own role with the student's id in every statement.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections import defaultdict
from collections.abc import Callable
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse, Response

from ..auth import CurrentUser, get_account_user
from ..auth_limits import RateLimiter
from ..config_api.schemas import PlannerRules
from ..config_api.service import AppConfigService
from ..db import Database
from ..errors import ErrorResponse, error_response
from ..logs import get_logger
from ..qbank.routes import as_service, as_user
from . import feed
from . import planner_store as ps
from .models import PlanItemOut
from .planner_models import (
    CalendarFeedCreatedOut,
    CalendarFeedOut,
    ItemPatchIn,
    ItemPatchOut,
    MilestoneOut,
    PlanWeekOut,
    RebalanceOut,
    RebalanceProposalOut,
    RebalancesOut,
    RebalanceStartOut,
    StudentItemsIn,
    StudentItemsOut,
    WeekDayOut,
    WeekItemOut,
    WeekOut,
)
from .rebalance import MOVABLE_STATUSES, missed_before
from .store import item_row_out, rows

log = get_logger("naadi.planner")
router = APIRouter(prefix="/api", tags=["planner"])
Row = dict[str, Any]

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Database unavailable"},
}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse, "description": "Not found"}}
_INVALID: dict[int | str, dict[str, Any]] = {422: {"model": ErrorResponse, "description": "Invalid inputs"}}

MAX_ACTIVE_FEED_FETCHES_PER_MIN = 60


def default_limiters() -> dict[str, RateLimiter]:
    return {
        "planner_read": RateLimiter(240, 60.0),
        "planner_write": RateLimiter(60, 60.0),
        "planner_rebalance": RateLimiter(6, 60.0),
        "planner_feed": RateLimiter(10, 60.0),
        # The public feed URL has no session: limited per address and per token, so guessing is hopeless and a
        # misbehaving calendar app cannot hammer the database.
        "ics_ip": RateLimiter(MAX_ACTIVE_FEED_FETCHES_PER_MIN, 60.0),
        "ics_token": RateLimiter(20, 60.0),
    }


def limited(bucket: str) -> Any:
    async def dep(request: Request, user: Annotated[CurrentUser, Depends(get_account_user)]) -> CurrentUser:
        limiters: dict[str, RateLimiter] = request.app.state.rate_limiters
        limiters[bucket].hit(str(user.user_id))
        return user

    return Depends(dep)


def _db(request: Request) -> Database:
    d: Database = request.app.state.db
    return d


def _config(request: Request) -> AppConfigService:
    c: AppConfigService = request.app.state.app_config
    return c


async def _rules(request: Request) -> PlannerRules:
    return PlannerRules.model_validate(await _config(request).get("planner"))


def _item_out(r: Row) -> PlanItemOut:
    return PlanItemOut.model_validate(item_row_out(r))


def _week_item(r: Row, cutoff: dt.date) -> WeekItemOut:
    return WeekItemOut.model_validate(
        {
            **item_row_out(r),
            "behind": r["status"] in MOVABLE_STATUSES and r["day"] < cutoff,
            "moved_from": r["moved_from"],
            "client_updated_at_ms": r["client_updated_at_ms"],
        }
    )


def rebalance_out(r: Row) -> RebalanceOut:
    return RebalanceOut(
        id=r["id"],
        created_at=r["created_at"],
        trigger=r["trigger"],
        status=r["status"],
        applied_at=r["applied_at"],
        thread_id=r["thread_id"],
        proposal=RebalanceProposalOut.model_validate(r["proposal"]),
    )


# ── The week ──


def _milestones(days: list[Row], exam_date: dt.date | None, today: dt.date) -> list[MilestoneOut]:
    out: list[MilestoneOut] = []
    last_phase: str | None = None
    for n, d in enumerate(days):
        phase = d["phase"]
        if phase and phase != last_phase and n > 0:
            out.append(
                MilestoneOut(
                    kind="phase", date=d["date"], day_no=d["day_no"], title=phase, days_away=(d["date"] - today).days
                )
            )
        last_phase = phase or last_phase
        if d["assessment"]:
            out.append(
                MilestoneOut(
                    kind="assessment",
                    date=d["date"],
                    day_no=d["day_no"],
                    title=d["assessment_title"],
                    days_away=(d["date"] - today).days,
                )
            )
    if days:
        rest = days[-1]
        out.append(
            MilestoneOut(
                kind="rest", date=rest["date"], day_no=rest["day_no"], title=None, days_away=(rest["date"] - today).days
            )
        )
    if exam_date is not None:
        out.append(
            MilestoneOut(kind="exam", date=exam_date, day_no=None, title=None, days_away=(exam_date - today).days)
        )
    return sorted(out, key=lambda m: (m.date, m.kind))


def week_start(first: dt.date, last: dt.date, today: dt.date, asked: dt.date | None) -> dt.date:
    """The first day of the shown week: the one asked for, else the plan week that holds today (plan weeks start on
    the plan's first day); always inside the plan."""
    if asked is not None:
        return min(max(asked, first), last)
    anchor = min(max(today, first), last)
    return first + dt.timedelta(days=7 * ((anchor - first).days // 7))


def build_week(
    data: ps.PlanData, rules: PlannerRules, asked: dt.date | None, now: dt.datetime | None = None
) -> WeekOut:
    local = ps.local_now(data.profile.get("timezone"), now)
    today = local.date()
    cutoff = missed_before(local, rules.missed_after_hours)
    first, last = data.days[0]["date"], data.days[-1]["date"]
    start = week_start(first, last, today, asked)
    by_date = {d["date"]: d for d in data.days}
    items_by_day: dict[dt.date, list[Row]] = defaultdict(list)
    for i in data.items:
        items_by_day[i["day"]].append(i)
    days: list[WeekDayOut] = []
    for k in range(7):
        date = start + dt.timedelta(days=k)
        d = by_date.get(date)
        its = [_week_item(i, cutoff) for i in items_by_day.get(date, [])]
        minutes = sum(i.target_minutes or 0 for i in its if i.status != "skipped")
        days.append(
            WeekDayOut(
                date=date,
                weekday=date.strftime("%a"),
                day_no=d["day_no"] if d else None,
                phase=d["phase"] if d else None,
                chapter_ref=d["chapter_ref"] if d else None,
                topic=d["topic"] if d else None,
                target_hours=d["target_hours"] if d else None,
                planned_hours=round(minutes / 60, 2),
                assessment=bool(d and d["assessment"]),
                rest=date == last,
                extra=bool(d and d["extra"]),
                in_plan=d is not None,
                is_today=date == today,
                past=date < today,
                items=its,
            )
        )
    exam = ps.exam_date_of(data)
    todays = [i for i in days if i.is_today]
    next_item = next((i for i in (todays[0].items if todays else []) if i.status in MOVABLE_STATUSES), None)
    return WeekOut(
        plan_id=data.plan["id"],
        start=start,
        end=start + dt.timedelta(days=6),
        week=(start - first).days // 7 + 1,
        weeks=data.days[-1]["week"],
        today=today,
        exam=str(data.plan["inputs"].get("exam") or data.profile.get("exam_target") or ""),
        exam_date=exam,
        days_to_exam=(exam - today).days,
        prev_start=None if start <= first else max(first, start - dt.timedelta(days=7)),
        next_start=None if start + dt.timedelta(days=7) > last else start + dt.timedelta(days=7),
        hours_planned=round(sum(d.planned_hours for d in days), 2),
        hours_target=round(sum(d.target_hours or 0 for d in days), 2),
        behind=ps.behind_count(data, rules, now),
        days=days,
        milestones=_milestones(data.days, exam, today),
        next_item=next_item,
        rebalance=None,
    )


@router.get(
    "/plan/week",
    operation_id="getPlanWeek",
    summary="Seven days of the plan: items, hours, assessment days, milestones and any rebalance proposal",
    response_model=PlanWeekOut,
    responses=_ERRORS,
)
async def get_week(
    request: Request,
    user: Annotated[CurrentUser, limited("planner_read")],
    start: Annotated[
        dt.date | None, Query(description="First day of the week; default: the plan week of today")
    ] = None,
) -> PlanWeekOut:
    rules = await _rules(request)

    async def run(conn: psycopg.AsyncConnection[Any]) -> tuple[ps.PlanData | None, Row | None]:
        data = await ps.load_plan(conn, user.user_id)
        return data, (await ps.current_proposal(conn, user.user_id) if data else None)

    data, proposal = await as_user(_db(request), user, run)
    if data is None:
        return PlanWeekOut(week=None)
    week = build_week(data, rules, start)
    if proposal is not None:
        week = week.model_copy(update={"rebalance": rebalance_out(proposal)})
    return PlanWeekOut(week=week)


# ── Items ──


@router.patch(
    "/plan/items/{item_id}",
    operation_id="patchPlanItem",
    summary="Change an item's status; the newest change wins (last-writer-wins per item)",
    response_model=ItemPatchOut,
    responses={**_ERRORS, **_NOT_FOUND, **_INVALID},
)
async def patch_item(
    request: Request,
    item_id: Annotated[uuid.UUID, Path()],
    body: ItemPatchIn,
    user: Annotated[CurrentUser, limited("planner_write")],
) -> ItemPatchOut | JSONResponse:
    async def run(conn: psycopg.AsyncConnection[Any]) -> tuple[bool, Row] | None:
        return await ps.patch_status(conn, user.user_id, item_id, body.status, body.client_updated_at_ms)

    out = await as_service(_db(request), run)
    if out is None:
        return error_response(404, "This plan item does not exist.", "not_found")
    return ItemPatchOut(applied=out[0], item=_item_out(out[1]))


@router.post(
    "/plan/items",
    operation_id="createPlanItems",
    summary="Add the student's own items to days of the plan",
    response_model=StudentItemsOut,
    status_code=201,
    responses={**_ERRORS, **_NOT_FOUND, **_INVALID},
)
async def create_items(
    request: Request, body: StudentItemsIn, user: Annotated[CurrentUser, limited("planner_write")]
) -> StudentItemsOut | JSONResponse:
    async def run(conn: psycopg.AsyncConnection[Any]) -> list[Row]:
        return await ps.create_items(conn, user.user_id, body.items)

    try:
        made = await as_service(_db(request), run)
    except ps.ItemsRefused as exc:
        if exc.code == "no_plan":
            return error_response(404, "There is no study plan yet.", "no_plan")
        return error_response(
            422,
            "That day cannot take new items." if exc.code == "assessment_day" else "That day is outside your plan.",
            exc.code,
        )
    return StudentItemsOut(items=[_item_out(r) for r in made])


# ── Rebalance ──

AgentClientFactory = Callable[[Request], Any]


def agent_client(request: Request) -> Any:
    """The Aegra runtime that hosts this very app, reached on its own port with the caller's token (so the run
    belongs to the student, like any run the web app starts)."""
    from langgraph_sdk import get_client

    factory: AgentClientFactory | None = getattr(request.app.state, "agent_client_factory", None)
    if factory is not None:
        return factory(request)
    server = request.scope.get("server")
    port = server[1] if server else 7860
    # The runtime and this app share one process and one origin: loopback, never the public URL.
    return get_client(url=f"http://127.0.0.1:{port}", headers={"authorization": request.headers["authorization"]})


@router.post(
    "/plan/rebalance",
    operation_id="startPlanRebalance",
    summary="Start a rebalance: the planner run proposes, then waits on the returned thread for the student's confirm",
    response_model=RebalanceStartOut,
    status_code=202,
    responses={
        **_ERRORS,
        **_NOT_FOUND,
        503: {"model": ErrorResponse, "description": "The agent runtime is unavailable"},
    },
)
async def start_rebalance(
    request: Request, user: Annotated[CurrentUser, limited("planner_rebalance")]
) -> RebalanceStartOut | JSONResponse:
    async def has_plan(conn: psycopg.AsyncConnection[Any]) -> bool:
        found = await rows(
            conn, "select 1 as one from public.study_plans where user_id = %s and active", (user.user_id,)
        )
        return bool(found)

    if not await as_service(_db(request), has_plan):
        return error_response(404, "There is no study plan yet.", "no_plan")
    client = agent_client(request)
    try:
        thread = await client.threads.create(metadata={"graph_id": "planner"})
        run = await client.runs.create(thread["thread_id"], "planner", input={"trigger": "student"})
    except Exception as exc:  # noqa: BLE001 - the runtime is a separate service: any failure is "unavailable"
        log.warning("planner_run_failed", error=type(exc).__name__)
        return error_response(503, "The planner could not be started. Try again in a moment.", "agent_unavailable")
    return RebalanceStartOut(thread_id=thread["thread_id"], run_id=run["run_id"])


@router.get(
    "/plan/rebalances",
    operation_id="listPlanRebalances",
    summary="The student's rebalance proposals (default: the one waiting for a decision)",
    response_model=RebalancesOut,
    responses={**_ERRORS, **_INVALID},
)
async def list_rebalances(
    request: Request,
    user: Annotated[CurrentUser, limited("planner_read")],
    status: Annotated[Literal["proposed", "accepted", "declined", "expired"] | None, Query()] = "proposed",
) -> RebalancesOut:
    async def run(conn: psycopg.AsyncConnection[Any]) -> list[Row]:
        return await rows(
            conn,
            f"select {ps.REBALANCE_SELECT} from public.plan_rebalances where user_id = (select auth.uid())"  # noqa: S608
            " and (%s::text is null or status = %s) order by created_at desc limit 20",
            (status, status),
        )

    return RebalancesOut(rebalances=[rebalance_out(r) for r in await as_user(_db(request), user, run)])


# ── Calendar feed ──


def feed_url(request: Request, token: str) -> str:
    base = request.app.state.settings.app_base_url or str(request.base_url)
    return f"{base.rstrip('/')}/api/plan/ics/{token}"


@router.get(
    "/plan/calendar-feed",
    operation_id="getCalendarFeed",
    summary="Whether the student has a live calendar feed (the URL itself is shown only when it is made)",
    response_model=CalendarFeedOut,
    responses=_ERRORS,
)
async def get_feed(request: Request, user: Annotated[CurrentUser, limited("planner_read")]) -> CalendarFeedOut:
    since = await as_service(_db(request), lambda conn: feed.active_since(conn, user.user_id))
    return CalendarFeedOut(active=since is not None, created_at=since)


@router.post(
    "/plan/calendar-feed",
    operation_id="rotateCalendarFeed",
    summary="Make a new calendar feed URL (the old one stops working); the URL is shown once",
    response_model=CalendarFeedCreatedOut,
    status_code=201,
    responses=_ERRORS,
)
async def rotate_feed(
    request: Request, response: Response, user: Annotated[CurrentUser, limited("planner_feed")]
) -> CalendarFeedCreatedOut:
    token, created = await as_service(_db(request), lambda conn: feed.rotate(conn, user.user_id))
    response.headers["cache-control"] = "no-store"  # the URL is shown once: never cached
    return CalendarFeedCreatedOut(url=feed_url(request, token), created_at=created)


@router.delete(
    "/plan/calendar-feed",
    operation_id="revokeCalendarFeed",
    summary="Stop the calendar feed: its URL answers 404 from now on",
    status_code=204,
    responses=_ERRORS,
)
async def revoke_feed(request: Request, user: Annotated[CurrentUser, limited("planner_feed")]) -> Response:
    await as_service(_db(request), lambda conn: feed.revoke(conn, user.user_id))
    return Response(status_code=204)


def _client_key(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    host = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")
    return hashlib.sha256(host.encode()).hexdigest()[:16]


@router.get(
    "/plan/ics/{token}",
    operation_id="getPlanIcs",
    summary="The calendar feed (no sign-in: the token in the URL is the credential)",
    responses={
        **_NOT_FOUND,
        429: {"model": ErrorResponse, "description": "Rate limited"},
        200: {"content": {"text/calendar": {}}, "description": "The plan as an iCalendar feed"},
    },
    response_class=Response,
)
async def get_ics(request: Request, token: Annotated[str, Path(min_length=20, max_length=128)]) -> Response:
    limiters: dict[str, RateLimiter] = request.app.state.rate_limiters
    limiters["ics_ip"].hit(_client_key(request))
    limiters["ics_token"].hit(hashlib.sha256(token.encode()).hexdigest()[:16])

    async def run(conn: psycopg.AsyncConnection[Any]) -> tuple[uuid.UUID, ps.PlanData | None] | None:
        user_id = await feed.user_for_token(conn, token)
        if user_id is None:
            return None
        return user_id, await ps.load_plan(conn, user_id)

    found = await as_service(_db(request), run)
    if found is None:
        return error_response(404, "This calendar link is not valid.", "not_found")
    user_id, data = found
    copy: dict[str, str] = await _config(request).get("ui_copy")
    body = feed.build_ics(user_id, data.days if data else [], data.items if data else [], copy)
    return Response(
        body,
        media_type="text/calendar; charset=utf-8",
        headers={
            "cache-control": "private, max-age=900",
            "content-disposition": 'inline; filename="naadi-study-plan.ics"',
            "x-content-type-options": "nosniff",
            "referrer-policy": "no-referrer",
        },
    )


def install_planner_routes(app: Any) -> None:
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
    app.include_router(router)


__all__ = ["agent_client", "build_week", "install_planner_routes", "rebalance_out", "router", "week_start"]
