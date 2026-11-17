"""Onboarding, the study plan, Today and coach suggestions (segment 11).

- `GET/PUT /api/onboarding`: the setup form's options and saved answers; saving updates the profile (as the
  student, RLS) and generates the plan (written by the API: plans are server-authoritative).
- `GET /api/plan/preview`: the live "11 weeks, six days a week" while the form changes (structured numbers).
- `GET /api/plan`, `GET /api/plan/export.csv`: the active plan, and the same rows in the template CSV's shape.
- `POST /api/plan/items/{id}/status`: tick an item done/skipped (or back to planned).
- `POST /api/diagnostic`: the 40-question timed diagnostic block (idempotent), once timed blocks exist.
- `GET /api/today`: everything Today shows in one request.
- `POST /api/coach/suggestions/{id}/accept|dismiss`.

Every response carries structured fields; the web app words them from `ui_copy`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import defaultdict
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import psycopg
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException

from ..analytics.student import by_system, cards_due, load_cells, load_weights, readiness
from ..auth import CurrentUser, get_account_user
from ..auth_limits import RateLimiter
from ..auth_routes import _valid_timezone
from ..config_api.schemas import CoachRules, PlanRules
from ..config_api.service import AppConfigService
from ..db import Database
from ..errors import ErrorResponse, FieldError, error_response
from ..logs import get_logger
from ..qbank.routes import as_service, as_user
from . import diagnostic as diag
from .generator import PlanDay, PlanError, PlanInputs, available_dates, generate, summarize, to_csv
from .items import ItemMinutes
from .models import (
    MATERIALS,
    CardsOut,
    CoachActionOut,
    CoachSuggestionOut,
    DiagnosticOut,
    DiagnosticState,
    ExamOption,
    HoursRange,
    ItemStatusIn,
    OnboardingIn,
    OnboardingOut,
    OnboardingState,
    OnboardingValues,
    PathwayStageOut,
    PlanDayMeta,
    PlanDayOut,
    PlanItemOut,
    PlanOut,
    PlanPreview,
    PlanSummaryOut,
    ReadinessOut,
    ResumeBlock,
    ResumeReading,
    SystemOut,
    SystemsOut,
    TodayOut,
    TodayPlan,
)
from .store import DAY_COLS, ITEM_COLS, day_out, item_row_out, load_template, rows, save_profile, write_plan

log = get_logger("naadi.plan")
router = APIRouter(prefix="/api", tags=["plan"])
Row = dict[str, Any]

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Database unavailable"},
}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse, "description": "Not found"}}
_INVALID: dict[int | str, dict[str, Any]] = {422: {"model": ErrorResponse, "description": "Invalid inputs"}}


def default_limiters() -> dict[str, RateLimiter]:
    # plan_export: the CSV export is a heavy route (segment 22 rate-limit audit: exports <= 30/min).
    return {
        "plan_read": RateLimiter(240, 60.0),
        "plan_write": RateLimiter(30, 60.0),
        "plan_export": RateLimiter(10, 60.0),
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


async def _plan_rules(request: Request) -> PlanRules:
    return PlanRules.model_validate(await _config(request).get("plan"))


async def _coach_rules(request: Request) -> CoachRules:
    return CoachRules.model_validate(await _config(request).get("coach"))


async def _features(request: Request, user: CurrentUser) -> dict[str, bool]:
    return await _config(request).resolved_features(is_admin=user.is_admin, app_env=request.app.state.settings.app_env)


def _zone(tz: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz or "UTC")
    except (KeyError, ValueError):
        return ZoneInfo("UTC")


def local_today(tz: str | None, now: dt.datetime | None = None) -> dt.date:
    return (now or dt.datetime.now(dt.UTC)).astimezone(_zone(tz)).date()


async def _profile(conn: psycopg.AsyncConnection[Any]) -> Row:
    await conn.execute(
        "insert into public.profiles (user_id) values ((select auth.uid())) on conflict (user_id) do nothing"
    )
    found = await rows(
        conn,
        "select exam_target, exam_date, daily_hours::float8 as daily_hours, study_days::int[] as study_days,"
        " timezone, settings from public.profiles where user_id = (select auth.uid())",
    )
    return found[0]


def _offered(rules: dict[str, Any], plan: PlanRules) -> list[ExamOption]:
    return [ExamOption(id=e, label=str(fmt["label"])) for e, fmt in rules["exams"].items() if e in plan.templates]


def _inputs(body_exam_date: dt.date, days: list[int], hours: float, start: dt.date, plan: PlanRules) -> PlanInputs:
    return PlanInputs(
        start=start,
        exam_date=body_exam_date,
        study_days=frozenset(days),
        daily_hours=hours,
        reference_hours=plan.reference_hours,
        min_study_days=plan.min_study_days,
        max_days=plan.max_study_days,
    )


def _summary_out(days: list[PlanDay], inputs: PlanInputs) -> PlanSummaryOut:
    s = summarize(days, inputs)
    return PlanSummaryOut(
        study_days=s.study_days,
        weeks=s.weeks,
        days_per_week=s.days_per_week,
        first_day=s.first_day,
        last_day=s.last_day,
        assessments=s.assessments,
        extra_review_days=s.extra_review_days,
        merged_days=s.merged_days,
    )


# ── Diagnostic: delegated to segment 09's timed blocks once they exist ──


def _timed_block_creator() -> Any:
    """`naadi_api.qbank.timed_blocks.create_timed_block` (segment 09, ledger decision 20261116T215440), or None
    while timed blocks have not shipped."""
    import importlib

    try:
        mod = importlib.import_module("naadi_api.qbank.timed_blocks")
    except ImportError:
        return None
    return getattr(mod, "create_timed_block", None)


async def _diagnostic_available(request: Request, user: CurrentUser) -> bool:
    if _timed_block_creator() is None:
        return False
    s = request.app.state.settings
    return await _config(request).feature_enabled("qbank_timed", is_admin=user.is_admin, app_env=s.app_env)


_OPEN_DIAGNOSTIC = (
    "select id, question_ids from public.blocks where user_id = (select auth.uid())"
    " and config->>'purpose' = 'diagnostic' and finished_at is null order by created_at desc limit 1"
)


# ── Onboarding ──


@router.get(
    "/onboarding",
    operation_id="getOnboarding",
    summary="Onboarding options (exams with a plan template, materials, hours) and the saved answers",
    response_model=OnboardingState,
    responses=_ERRORS,
)
async def get_onboarding(request: Request, user: Annotated[CurrentUser, limited("plan_read")]) -> OnboardingState:
    plan = await _plan_rules(request)
    rules: dict[str, Any] = await _config(request).get("exam_rules")
    exams = _offered(rules, plan)
    available = await _diagnostic_available(request, user)

    async def run(conn: psycopg.AsyncConnection[Any]) -> tuple[Row, Row | None]:
        prof = await _profile(conn)
        open_block = (await rows(conn, _OPEN_DIAGNOSTIC))[:1] if available else []
        return prof, open_block[0] if open_block else None

    prof, open_block = await as_user(_db(request), user, run)
    onb = (prof["settings"] or {}).get("onboarding") or {}
    completed = onb.get("completed_at")
    exam = prof["exam_target"] if prof["exam_target"] in {e.id for e in exams} else (exams[0].id if exams else "step1")
    hours = min(max(float(prof["daily_hours"]), 1.0), plan.max_daily_hours)
    return OnboardingState(
        exams=exams,
        materials=list(MATERIALS),
        hours=HoursRange(min=1, max=plan.max_daily_hours, step=0.5, default=hours),
        min_study_days=plan.min_study_days,
        max_days=plan.max_study_days,
        today=local_today(prof["timezone"]),
        values=OnboardingValues(
            exam=exam,
            exam_date=prof["exam_date"],
            medical_school=onb.get("medical_school"),
            daily_hours=hours,
            study_days=list(prof["study_days"]),
            materials=[m for m in MATERIALS if m in (onb.get("materials") or [])],
        ),
        completed_at=dt.datetime.fromisoformat(completed) if isinstance(completed, str) else None,
        diagnostic=DiagnosticState(
            available=available,
            questions=int(rules["diagnostic_questions"]),
            open_block_id=open_block["id"] if open_block else None,
        ),
    )


def _plan_problem(exc: PlanError) -> JSONResponse:
    facts = {k: str(v) for k, v in exc.facts.items()}
    message = (
        "The exam date is further away than a plan can cover."
        if exc.code == "too_many_days"
        else "These dates and study days do not leave enough days for a plan."
    )
    return error_response(
        422,
        message,
        exc.code,
        details=[
            FieldError(loc=["body", "exam_date"], msg=",".join(f"{k}={v}" for k, v in facts.items()), type=exc.code)
        ],
    )


@router.put(
    "/onboarding",
    operation_id="saveOnboarding",
    summary="Save the onboarding answers to the profile and generate the study plan",
    response_model=OnboardingOut,
    responses={**_ERRORS, **_INVALID},
)
async def save_onboarding(
    request: Request, body: OnboardingIn, user: Annotated[CurrentUser, limited("plan_write")]
) -> OnboardingOut | JSONResponse:
    plan = await _plan_rules(request)
    rules: dict[str, Any] = await _config(request).get("exam_rules")
    if body.exam not in {e.id for e in _offered(rules, plan)}:
        return error_response(422, "There is no plan for this exam yet.", "exam_not_offered")
    if body.daily_hours > plan.max_daily_hours:
        return error_response(422, "Choose fewer hours a day.", "too_many_hours")
    template_id = plan.templates[body.exam]
    now = dt.datetime.now(dt.UTC)

    async def read(conn: psycopg.AsyncConnection[Any]) -> tuple[Row, list[Any]]:
        return await _profile(conn), await load_template(conn, template_id)

    prof, template = await as_user(_db(request), user, read)
    start = local_today(body.timezone or prof["timezone"], now)
    if not template:
        return error_response(422, "There is no plan for this exam yet.", "no_template")
    # Bounded before anything is generated or written: a far-future date is refused, never expanded.
    if (body.exam_date - start).days > plan.max_study_days:
        exc = PlanError("too_many_days", days=(body.exam_date - start).days, allowed=plan.max_study_days)
        return _plan_problem(exc)
    inputs = _inputs(body.exam_date, body.study_days, body.daily_hours, start, plan)
    answers: dict[str, Any] = {
        "exam": body.exam,
        "exam_date": body.exam_date,
        "daily_hours": body.daily_hours,
        "study_days": body.study_days,
        "timezone": body.timezone,
    }
    onboarding: dict[str, Any] = {"medical_school": body.medical_school, "materials": list(body.materials)}
    try:
        days = generate(template, inputs)
    except PlanError as exc:
        # The answers are kept so the form comes back filled in; onboarding is not complete without a plan.
        async def keep(conn: psycopg.AsyncConnection[Any]) -> Row:
            return await save_profile(conn, user.user_id, onboarding=onboarding, **answers)

        await as_user(_db(request), user, keep)
        return _plan_problem(exc)
    completed_at = now.replace(microsecond=0)
    sizes = ItemMinutes.model_validate(plan.item_minutes.model_dump())

    async def write(conn: psycopg.AsyncConnection[Any]) -> uuid.UUID:
        # One transaction: the profile, the plan, and only then completed_at. A failed plan write leaves
        # onboarding incomplete (Today keeps sending the student to /welcome) instead of done with no plan.
        await save_profile(conn, user.user_id, onboarding=onboarding, **answers)
        plan_id = await write_plan(
            conn, user.user_id, template_id=template_id, exam=body.exam, inputs=inputs, days=days, sizes=sizes
        )
        await save_profile(conn, user.user_id, onboarding={"completed_at": completed_at.isoformat()}, **answers)
        return plan_id

    plan_id = await as_service(_db(request), write)
    log.info("plan_generated", days=len(days), template=template_id)
    return OnboardingOut(plan_id=plan_id, summary=_summary_out(days, inputs), completed_at=completed_at)


@router.get(
    "/plan/preview",
    operation_id="previewPlan",
    summary="Live plan preview for the onboarding form (nothing is saved)",
    response_model=PlanPreview,
    responses={**_ERRORS, **_INVALID},
)
async def preview_plan(
    request: Request,
    user: Annotated[CurrentUser, limited("plan_read")],
    exam: Annotated[str, Query(pattern=r"^[a-z0-9]{2,16}$")],
    exam_date: dt.date,
    daily_hours: Annotated[float, Query(ge=1, le=24)],
    study_days: Annotated[list[int], Query(min_length=1, max_length=7)],
    tz: Annotated[str | None, Query(max_length=64)] = None,
) -> PlanPreview:
    plan = await _plan_rules(request)
    template_id = plan.templates.get(exam)  # type: ignore[call-overload]
    if any(d < 1 or d > 7 for d in study_days):
        raise HTTPException(422, "Study days are ISO weekdays 1 to 7.")
    if tz is not None:
        try:
            tz = _valid_timezone(tz)
        except ValueError as exc:
            raise HTTPException(422, "Use an IANA time zone name.") from exc

    async def read(conn: psycopg.AsyncConnection[Any]) -> tuple[Row, list[Any]]:
        return await _profile(conn), (await load_template(conn, template_id) if template_id else [])

    prof, template = await as_user(_db(request), user, read)
    start = local_today(tz or prof["timezone"])
    inputs = _inputs(exam_date, sorted(set(study_days)), min(daily_hours, plan.max_daily_hours), start, plan)
    sizes = {"needed_days": plan.min_study_days, "max_days": plan.max_study_days}
    span = (exam_date - start).days
    if span > plan.max_study_days:  # checked before any date is generated: the work stays bounded
        return PlanPreview(ok=False, problem="too_many_days", available_days=0, **sizes)
    avail = len(available_dates(inputs)) if span > 0 else 0
    if not template:
        return PlanPreview(ok=False, problem="no_template", available_days=avail, **sizes)
    try:
        days = generate(template, inputs)
    except PlanError as exc:
        known = ("too_few_study_days", "too_many_days", "exam_date_not_after_start")
        problem = exc.code if exc.code in known else "too_few_study_days"
        return PlanPreview(ok=False, problem=problem, available_days=avail, **sizes)
    return PlanPreview(ok=True, available_days=avail, summary=_summary_out(days, inputs), **sizes)


# ── The plan ──

_ACTIVE = (
    "select id, template_id, created_at, inputs from public.study_plans where user_id = (select auth.uid()) and active"
)


async def _active_plan(conn: psycopg.AsyncConnection[Any]) -> tuple[Row, list[Row], list[Row]] | None:
    found = await rows(conn, _ACTIVE)
    if not found:
        return None
    p = found[0]
    days = await rows(
        conn,
        f"select {DAY_COLS} from public.study_plan_days where plan_id = %s order by day_no",  # noqa: S608 - constant
        (p["id"],),
    )
    items = await rows(
        conn,
        f"select {ITEM_COLS} from public.plan_items where user_id = (select auth.uid())"  # noqa: S608 - constant
        " and (plan_id = %s or (plan_id is null and day between %s and %s)) order by day, sort nulls last, id",
        (p["id"], days[0]["date"] if days else dt.date.min, days[-1]["date"] if days else dt.date.min),
    )
    return p, days, items


def _summary_from_rows(p: Row, days: list[Row]) -> PlanSummaryOut:
    inputs = p["inputs"]
    return PlanSummaryOut(
        study_days=len(days),
        weeks=days[-1]["week"] if days else 0,
        days_per_week=len(inputs.get("study_days") or []),
        first_day=days[0]["date"],
        last_day=days[-1]["date"],
        assessments=sum(1 for d in days if d["assessment"]),
        extra_review_days=sum(1 for d in days if d["extra"]),
        merged_days=sum(1 for d in days if len(d["merged_days"]) > 1),
    )


@router.get(
    "/plan",
    operation_id="getPlan",
    summary="The active study plan: every day with its items",
    response_model=PlanOut,
    responses={**_ERRORS, **_NOT_FOUND},
)
async def get_plan(request: Request, user: Annotated[CurrentUser, limited("plan_read")]) -> PlanOut | JSONResponse:
    found = await as_user(_db(request), user, _active_plan)
    if found is None:
        return error_response(404, "There is no study plan yet.", "no_plan")
    p, days, items = found
    by_day: dict[dt.date, list[Row]] = defaultdict(list)
    for i in items:
        by_day[i["day"]].append(i)
    summary = _summary_from_rows(p, days)
    return PlanOut(
        id=p["id"],
        template_id=p["template_id"],
        created_at=p["created_at"],
        inputs=p["inputs"],
        summary=summary,
        days=[PlanDayOut.model_validate(day_out(d, by_day.get(d["date"], []))) for d in days],
    )


@router.get(
    "/plan/export.csv",
    operation_id="exportPlanCsv",
    summary="The active plan in the template CSV's shape (CRLF, UTF-8)",
    responses={**_ERRORS, **_NOT_FOUND, 200: {"content": {"text/csv": {}}, "description": "The plan as CSV"}},
    response_class=Response,
)
async def export_plan(request: Request, user: Annotated[CurrentUser, limited("plan_export")]) -> Response:
    async def run(conn: psycopg.AsyncConnection[Any]) -> list[Row] | None:
        p = await rows(conn, _ACTIVE)
        if not p:
            return None
        return await rows(
            conn,
            "select day_no, date, weekday, week, phase, chapter_ref, chapter_ids, topic, chapter_questions, qbank,"
            " self_assessment, admin_task, extra_resource, target_hours::float8 as target_hours, template_day,"
            " merged_days::int[] as merged_days, extra, status, score_notes"
            " from public.study_plan_days where plan_id = %s order by day_no",
            (p[0]["id"],),
        )

    found = await as_user(_db(request), user, run)
    if found is None:
        return error_response(404, "There is no study plan yet.", "no_plan")
    days = [
        PlanDay(
            day_no=r["day_no"],
            date=r["date"],
            weekday=r["weekday"],
            week=r["week"],
            phase=r["phase"],
            chapter_ref=r["chapter_ref"],
            chapter_ids=tuple(r["chapter_ids"]),
            topic=r["topic"],
            chapter_questions=r["chapter_questions"],
            qbank=r["qbank"],
            self_assessment=r["self_assessment"],
            admin_task=r["admin_task"],
            extra_resource=r["extra_resource"],
            target_hours=r["target_hours"],
            template_day=r["template_day"],
            merged_days=tuple(r["merged_days"]),
            extra=r["extra"],
            status=r["status"],
            score_notes=r["score_notes"],
        )
        for r in found
    ]
    return Response(
        to_csv(days),
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": 'attachment; filename="naadi-study-plan.csv"', "cache-control": "no-store"},
    )


@router.post(
    "/plan/items/{item_id}/status",
    operation_id="setPlanItemStatus",
    summary="Mark a plan item done, skipped or planned again",
    response_model=PlanItemOut,
    responses={**_ERRORS, **_NOT_FOUND},
)
async def set_item_status(
    request: Request,
    item_id: Annotated[uuid.UUID, Path()],
    body: ItemStatusIn,
    user: Annotated[CurrentUser, limited("plan_write")],
) -> PlanItemOut | JSONResponse:
    async def run(conn: psycopg.AsyncConnection[Any]) -> list[Row]:
        return await rows(
            conn,
            f"update public.plan_items set status = %s where id = %s and user_id = (select auth.uid())"  # noqa: S608
            f" returning {ITEM_COLS}",
            (body.status, item_id),
        )

    out = await as_user(_db(request), user, run)
    if not out:
        return error_response(404, "This plan item does not exist.", "not_found")
    return PlanItemOut.model_validate(item_row_out(out[0]))


# ── Diagnostic ──


@router.post(
    "/diagnostic",
    operation_id="createDiagnostic",
    summary="The onboarding diagnostic: a timed, blueprint-stratified block (returns the open one if it exists)",
    response_model=DiagnosticOut,
    status_code=201,
    responses={**_ERRORS, **_NOT_FOUND, **_INVALID},
)
async def create_diagnostic(
    request: Request, user: Annotated[CurrentUser, limited("plan_write")]
) -> DiagnosticOut | JSONResponse:
    creator = _timed_block_creator()
    if creator is None or not await _diagnostic_available(request, user):
        return error_response(404, "The diagnostic is not available yet.", "diagnostic_unavailable")
    rules: dict[str, Any] = await _config(request).get("exam_rules")
    total = int(rules["diagnostic_questions"])
    seed = diag.diagnostic_seed(user.user_id)

    async def pick(conn: psycopg.AsyncConnection[Any]) -> tuple[Row | None, str, list[str]]:
        open_block = await rows(conn, _OPEN_DIAGNOSTIC)
        prof = await _profile(conn)
        exam = prof["exam_target"]
        if open_block:
            return open_block[0], exam, []
        weights = await load_weights(conn, exam)
        pool = [
            diag.Candidate(r["id"], tuple(r["taxonomy_ids"]), r["difficulty"])
            for r in await rows(
                conn,
                "select id, taxonomy_ids, difficulty from public.questions where exam = %s and block is null"
                " order by id",
                (exam,),
            )
        ]
        return None, exam, diag.choose_diagnostic(pool, weights, total, seed)

    def existing(block: Row) -> JSONResponse:
        return JSONResponse(
            DiagnosticOut(block_id=block["id"], questions=len(block["question_ids"]), created=False).model_dump(
                mode="json"
            ),
            status_code=200,
        )

    open_block, exam, ids = await as_user(_db(request), user, pick)
    if open_block is not None:
        return existing(open_block)
    if len(ids) < total:
        return error_response(422, "There are not enough questions for the diagnostic yet.", "pool_too_small")
    from ..qbank.models import BlockConfig

    cfg = BlockConfig(
        exam=exam,
        mode="timed",
        status="all",
        difficulty=None,
        systems=[],
        disciplines=[],
        seed=seed,
        weak_spot=False,
        purpose="diagnostic",
    )
    try:
        block = await creator(_db(request), user, cfg, ids, rules=rules, sections=1)
    except psycopg.errors.UniqueViolation:
        # A concurrent request created it first (index blocks_one_open_diagnostic, 0014): return that one.
        raced = await as_user(_db(request), user, lambda conn: rows(conn, _OPEN_DIAGNOSTIC))
        if not raced:
            raise
        return existing(raced[0])
    return DiagnosticOut(block_id=block.id, questions=len(ids), created=True)


# ── Today ──

_RESUME_READING = """
select rp.chapter_id, c.title as chapter_title, rp.section_id, s.heading as section_heading, s.sort as section_n
from public.reading_positions rp
join public.chapters c on c.id = rp.chapter_id
join public.chapter_sections s on s.id = rp.section_id
where rp.user_id = (select auth.uid()) and rp.completed_at is null
order by rp.client_updated_at desc limit 1
"""
_RESUME_BLOCK = """
select b.id, b.config, b.question_ids, b.created_at,
  (select count(*) from public.attempts a where a.block_id = b.id and a.user_id = b.user_id
     and a.chosen is not null)::int as answered
from public.blocks b
where b.user_id = (select auth.uid()) and b.finished_at is null
order by b.created_at desc limit 1
"""
_SECTION_N = """
select count(*)::int as n from public.chapter_sections where chapter_id = %s and sort <= %s
"""
_COACH = """
select id, for_date, kind, source, title, body, facts, action, status from public.coach_suggestions
where user_id = (select auth.uid()) and status = 'new' and for_date between %s and %s
order by for_date desc, created_at limit %s
"""
_PATHWAY = """
select s.key, s.title, count(t.id)::int as total,
  count(u.task_id) filter (where u.status in ('done', 'na'))::int as done
from public.pathway_stages s left join public.pathway_tasks t on t.stage_key = s.key
left join public.user_pathway_tasks u on u.task_id = t.id and u.user_id = (select auth.uid())
group by s.key, s.title, s.sort order by s.sort, s.key
"""


def coach_out(r: Row) -> CoachSuggestionOut:
    return CoachSuggestionOut.model_validate(r)


@router.get(
    "/today",
    operation_id="getToday",
    summary="Today in one request: countdown, plan items, resume target, readiness, by-system, cards, coach",
    response_model=TodayOut,
    responses=_ERRORS,
)
async def get_today(request: Request, user: Annotated[CurrentUser, limited("plan_read")]) -> TodayOut:
    coach = await _coach_rules(request)
    features = await _features(request, user)
    now = dt.datetime.now(dt.UTC)

    async def run(conn: psycopg.AsyncConnection[Any]) -> TodayOut:
        prof = await _profile(conn)
        today = local_today(prof["timezone"], now)
        exam = prof["exam_target"]
        onb = (prof["settings"] or {}).get("onboarding") or {}
        weights = await load_weights(conn, exam)
        cells = await load_cells(
            conn,
            exam,
            [w.id for w in weights],
            now=now,
            window_days=coach.window_days,
            readiness_days=coach.readiness_days,
        )
        stats = by_system(weights, cells.recent, min_attempts=coach.min_attempts, weak_below=coach.weak_below)
        ready = readiness(
            weights, cells, min_answered=coach.readiness_min_answered, min_coverage=coach.readiness_min_coverage
        )
        plan_out: TodayPlan | None = None
        active = await rows(conn, _ACTIVE)
        if active:
            pid = active[0]["id"]
            day = await rows(
                conn,
                f"select {DAY_COLS} from public.study_plan_days where plan_id = %s and date = %s",  # noqa: S608
                (pid, today),
            )
            items = await rows(
                conn,
                f"select {ITEM_COLS} from public.plan_items where user_id = (select auth.uid()) and day = %s"  # noqa: S608
                " order by (plan_id is null), sort nulls last, id",
                (today,),
            )
            nxt = await rows(
                conn,
                "select min(date) as d from public.study_plan_days where plan_id = %s and date > %s",
                (pid, today),
            )
            now_item = next((i["id"] for i in items if i["status"] not in ("done", "skipped")), None)
            plan_out = TodayPlan(
                plan_id=pid,
                day=PlanDayMeta.model_validate(day_out(day[0], [])) if day else None,
                items=[PlanItemOut.model_validate(item_row_out(i)) for i in items],
                next_day=nxt[0]["d"],
                now_item=now_item,
            )
        resume: ResumeReading | ResumeBlock | None = None
        blk = await rows(conn, _RESUME_BLOCK)
        if blk:
            b = blk[0]
            total = len(b["question_ids"])
            mode = (b["config"] or {}).get("mode", "tutor")
            if b["answered"] < total:
                resume = ResumeBlock(
                    kind="block",
                    block_id=b["id"],
                    mode=mode,
                    answered=b["answered"],
                    total=total,
                    # The timed/exam player resumes its own section and clock from question 1 (segment 09).
                    next_n=min(b["answered"] + 1, total) if mode == "tutor" else 1,
                    diagnostic=(b["config"] or {}).get("purpose") == "diagnostic",
                )
        if resume is None:
            rd = await rows(conn, _RESUME_READING)
            if rd:
                r = rd[0]
                n = await rows(conn, _SECTION_N, (r["chapter_id"], r["section_n"]))
                resume = ResumeReading(
                    kind="reading",
                    chapter_id=r["chapter_id"],
                    chapter_title=r["chapter_title"],
                    section_id=r["section_id"],
                    section_heading=r["section_heading"],
                    section_n=n[0]["n"],
                )
        due = (await cards_due(conn, now=now))["due"]
        suggestions: list[CoachSuggestionOut] = []
        if features.get("coach", False):
            suggestions = [
                coach_out(r)
                for r in await rows(conn, _COACH, (today - dt.timedelta(days=1), today, coach.max_suggestions))
            ]
        pathway = None
        if features.get("pathway", False):
            pathway = [PathwayStageOut.model_validate(r) for r in await rows(conn, _PATHWAY)]
        return TodayOut(
            today=today,
            exam=exam,
            exam_date=prof["exam_date"],
            days_left=(prof["exam_date"] - today).days if prof["exam_date"] else None,
            onboarding_complete=bool(onb.get("completed_at")),
            plan=plan_out,
            resume=resume,
            readiness=ReadinessOut(
                score=ready.score,
                coverage=ready.coverage,
                answered=ready.answered,
                needed_answered=ready.needed_answered,
                needed_coverage=ready.needed_coverage,
                days=coach.readiness_days,
            ),
            systems=SystemsOut(
                window_days=coach.window_days,
                min_attempts=coach.min_attempts,
                weak_below=round(coach.weak_below * 100),
                items=[
                    SystemOut(
                        id=s.id, label=s.label, attempts=s.attempts, correct=s.correct, percent=s.percent, state=s.state
                    )
                    for s in stats
                ],
            ),
            cards=CardsOut(due=due, reviewable=features.get("cards", False)),
            coach=suggestions,
            pathway=pathway,
        )

    out: TodayOut = await as_user(_db(request), user, run)
    return out


# ── Coach suggestions ──

_INSERT_ITEM = (
    "insert into public.plan_items (user_id, day, kind, title, ref_type, ref_id, target_minutes, target_count,"
    " origin, sort) values (%s,%s,%s,%s,%s,%s,%s,%s,'assistant',%s) returning id, day, kind, title, ref_type,"
    " ref_id, target_minutes, target_count, status, origin, sort, plan_day"
)
_SUGGESTION = (
    "select id, for_date, kind, source, title, body, facts, action, status from public.coach_suggestions"
    " where id = %s and user_id = (select auth.uid())"
)


def action_items(action: dict[str, Any], day: dt.date) -> list[dict[str, Any]]:
    """The plan items an accepted suggestion adds (validated shapes only; unknown actions add nothing)."""
    out: list[dict[str, Any]] = []
    for it in action.get("add_items") or []:
        kind = it.get("kind")
        ref_type = it.get("ref_type")
        if kind not in ("read", "questions", "cards") or ref_type not in (
            "weak_spot",
            "qbank_incorrect",
            "cards",
            "chapter",
            "section",
        ):
            continue
        out.append(
            {
                "day": day,
                "kind": kind,
                "title": str(it.get("title") or "")[:200] or kind,
                "ref_type": ref_type,
                "ref_id": (str(it["ref_id"])[:128] if it.get("ref_id") is not None else None),
                "target_minutes": int(it["target_minutes"]) if it.get("target_minutes") is not None else None,
                "target_count": int(it["target_count"]) if it.get("target_count") is not None else None,
            }
        )
    return out


@router.post(
    "/coach/suggestions/{suggestion_id}/accept",
    operation_id="acceptCoachSuggestion",
    summary="Accept a coach suggestion: its stored action adds items to today's plan",
    response_model=CoachActionOut,
    responses={**_ERRORS, **_NOT_FOUND},
)
async def accept_suggestion(
    request: Request,
    suggestion_id: Annotated[uuid.UUID, Path()],
    user: Annotated[CurrentUser, limited("plan_write")],
) -> CoachActionOut | JSONResponse:
    async def read(conn: psycopg.AsyncConnection[Any]) -> tuple[list[Row], dt.date]:
        prof = await _profile(conn)
        return await rows(conn, _SUGGESTION, (suggestion_id,)), local_today(prof["timezone"])

    found, today = await as_user(_db(request), user, read)
    if not found:
        return error_response(404, "This suggestion does not exist.", "not_found")
    s = found[0]
    if s["status"] != "new":
        return JSONResponse(
            CoachActionOut(suggestion=coach_out(s), added_items=[]).model_dump(mode="json"), status_code=200
        )
    items = action_items(s["action"] or {}, today)

    async def apply(conn: psycopg.AsyncConnection[Any]) -> tuple[Row, list[Row]]:
        updated = await rows(
            conn,
            "update public.coach_suggestions set status = 'accepted' where id = %s and user_id = %s and status = 'new'"
            " returning id, for_date, kind, source, title, body, facts, action, status",
            (suggestion_id, user.user_id),
        )
        if not updated:  # accepted twice at once: the first one won
            again = await rows(
                conn,
                "select id, for_date, kind, source, title, body, facts, action, status from public.coach_suggestions"
                " where id = %s and user_id = %s",
                (suggestion_id, user.user_id),
            )
            return again[0], []
        added: list[Row] = []
        base = await rows(
            conn,
            "select coalesce(max(sort), -1)::int as m from public.plan_items where user_id = %s and day = %s",
            (user.user_id, today),
        )
        for n, it in enumerate(items, start=base[0]["m"] + 1):
            added += await rows(
                conn,
                _INSERT_ITEM,
                (
                    user.user_id,
                    it["day"],
                    it["kind"],
                    it["title"],
                    it["ref_type"],
                    it["ref_id"],
                    it["target_minutes"],
                    it["target_count"],
                    n,
                ),
            )
        return updated[0], added

    row, added = await as_service(_db(request), apply)
    return CoachActionOut(
        suggestion=coach_out(row), added_items=[PlanItemOut.model_validate(item_row_out(a)) for a in added]
    )


@router.post(
    "/coach/suggestions/{suggestion_id}/dismiss",
    operation_id="dismissCoachSuggestion",
    summary="Dismiss a coach suggestion",
    response_model=CoachSuggestionOut,
    responses={**_ERRORS, **_NOT_FOUND},
)
async def dismiss_suggestion(
    request: Request,
    suggestion_id: Annotated[uuid.UUID, Path()],
    user: Annotated[CurrentUser, limited("plan_write")],
) -> CoachSuggestionOut | JSONResponse:
    async def run(conn: psycopg.AsyncConnection[Any]) -> list[Row]:
        return await rows(
            conn,
            "update public.coach_suggestions set status = 'dismissed' where id = %s and user_id = (select auth.uid())"
            " and status = 'new' returning id, for_date, kind, source, title, body, facts, action, status",
            (suggestion_id,),
        ) or await rows(conn, _SUGGESTION, (suggestion_id,))

    out = await as_user(_db(request), user, run)
    if not out:
        return error_response(404, "This suggestion does not exist.", "not_found")
    return coach_out(out[0])


def install_plan_routes(app: Any) -> None:
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
    app.include_router(router)


__all__ = ["install_plan_routes", "router"]
