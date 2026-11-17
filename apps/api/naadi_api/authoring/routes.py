"""Admin API of the authoring pipeline and the human review queue (segment 21), all under `/api/admin/review`.

    GET  /summary                       counts per queue filter (what the admin hub shows)
    GET  /items?filter=                 the queue
    GET  /items/{id}                    one item with its key, lint and solver findings, gates, reviews, statistics
    POST /items/{id}/reviews            a reviewer's Part C ratings and decision (publishes when the rules say so)
    POST /items/{id}/checks             re-run the linter (and, on request, the blind solver)
    POST /items/{id}/resubmit           a revised draft goes back to review (a new round)
    POST /items/{id}/psychometrics      keep / revise / retire a flagged published item
    GET  /gaps, /taxonomy               what to author next
    POST /runs, GET /runs               start an authoring run (background), recent runs
    POST /psychometrics/run             recompute item statistics now

Admin only (ADMIN_EMAILS, session re-checked). The owner is the only user, so one approval publishes; the linter,
the source check and the model cross-check must all have passed first (409 `publish_blocked` with the codes).
Answers are visible here by design: this is the reviewer's surface and no student endpoint reaches it. Errors
carry a stable `code`; the web words them with `ui_copy`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ..auth import AdminUser
from ..config_api.schemas import AuthoringRules
from ..db import Database, DatabaseNotConfigured
from ..embeddings.jobs import schedule_reindex
from ..errors import ErrorResponse, FieldError, error_response
from ..logs import get_logger
from . import psychometrics_job, review_store
from .checks import CallBudget, blind_solve
from .deps import build_deps
from .jobs import AuthoringJobs
from .lint import lab_refs
from .models import QuestionReviewIn
from .pipeline import DIFFICULTY_CYCLE, Deps
from .review import ReviewRefused, ReviewRow, check_submission, publish_blockers, resolve, visible_rows
from .selection import CellError, TaxNode, build_cell
from .store import PgAuthoringStore, service_conn

log = get_logger("naadi.authoring.routes")
router = APIRouter(prefix="/api/admin/review", tags=["admin"])

QueueFilter = Literal["ai_drafts", "seed_spot_check", "psychometric_flags", "adjudication", "needs_check", "all"]
ItemId = Annotated[str, Path(pattern=r"^[A-Za-z0-9._:-]{1,80}$")]
_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    403: {"model": ErrorResponse, "description": "Not an admin"},
    404: {"model": ErrorResponse, "description": "item_not_found"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "authoring_unavailable"},
}


class AuthoringError(Exception):
    def __init__(self, status: int, code: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.extra = extra or {}


# ── payloads ──


class ReviewSummary(BaseModel):
    ai_drafts: int
    seed_spot_check: int
    psychometric_flags: int
    adjudication: int
    needs_check: int
    required_approvals: int


class ReviewQueueItem(BaseModel):
    id: str
    exam: str
    status: str
    origin: Literal["ai", "seed"]
    difficulty: str | None
    stem_preview: str
    errors: int
    warnings: int
    state: str
    submitted: int
    needed: int
    mine_submitted: bool
    psych_flags: list[str]


class ReviewQueueList(BaseModel):
    items: list[ReviewQueueItem]


class ReviewFinding(BaseModel):
    rule: str
    severity: Literal["error", "warn", "info"]
    target: str
    params: dict[str, JsonValue]


class AuthoringTaxonomyRef(BaseModel):
    id: str
    kind: str
    label: str


class ReviewAuthorInfo(BaseModel):
    model: str | None
    prompt_hash: str | None
    run_id: str | None
    excerpt_ids: list[str]
    at: str | None


class ReviewSolverOut(BaseModel):
    agrees: bool | None
    choice: str | None
    confidence: float | None
    defensibility: dict[str, float]
    cover_matches: bool | None
    model: str | None
    independent: bool
    error: str | None


class ReviewGatesOut(BaseModel):
    lint_ok: bool
    source_ok: bool
    cross_check_ok: bool
    independent: bool
    blockers: list[str]


class ReviewResolutionOut(BaseModel):
    state: str
    needed: int
    submitted: int
    round: int


class ReviewRatingsOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    accuracy: str | None = None
    single_best_answer: bool | None = None
    cognitive_level: str | None = None
    flaws: list[str] = Field(default_factory=list)
    difficulty: str | None = None
    taxonomy_ok: bool | None = None


class ReviewEntryOut(BaseModel):
    id: int
    round: int
    decision: Literal["approve", "revise", "reject"]
    adjudication: bool
    request_second: bool
    is_mine: bool
    ratings: ReviewRatingsOut
    note: str | None
    created_at: dt.datetime


class ReviewPermission(BaseModel):
    allowed: bool
    reason: str | None = None


class ItemStatsOut(BaseModel):
    n: int
    p_correct: float | None
    point_biserial: float | None
    option_counts: dict[str, int]
    median_seconds: float | None
    report_count: int
    computed_at: str | None
    flags: list[str]
    can_act: bool


class ReviewOptionOut(BaseModel):
    label: str
    html: str


class ReviewItemDetail(BaseModel):
    id: str
    exam: str
    status: str
    origin: Literal["ai", "seed"]
    difficulty: str | None
    stem_html: str
    options: list[ReviewOptionOut]
    answer: str
    answer_html: str | None
    explanation_html: str | None
    why_not_html: str | None
    educational_objective: str | None
    taxonomy: list[AuthoringTaxonomyRef]
    source_id: str | None
    created_at: dt.datetime
    author: ReviewAuthorInfo | None
    lint: list[ReviewFinding]
    solver: ReviewSolverOut | None
    solver_findings: list[ReviewFinding]
    gates: ReviewGatesOut
    resolution: ReviewResolutionOut
    reviews: list[ReviewEntryOut]
    hidden_reviews: int
    can_review: ReviewPermission
    stats: ItemStatsOut | None
    required_approvals: int


class ReviewSubmitResult(BaseModel):
    outcome: Literal["recorded", "published", "revise", "rejected", "adjudication"]
    item: ReviewItemDetail


class ReviewChecksIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    solver: bool = False


class ReviewPsychometricsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["keep", "revise", "retire"]


class AuthoringGap(BaseModel):
    exam: str
    topic_id: str
    label: str
    published: int
    pending: int


class AuthoringGapList(BaseModel):
    items: list[AuthoringGap]
    min_published: int


class AuthoringTaxonomyList(BaseModel):
    systems: list[AuthoringTaxonomyRef]
    seconds: list[AuthoringTaxonomyRef]
    competencies: list[AuthoringTaxonomyRef]
    topics: list[AuthoringTaxonomyRef]


class AuthoringRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exam: Literal["step1", "step2ck"]
    topic_id: str = Field(max_length=120)
    second_id: str | None = Field(default=None, max_length=120)
    competency_id: str | None = Field(default=None, max_length=120)
    objective: str | None = Field(default=None, max_length=300)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    count: int = Field(default=1, ge=1, le=3)


class AuthoringRunJob(BaseModel):
    id: uuid.UUID
    status: Literal["running", "done", "failed"]
    count: int
    outcomes: list[str]
    started_at: dt.datetime
    finished_at: dt.datetime | None
    error: str | None


class AuthoringRunRow(BaseModel):
    id: uuid.UUID
    trigger: str
    cell: dict[str, JsonValue]
    difficulty: str | None
    outcome: str
    attempts: int
    llm_calls: int
    question_id: str | None
    created_at: dt.datetime


class AuthoringRunList(BaseModel):
    active: list[AuthoringRunJob]
    items: list[AuthoringRunRow]


class PsychometricsRunOut(BaseModel):
    items: int
    flagged: int
    updated: int


# ── plumbing ──


def _db(request: Request) -> Database:
    d: Database = request.app.state.db
    return d


async def _rules(request: Request) -> AuthoringRules:
    return AuthoringRules.model_validate(await request.app.state.app_config.get("authoring"))


async def _labs(request: Request) -> list[Any]:
    try:
        return lab_refs(await request.app.state.app_config.get("lab_values"))
    except Exception:  # noqa: BLE001 - the lab-value rule is skipped without its table
        return []


def _hit(request: Request, admin_id: uuid.UUID) -> None:
    limiters = getattr(request.app.state, "rate_limiters", None)
    if limiters and "admin" in limiters:
        limiters["admin"].hit(str(admin_id))


def _gateway(request: Request) -> Any:
    gw = getattr(request.app.state, "authoring_gateway", None)
    if gw is not None:
        return gw
    from ..llm import get_gateway

    try:
        return get_gateway()
    except RuntimeError as exc:
        raise AuthoringError(503, "authoring_unavailable") from exc


async def _taxonomy_and_sources(db: Database) -> tuple[dict[str, TaxNode], dict[str, Any]]:
    store = PgAuthoringStore(db)
    return await store.taxonomy(), await store.sources()


def _finding(d: dict[str, Any]) -> ReviewFinding:
    return ReviewFinding(
        rule=d["rule"], severity=d["severity"], target=d.get("target", ""), params=d.get("params") or {}
    )


def _stats_out(item: dict[str, Any], rules: AuthoringRules) -> ItemStatsOut | None:
    st = item["stats"] or {}
    if not st:
        return None
    flags = review_store.psych_flags_of(item)
    n = int(st.get("n") or 0)
    return ItemStatsOut(
        n=n,
        p_correct=st.get("pCorrect"),
        point_biserial=st.get("pointBiserial"),
        option_counts={k: int(v) for k, v in (st.get("optionCounts") or {}).items()},
        median_seconds=st.get("medianSeconds"),
        report_count=int(st.get("reportCount") or 0),
        computed_at=st.get("computedAt"),
        flags=flags,
        can_act=n >= rules.act_min_n,
    )


async def build_detail(
    conn: Any,
    item: dict[str, Any],
    me: uuid.UUID,
    rules: AuthoringRules,
    taxonomy: dict[str, TaxNode],
    sources: dict[str, Any],
    labs: list[Any],
) -> ReviewItemDetail:
    review = item["review"] or {}
    round_ = int(review.get("round", 1))
    all_rows = await review_store.reviews_of(conn, item["id"], round_)
    past = [r for r in await review_store.history_of(conn, item["id"]) if r["round"] < round_]
    rr = [review_store._row(r) for r in all_rows]
    res = resolve(rr, rules.required_approvals)
    visible = {id(r) for r in visible_rows(me, rr, rules.required_approvals)}
    shown = [r for r, parsed in zip(all_rows, rr, strict=True) if id(parsed) in visible]
    report = review_store.lint_item(item, {k: v.kind for k, v in taxonomy.items()}, sources, labs)
    gates = review_store.gates_of(item, report)
    solver = review.get("solver") or None
    author = review.get("authorModel")
    permission = ReviewPermission(allowed=True)
    status = item["status"]
    if status in ("draft", "retired"):
        permission = ReviewPermission(allowed=False, reason="status_not_reviewable")
    elif status == "published" and review.get("humanExpertReviewed") is not False:
        permission = ReviewPermission(allowed=False, reason="already_reviewed")
    elif res.decided:
        permission = ReviewPermission(allowed=False, reason="round_closed")
    elif any(r["reviewer_user_id"] == me for r in all_rows):
        permission = ReviewPermission(allowed=False, reason="already_reviewed_by_you")

    def out(r: dict[str, Any]) -> ReviewEntryOut:
        return ReviewEntryOut(
            id=r["id"],
            round=r["round"],
            decision=r["decision"],
            adjudication=r["adjudication"],
            request_second=r["request_second"],
            is_mine=r["reviewer_user_id"] == me,
            ratings=ReviewRatingsOut.model_validate(r["ratings"] or {}),
            note=r["note"],
            created_at=r["created_at"],
        )

    return ReviewItemDetail(
        id=item["id"],
        exam=item["exam"],
        status=status,
        origin=review_store.origin_of(item),
        difficulty=item["difficulty"],
        stem_html=item["stem_html"],
        options=[ReviewOptionOut(label=str(o["label"]), html=str(o["html"])) for o in item["options"]],
        answer=str(item["answer"]).strip(),
        answer_html=item["answer_html"],
        explanation_html=item["explanation_html"],
        why_not_html=item["why_not_html"],
        educational_objective=item["educational_objective"],
        taxonomy=[
            AuthoringTaxonomyRef(id=t, kind=taxonomy[t].kind, label=taxonomy[t].label)
            for t in item["taxonomy_ids"]
            if t in taxonomy
        ],
        source_id=item["source_id"],
        created_at=item["created_at"],
        author=ReviewAuthorInfo(
            model=author.get("model"),
            prompt_hash=author.get("promptHash"),
            run_id=author.get("runId"),
            excerpt_ids=list(author.get("excerptIds") or []),
            at=author.get("at"),
        )
        if author
        else None,
        lint=[_finding(f.as_dict()) for f in report.findings],
        solver=ReviewSolverOut(
            agrees=solver.get("agrees"),
            choice=solver.get("choice"),
            confidence=solver.get("confidence"),
            defensibility={k: float(v) for k, v in (solver.get("defensibility") or {}).items()},
            cover_matches=solver.get("coverMatches"),
            model=solver.get("model"),
            independent=bool(solver.get("independent")),
            error=solver.get("error"),
        )
        if solver
        else None,
        solver_findings=[_finding(f) for f in (solver or {}).get("findings", [])],
        gates=ReviewGatesOut(
            lint_ok=gates.lint_ok,
            source_ok=gates.source_ok,
            cross_check_ok=gates.cross_check_ok,
            independent=gates.independent,
            blockers=publish_blockers(gates),
        ),
        resolution=ReviewResolutionOut(state=res.state, needed=res.needed, submitted=res.submitted, round=round_),
        reviews=[out(r) for r in [*past, *shown]],
        hidden_reviews=len(all_rows) - len(shown),
        can_review=permission,
        stats=_stats_out(item, rules),
        required_approvals=rules.required_approvals,
    )


async def _load(request: Request, conn: Any, item_id: str, me: uuid.UUID) -> ReviewItemDetail:
    item = await review_store.get_item(conn, item_id)
    if item is None:
        raise AuthoringError(404, "item_not_found")
    rules = await _rules(request)
    taxonomy, sources = await _taxonomy_and_sources(_db(request))
    return await build_detail(conn, item, me, rules, taxonomy, sources, await _labs(request))


# ── reads ──


@router.get("/summary", operation_id="getReviewSummary", response_model=ReviewSummary, responses=_ERRORS)
async def summary(request: Request, admin: AdminUser) -> ReviewSummary:
    _hit(request, admin.user_id)
    rules = await _rules(request)
    try:
        async with service_conn(_db(request)) as conn:
            c = await review_store.counts(conn, rules.required_approvals)
    except DatabaseNotConfigured as exc:
        raise AuthoringError(503, "authoring_unavailable") from exc
    return ReviewSummary(**c, required_approvals=rules.required_approvals)


@router.get("/items", operation_id="listReviewItems", response_model=ReviewQueueList, responses=_ERRORS)
async def items(
    request: Request,
    admin: AdminUser,
    filter: Annotated[QueueFilter, Query()] = "ai_drafts",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> ReviewQueueList:
    _hit(request, admin.user_id)
    rules = await _rules(request)
    taxonomy, sources = await _taxonomy_and_sources(_db(request))
    async with service_conn(_db(request)) as conn:
        rows = await review_store.list_queue(
            conn,
            filter,
            admin.user_id,
            rules.required_approvals,
            {k: v.kind for k, v in taxonomy.items()},
            sources,
            await _labs(request),
            limit,
            offset,
        )
    return ReviewQueueList(
        items=[
            ReviewQueueItem(
                id=r.id,
                exam=r.exam,
                status=r.status,
                origin=r.origin,
                difficulty=r.difficulty,
                stem_preview=r.stem_preview,
                errors=r.flags["errors"],
                warnings=r.flags["warnings"],
                state=r.state,
                submitted=r.submitted,
                needed=r.needed,
                mine_submitted=r.mine_submitted,
                psych_flags=r.psych_flags,
            )
            for r in rows
        ]
    )


@router.get("/items/{item_id}", operation_id="getReviewItem", response_model=ReviewItemDetail, responses=_ERRORS)
async def item(request: Request, admin: AdminUser, item_id: ItemId) -> ReviewItemDetail:
    _hit(request, admin.user_id)
    async with service_conn(_db(request)) as conn:
        return await _load(request, conn, item_id, admin.user_id)


# ── writes ──


@router.post(
    "/items/{item_id}/reviews",
    operation_id="submitReview",
    response_model=ReviewSubmitResult,
    responses={
        **_ERRORS,
        409: {
            "model": ErrorResponse,
            "description": "publish_blocked, round_closed, already_reviewed, status_not_reviewable",
        },
        422: {"model": ErrorResponse, "description": "validation_error or approve_contradicts_ratings"},
    },
)
async def submit_review(
    request: Request, admin: AdminUser, item_id: ItemId, body: QuestionReviewIn
) -> ReviewSubmitResult:
    _hit(request, admin.user_id)
    rules = await _rules(request)
    taxonomy, sources = await _taxonomy_and_sources(_db(request))
    labs = await _labs(request)
    async with service_conn(_db(request)) as conn:
        it = await review_store.lock_item(conn, item_id)
        if it is None:
            raise AuthoringError(404, "item_not_found")
        review = it["review"] or {}
        seed_check = it["status"] == "published" and review.get("humanExpertReviewed") is False
        if it["status"] not in ("in_review", "auto_checked") and not seed_check:
            raise AuthoringError(409, "status_not_reviewable")
        round_ = int(review.get("round", 1))
        rows = await review_store.reviews_of(conn, item_id, round_)
        parsed = [review_store._row(r) for r in rows]
        try:
            adjudication = check_submission(
                admin.user_id, parsed, rules.required_approvals, body.ratings, body.decision
            )
        except ReviewRefused as exc:
            raise AuthoringError(422 if exc.code == "approve_contradicts_ratings" else 409, exc.code) from exc
        if body.decision == "approve":
            report = review_store.lint_item(it, {k: v.kind for k, v in taxonomy.items()}, sources, labs)
            blockers = publish_blockers(review_store.gates_of(it, report))
            if blockers:
                raise AuthoringError(409, "publish_blocked", {"blockers": blockers})
        await review_store.insert_review(
            conn,
            item_id,
            admin.user_id,
            round_,
            body.decision,
            body.ratings,
            body.note,
            body.request_second,
            adjudication,
        )
        parsed.append(
            ReviewRow(
                admin.user_id, body.decision, adjudication, body.request_second, body.ratings.model_dump(mode="json")
            )
        )
        res = resolve(parsed, rules.required_approvals)
        outcome = (
            "recorded"
            if not res.decided
            else {"approved": "published", "revise": "revise", "rejected": "rejected"}[res.state]
        )
        if res.state == "adjudication":
            outcome = "adjudication"
        if res.decided:
            fresh = await review_store.lock_item(conn, item_id)
            assert fresh is not None
            rows_all = await review_store.reviews_of(conn, item_id, round_)
            await review_store.apply_outcome(conn, fresh, res, [review_store._row(r) for r in rows_all], admin.user_id)
        detail = await _load(request, conn, item_id, admin.user_id)
    if outcome in ("published", "rejected", "revise"):
        schedule_reindex(request.app, ("questions",), ref_ids=[item_id])
    log.info("authoring.review", item=item_id, decision=body.decision, outcome=outcome)
    return ReviewSubmitResult(outcome=outcome, item=detail)


@router.post(
    "/items/{item_id}/checks", operation_id="runReviewChecks", response_model=ReviewItemDetail, responses=_ERRORS
)
async def checks(request: Request, admin: AdminUser, item_id: ItemId, body: ReviewChecksIn) -> ReviewItemDetail:
    """Re-run the linter; with `solver`, the blind solver too (model calls, job reserve). The result is stored on the
    item so the publish gate reads it."""
    _hit(request, admin.user_id)
    taxonomy, sources = await _taxonomy_and_sources(_db(request))
    labs = await _labs(request)
    async with service_conn(_db(request)) as conn:
        it = await review_store.get_item(conn, item_id)
        if it is None:
            raise AuthoringError(404, "item_not_found")
        report = review_store.lint_item(it, {k: v.kind for k, v in taxonomy.items()}, sources, labs)
        solver_json = None
        if body.solver:
            gw = _gateway(request)
            row = {**it, "options": it["options"]}
            author_model = ((it["review"] or {}).get("authorModel") or {}).get("model")
            if author_model is None:
                # A seed item has no recorded author: the nearest author model of the configured chain stands in, so
                # the independence check still compares against a real family.
                cfg = await gw.config()
                author_model = cfg.roles["author"].chain[0].model
            try:
                res = await blind_solve(gw, row, _ctx(), CallBudget(), author_model)
            except Exception as exc:  # budget and provider failures become a stable code
                from ..llm_budget import BudgetExhausted

                raise AuthoringError(503 if isinstance(exc, BudgetExhausted) else 502, "solver_unavailable") from exc
            solver_json = res.as_dict()
        await review_store.store_checks(conn, item_id, report.as_dict(), solver_json)
        if it["status"] == "auto_checked":
            fresh = await review_store.get_item(conn, item_id)
            assert fresh is not None
            gates = review_store.gates_of(
                fresh, review_store.lint_item(fresh, {k: v.kind for k, v in taxonomy.items()}, sources, labs)
            )
            if not publish_blockers(gates):
                await conn.execute("update public.questions set status = 'in_review' where id = %s", (item_id,))
        return await _load(request, conn, item_id, admin.user_id)


def _ctx() -> Any:
    from ..llm import CallContext

    return CallContext(purpose="job", run_id=f"recheck:{uuid.uuid4().hex[:8]}")


@router.post(
    "/items/{item_id}/resubmit", operation_id="resubmitReviewItem", response_model=ReviewItemDetail, responses=_ERRORS
)
async def resubmit(request: Request, admin: AdminUser, item_id: ItemId) -> ReviewItemDetail:
    """A revised draft goes back to review as a new round (statistics reset). The linter must pass first."""
    _hit(request, admin.user_id)
    taxonomy, sources = await _taxonomy_and_sources(_db(request))
    labs = await _labs(request)
    async with service_conn(_db(request)) as conn:
        it = await review_store.lock_item(conn, item_id)
        if it is None:
            raise AuthoringError(404, "item_not_found")
        if it["status"] != "draft":
            raise AuthoringError(409, "status_not_reviewable")
        report = review_store.lint_item(it, {k: v.kind for k, v in taxonomy.items()}, sources, labs)
        if not report.ok:
            raise AuthoringError(409, "publish_blocked", {"blockers": ["lint_errors"]})
        gates = review_store.gates_of(it, report)
        # A revised item keeps its earlier cross-check only if it is unchanged; the reviewer re-runs it from the UI.
        await review_store.resubmit(conn, it, "in_review" if not publish_blockers(gates) else "auto_checked")
        return await _load(request, conn, item_id, admin.user_id)


@router.post(
    "/items/{item_id}/psychometrics",
    operation_id="actOnPsychometrics",
    response_model=ReviewItemDetail,
    responses=_ERRORS,
)
async def psychometric_action(
    request: Request, admin: AdminUser, item_id: ItemId, body: ReviewPsychometricsIn
) -> ReviewItemDetail:
    """Keep (acknowledge the flags), revise (back to draft) or retire a flagged item. Retiring needs `act_min_n`
    responses: below that a flag is all the data supports."""
    _hit(request, admin.user_id)
    rules = await _rules(request)
    async with service_conn(_db(request)) as conn:
        it = await review_store.lock_item(conn, item_id)
        if it is None:
            raise AuthoringError(404, "item_not_found")
        if it["status"] != "published" or not review_store.psych_flags_of(it):
            raise AuthoringError(409, "not_flagged")
        stats = it["stats"] or {}
        if body.action == "retire" and int(stats.get("n") or 0) < rules.act_min_n:
            raise AuthoringError(409, "too_few_responses")
        from psycopg.types.json import Jsonb

        review = dict(it["review"] or {})
        if body.action == "keep":
            review["psychAckAt"] = stats.get("computedAt")
            await conn.execute("update public.questions set review = %s where id = %s", (Jsonb(review), item_id))
        else:
            status = "retired" if body.action == "retire" else "draft"
            review["psychAction"] = {"action": body.action, "by": str(admin.user_id), "n": stats.get("n")}
            await conn.execute(
                "update public.questions set status = %s, review = %s where id = %s", (status, Jsonb(review), item_id)
            )
        detail = await _load(request, conn, item_id, admin.user_id)
    if body.action != "keep":
        schedule_reindex(request.app, ("questions",), ref_ids=[item_id])
    return detail


# ── what to author ──


@router.get("/gaps", operation_id="listAuthoringGaps", response_model=AuthoringGapList, responses=_ERRORS)
async def gaps(request: Request, admin: AdminUser) -> AuthoringGapList:
    _hit(request, admin.user_id)
    rules = await _rules(request)
    found = await PgAuthoringStore(_db(request)).gaps(rules.target_exams, rules.min_published_per_topic, 60)
    return AuthoringGapList(
        items=[
            AuthoringGap(exam=g.exam, topic_id=g.topic_id, label=g.label, published=g.published, pending=g.pending)
            for g in found
        ],
        min_published=rules.min_published_per_topic,
    )


@router.get("/taxonomy", operation_id="getAuthoringTaxonomy", response_model=AuthoringTaxonomyList, responses=_ERRORS)
async def taxonomy_for(
    request: Request, admin: AdminUser, exam: Annotated[Literal["step1", "step2ck"], Query()]
) -> AuthoringTaxonomyList:
    _hit(request, admin.user_id)
    tax = await PgAuthoringStore(_db(request)).taxonomy()
    second = "discipline" if exam == "step1" else "clinical_science"

    def pick(kind: str) -> list[AuthoringTaxonomyRef]:
        nodes = sorted((n for n in tax.values() if n.kind == kind and exam in n.exam), key=lambda n: (n.sort, n.id))
        return [AuthoringTaxonomyRef(id=n.id, kind=n.kind, label=n.label) for n in nodes]

    return AuthoringTaxonomyList(
        systems=pick("system"), seconds=pick(second), competencies=pick("competency"), topics=pick("topic")
    )


def _job_out(j: Any) -> AuthoringRunJob:
    return AuthoringRunJob(
        id=j.id,
        status=j.status,
        count=j.count,
        outcomes=j.outcomes,
        started_at=j.started_at,
        finished_at=j.finished_at,
        error=j.error,
    )


def _jobs(request: Request) -> AuthoringJobs:
    jobs: AuthoringJobs | None = getattr(request.app.state, "authoring_jobs", None)
    if jobs is None:
        jobs = AuthoringJobs()
        request.app.state.authoring_jobs = jobs
    return jobs


async def deps_for(request: Request, rules: AuthoringRules) -> Deps:
    return await build_deps(
        _db(request), _gateway(request), request.app.state.settings, request.app.state.app_config, rules
    )


@router.post(
    "/runs",
    operation_id="startAuthoringRun",
    status_code=202,
    response_model=AuthoringRunJob,
    responses={
        **_ERRORS,
        409: {"model": ErrorResponse, "description": "run_active"},
        422: {"model": ErrorResponse, "description": "cell_invalid"},
    },
)
async def start_run(request: Request, admin: AdminUser, body: AuthoringRunIn) -> AuthoringRunJob:
    """Draft `count` items for one taxonomy cell in the background. They land in the review queue; nothing is
    published. Model calls go through the gateway's free chains and the job reserve."""
    _hit(request, admin.user_id)
    rules = await _rules(request)
    store = PgAuthoringStore(_db(request))
    taxonomy = await store.taxonomy()
    try:
        cell = build_cell(
            taxonomy,
            body.exam,
            body.topic_id,
            second_id=body.second_id,
            competency_id=body.competency_id,
            common_second=await store.common_second(body.exam, body.topic_id),
        )
    except CellError as exc:
        raise AuthoringError(422, "cell_invalid", {"reason": exc.code}) from exc
    deps = await deps_for(request, rules)
    objective = (
        body.objective
        or f"Apply the key concept of {taxonomy[cell.topic_id].label} to a clinical or experimental scenario."
    )
    job = _jobs(request).start(deps, cell, objective, body.difficulty or DIFFICULTY_CYCLE[0], body.count, admin.user_id)
    if job is None:
        raise AuthoringError(409, "run_active")
    return _job_out(job)


@router.get("/runs", operation_id="listAuthoringRuns", response_model=AuthoringRunList, responses=_ERRORS)
async def runs(request: Request, admin: AdminUser) -> AuthoringRunList:
    _hit(request, admin.user_id)
    async with service_conn(_db(request)) as conn:
        cur = await conn.execute(
            "select id, trigger, cell, difficulty, outcome, attempts, llm_calls, question_id, created_at"
            " from public.authoring_runs order by created_at desc limit 50"
        )
        rows = await cur.fetchall()
    return AuthoringRunList(
        active=[_job_out(j) for j in sorted(_jobs(request).jobs.values(), key=lambda j: j.started_at, reverse=True)],
        items=[AuthoringRunRow(**r) for r in rows],
    )


@router.post(
    "/psychometrics/run", operation_id="runPsychometrics", response_model=PsychometricsRunOut, responses=_ERRORS
)
async def run_psychometrics(request: Request, admin: AdminUser) -> PsychometricsRunOut:
    _hit(request, admin.user_id)
    stats = await psychometrics_job.run(_db(request), await _rules(request))
    return PsychometricsRunOut(**stats.as_dict())


# ── errors and install ──


async def _authoring_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AuthoringError)
    details = [
        FieldError(loc=[key, i], msg=str(v), type=exc.code)
        for key, vals in exc.extra.items()
        for i, v in enumerate(vals if isinstance(vals, list) else [vals])
    ]
    return error_response(exc.status, exc.code, exc.code, details=details or None)


def install_authoring_routes(app: Any) -> None:
    app.add_exception_handler(AuthoringError, _authoring_error)
    app.state.authoring_jobs = AuthoringJobs()
    app.include_router(router)


__all__ = ["install_authoring_routes", "router"]
