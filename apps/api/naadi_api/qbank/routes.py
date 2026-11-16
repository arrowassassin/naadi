"""Qbank: pool counts, block building (manual and weak-spot), tutor-mode questions, server-side grading.

Answer-leak rule (CLAUDE.md, HANDOFF 6.3): `answer`, `answer_html`, `explanation_html`, `why_not_html` and
`educational_objective` are not readable by clients at all (column grants). They are read here with the pool's
privileged role, and only after the caller has a *graded* attempt on that question in that block and the block's
mode shows explanations after each question. Everything else is read as the signed-in user, so RLS decides what
exists; every privileged statement filters on the caller's id explicitly.
"""

from __future__ import annotations

import datetime as dt
import re
import secrets
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Path, Query, Request
from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb
from starlette.exceptions import HTTPException

from ..auth import CurrentUser, get_account_user
from ..auth_db import user_tx
from ..auth_limits import RateLimiter
from ..config_api.service import AppConfigService
from ..db import Database, DatabaseNotConfigured
from ..errors import ErrorResponse
from ..logs import get_logger
from .models import (
    ID_PATTERN,
    TAXONOMY_PATTERN,
    AttemptIn,
    AttemptOut,
    AttemptState,
    BlockConfig,
    BlockCreate,
    BlockDetail,
    BlockLimits,
    BlockList,
    BlockQuestionState,
    BlockSummary,
    Difficulty,
    ExamChoice,
    Explanation,
    FlagIn,
    FlagOut,
    LabGroupOut,
    LabRowOut,
    LabValuesOut,
    LessonLink,
    ModeChoice,
    PoolFacet,
    PoolResponse,
    QuestionBody,
    QuestionOption,
    QuestionView,
    SingleBlockCreate,
    SourceRef,
    Status,
    WeakCellOut,
    WeakSpotBlock,
    WeakSpotCreate,
)
from .selection import GradedAttempt as Graded
from .selection import (
    History,
    PoolQuestion,
    choose,
    facet_counts,
    filter_pool,
    weak_cells,
    weak_spot_order,
)

log = get_logger("naadi.qbank")

router = APIRouter(prefix="/api", tags=["qbank"])

Row = dict[str, Any]
UNAVAILABLE = "Practice questions cannot be reached right now. Try again in a moment."
WEAK_SPOT_DEFAULT = 20
SECOND_GROUP_KINDS = ("discipline", "clinical_science")

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Database unavailable"},
}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse, "description": "Not found or not visible"}}
_INVALID: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Invalid request or a pool too small for the block"}
}


def default_limiters() -> dict[str, RateLimiter]:
    return {
        "qbank_read": RateLimiter(300, 60.0),
        "qbank_write": RateLimiter(120, 60.0),
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


@asynccontextmanager
async def _service_tx(db: Database) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    """A transaction with the pool's own (privileged) role: the only way to read answer columns or to grade."""
    if not db.configured:
        raise DatabaseNotConfigured("DATABASE_URL is not set")
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    async with db.pool.connection(timeout=5.0) as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]
        try:
            async with conn.transaction():
                await conn.execute("set local statement_timeout = 5000")
                yield conn
        finally:
            conn.row_factory = tuple_row


async def _guard[T](fn: Callable[[], Awaitable[T]]) -> T:
    try:
        return await fn()
    except DatabaseNotConfigured as exc:
        raise HTTPException(503, UNAVAILABLE) from exc
    except (psycopg.OperationalError, TimeoutError) as exc:
        log.warning("qbank_db_unavailable", error=type(exc).__name__)
        raise HTTPException(503, UNAVAILABLE) from exc


async def as_user[T](db: Database, user: CurrentUser, fn: Callable[[psycopg.AsyncConnection[Any]], Awaitable[T]]) -> T:
    async def run() -> T:
        async with user_tx(db, user.user_id, deadline_s=8.0) as conn:
            return await fn(conn)

    return await _guard(run)


async def as_service[T](db: Database, fn: Callable[[psycopg.AsyncConnection[Any]], Awaitable[T]]) -> T:
    async def run() -> T:
        async with _service_tx(db) as conn:
            return await fn(conn)

    return await _guard(run)


async def _rows(
    conn: psycopg.AsyncConnection[Any], query: str, params: tuple[Any, ...] | dict[str, Any] = ()
) -> list[Row]:
    cur = await conn.execute(query, params)
    return [dict(r) for r in await cur.fetchall()]


async def _first(
    conn: psycopg.AsyncConnection[Any], query: str, params: tuple[Any, ...] | dict[str, Any] = ()
) -> Row | None:
    rows = await _rows(conn, query, params)
    return rows[0] if rows else None


# ── Config ──


async def _rules(request: Request) -> dict[str, Any]:
    rules: dict[str, Any] = await _config(request).get("exam_rules")
    return rules


async def _timed_available(request: Request, user: CurrentUser) -> bool:
    s = request.app.state.settings
    return await _config(request).feature_enabled("qbank_timed", is_admin=user.is_admin, app_env=s.app_env)


def _check_exam(rules: dict[str, Any], exam: str) -> None:
    if exam not in rules["exams"]:
        raise HTTPException(422, "That exam is not available for practice.")


# ── Reads under RLS ──

_POOL = "select id, taxonomy_ids, difficulty from public.questions where exam = %s order by id"
_HISTORY = """
select question_id,
  bool_or(chosen is not null and correct is not null) as answered,
  (array_agg(correct order by created_at desc, id desc) filter (where correct is not null))[1] as last_correct,
  (array_agg(flagged order by created_at desc, id desc))[1] as flagged
from public.attempts where user_id = (select auth.uid())
group by question_id
"""
_TAXONOMY = """
select id, kind, label from public.taxonomy_nodes where %s = any(exam)
order by sort, label
"""


async def _load_pool(conn: psycopg.AsyncConnection[Any], exam: str) -> tuple[list[PoolQuestion], dict[str, History]]:
    questions = [
        PoolQuestion(r["id"], tuple(r["taxonomy_ids"]), r["difficulty"]) for r in await _rows(conn, _POOL, (exam,))
    ]
    history = {
        r["question_id"]: History(bool(r["answered"]), r["last_correct"], bool(r["flagged"]))
        for r in await _rows(conn, _HISTORY)
    }
    return questions, history


async def _load_taxonomy(conn: psycopg.AsyncConnection[Any], exam: str) -> list[Row]:
    return await _rows(conn, _TAXONOMY, (exam,))


@router.get(
    "/qbank/pool",
    operation_id="getQbankPool",
    summary="How many questions match each Builder filter (live counts from the caller's attempts and the taxonomy)",
    response_model=PoolResponse,
    responses={**_ERRORS, **_INVALID},
)
async def get_pool(
    request: Request,
    user: Annotated[CurrentUser, limited("qbank_read")],
    exam: Annotated[str | None, Query(pattern=r"^[a-z0-9]{2,16}$")] = None,
    systems: Annotated[list[str] | None, Query(max_length=40)] = None,
    disciplines: Annotated[list[str] | None, Query(max_length=40)] = None,
    status: Status = "all",
    difficulty: Difficulty | None = None,
) -> PoolResponse:
    rules = await _rules(request)
    exams = [ExamChoice(id=k, label=v["label"]) for k, v in rules["exams"].items()]
    timed_ok = await _timed_available(request, user)
    for ids in (systems or [], disciplines or []):
        if any(not _match_taxonomy(i) for i in ids):
            raise HTTPException(422, "Invalid taxonomy id.")

    async def run(conn: psycopg.AsyncConnection[Any]) -> PoolResponse:
        chosen = exam or ""
        if not chosen:
            prof = await _rows(conn, "select exam_target from public.profiles where user_id = (select auth.uid())")
            target = prof[0]["exam_target"] if prof else None
            chosen = str(target) if target in rules["exams"] else next(iter(rules["exams"]))
        _check_exam(rules, chosen)
        questions, history = await _load_pool(conn, chosen)
        nodes = await _load_taxonomy(conn, chosen)
        sys_nodes = [n for n in nodes if n["kind"] == "system"]
        disc_nodes = [n for n in nodes if n["kind"] in SECOND_GROUP_KINDS]
        f = facet_counts(
            questions,
            history,
            systems=systems or [],
            disciplines=disciplines or [],
            status=status,
            difficulty=difficulty,
            system_ids=[n["id"] for n in sys_nodes],
            discipline_ids=[n["id"] for n in disc_nodes],
        )
        block = rules["block"]
        return PoolResponse(
            exam=chosen,
            exams=exams,
            limits=BlockLimits(**block),
            modes=[
                ModeChoice(id="tutor", available=True),
                ModeChoice(id="timed", available=timed_ok),
                ModeChoice(id="exam", available=timed_ok),
            ],
            total=f.total,
            systems=[PoolFacet(id=n["id"], label=n["label"], count=f.systems[n["id"]]) for n in sys_nodes],
            disciplines=[PoolFacet(id=n["id"], label=n["label"], count=f.disciplines[n["id"]]) for n in disc_nodes],
            status=f.status,
            difficulty=f.difficulty,
        )

    return await as_user(_db(request), user, run)


def _match_taxonomy(value: str) -> bool:
    return re.match(TAXONOMY_PATTERN, value) is not None


# ── Blocks ──


def _summary(row: Row, answered: int, correct: int) -> BlockSummary:
    cfg = BlockConfig.model_validate(row["config"])
    reveal = cfg.mode == "tutor" or row["finished_at"] is not None
    return BlockSummary(
        id=row["id"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        config=cfg,
        total=len(row["question_ids"]),
        answered=answered,
        correct=correct if reveal else None,
        score=float(row["score"]) if row["score"] is not None else None,
    )


_BLOCK_ROW = "select id, created_at, started_at, finished_at, config, question_ids, score from public.blocks"
_BLOCK_ATTEMPTS = """
select question_id, chosen, correct, flagged from public.attempts
where block_id = %s and user_id = (select auth.uid())
"""


async def _detail(conn: psycopg.AsyncConnection[Any], row: Row) -> BlockDetail:
    attempts = {a["question_id"]: a for a in await _rows(conn, _BLOCK_ATTEMPTS, (row["id"],))}
    cfg = BlockConfig.model_validate(row["config"])
    reveal = cfg.mode == "tutor" or row["finished_at"] is not None
    states: list[BlockQuestionState] = []
    answered = correct = 0
    next_n = 0
    for i, qid in enumerate(row["question_ids"], start=1):
        a = attempts.get(qid)
        done = a is not None and a["chosen"] is not None and a["correct"] is not None
        answered += 1 if done else 0
        correct += 1 if a is not None and done and a["correct"] else 0
        if not done and not next_n:
            next_n = i
        states.append(
            BlockQuestionState(
                n=i,
                answered=done,
                flagged=bool(a and a["flagged"]),
                correct=bool(a["correct"]) if done and reveal and a is not None else None,
            )
        )
    summary = _summary(row, answered, correct)
    return BlockDetail(**summary.model_dump(), questions=states, next_n=next_n or 1)


async def _insert_block(
    conn: psycopg.AsyncConnection[Any], user: CurrentUser, cfg: BlockConfig, question_ids: list[str]
) -> BlockDetail:
    rows = await _rows(
        conn,
        "insert into public.blocks (id, user_id, config, question_ids, started_at) "
        "values (%s, (select auth.uid()), %s, %s, now()) "
        "returning id, created_at, started_at, finished_at, config, question_ids, score",
        (uuid.uuid4(), Jsonb(cfg.model_dump(mode="json")), question_ids),
    )
    return await _detail(conn, rows[0])


def _check_count(rules: dict[str, Any], count: int) -> None:
    lo, hi = rules["block"]["min_questions"], rules["block"]["max_questions"]
    if not lo <= count <= hi:
        raise HTTPException(422, f"A block has {lo} to {hi} questions.")


async def _check_mode(request: Request, user: CurrentUser, mode: str) -> None:
    if mode != "tutor" and not await _timed_available(request, user):
        raise HTTPException(422, "Timed and exam blocks are not available yet. Use tutor mode.")


@router.post(
    "/blocks",
    operation_id="createBlock",
    summary="Build a block from the filters: server-chosen, no duplicates, deterministic for the seed",
    response_model=BlockDetail,
    status_code=201,
    responses={**_ERRORS, **_INVALID},
)
async def create_block(
    request: Request, body: BlockCreate, user: Annotated[CurrentUser, limited("qbank_write")]
) -> BlockDetail:
    rules = await _rules(request)
    _check_exam(rules, body.exam)
    _check_count(rules, body.count)
    await _check_mode(request, user, body.mode)
    seed = body.seed if body.seed is not None else secrets.randbits(48)

    async def run(conn: psycopg.AsyncConnection[Any]) -> BlockDetail:
        questions, history = await _load_pool(conn, body.exam)
        pool = filter_pool(
            questions,
            history,
            systems=body.systems,
            disciplines=body.disciplines,
            status=body.status,
            difficulty=body.difficulty,
        )
        if len(pool) < body.count:
            noun = "question matches" if len(pool) == 1 else "questions match"
            raise HTTPException(
                422,
                f"Only {len(pool)} {noun} these filters. Choose {len(pool)} or fewer, or widen the filters.",
            )
        ids = choose((q.id for q in pool), body.count, seed)
        cfg = BlockConfig(
            exam=body.exam,
            mode=body.mode,
            status=body.status,
            difficulty=body.difficulty,
            systems=body.systems,
            disciplines=body.disciplines,
            seed=seed,
            weak_spot=False,
        )
        return await _insert_block(conn, user, cfg, ids)

    return await as_user(_db(request), user, run)


@router.post(
    "/blocks/single",
    operation_id="createSingleQuestionBlock",
    summary="A one-question tutor block for a question found elsewhere (search); answers stay hidden until answered",
    response_model=BlockDetail,
    status_code=201,
    responses={**_ERRORS, **_NOT_FOUND},
)
async def create_single_block(
    request: Request, body: SingleBlockCreate, user: Annotated[CurrentUser, limited("qbank_write")]
) -> BlockDetail:
    async def run(conn: psycopg.AsyncConnection[Any]) -> BlockDetail:
        # Read under RLS: a question the caller may not see (draft, hidden chapter) is simply not found.
        found = await _first(conn, "select id, exam from public.questions where id = %s", (body.question_id,))
        if found is None:
            raise HTTPException(404, "This question does not exist or is not available to you.")
        cfg = BlockConfig(
            exam=found["exam"],
            mode="tutor",
            status="all",
            difficulty=None,
            systems=[],
            disciplines=[],
            seed=0,
            weak_spot=False,
        )
        return await _insert_block(conn, user, cfg, [found["id"]])

    return await as_user(_db(request), user, run)


_GRADED = """
select a.question_id, a.correct, a.created_at, q.taxonomy_ids
from public.attempts a join public.questions q on q.id = a.question_id
where a.user_id = (select auth.uid()) and a.correct is not null and q.exam = %s
"""


@router.post(
    "/blocks/weak-spot",
    operation_id="createWeakSpotBlock",
    summary="Build a block from the caller's weakest topics (accuracy weighted by recency, with a minimum of attempts)",
    response_model=WeakSpotBlock,
    status_code=201,
    responses={**_ERRORS, **_INVALID},
)
async def create_weak_spot_block(
    request: Request, body: WeakSpotCreate, user: Annotated[CurrentUser, limited("qbank_write")]
) -> WeakSpotBlock:
    rules = await _rules(request)
    _check_exam(rules, body.exam)
    await _check_mode(request, user, body.mode)
    seed = body.seed if body.seed is not None else secrets.randbits(48)
    lo, hi = rules["block"]["min_questions"], rules["block"]["max_questions"]
    wanted = min(body.count or WEAK_SPOT_DEFAULT, hi)
    _check_count(rules, wanted)

    async def run(conn: psycopg.AsyncConnection[Any]) -> WeakSpotBlock:
        graded_rows = await _rows(conn, _GRADED, (body.exam,))
        nodes = {n["id"]: n for n in await _load_taxonomy(conn, body.exam)}
        taxonomy = {r["question_id"]: tuple(r["taxonomy_ids"]) for r in graded_rows}
        graded = [Graded(r["question_id"], bool(r["correct"]), r["created_at"]) for r in graded_rows]
        cells = weak_cells(graded, taxonomy, {k: v["kind"] for k, v in nodes.items()}, now=dt.datetime.now(dt.UTC))
        if not cells:
            raise HTTPException(
                422,
                "Naadi needs a little more practice history to find your weak spots. "
                "Answer a few more questions, then try again.",
            )
        questions, history = await _load_pool(conn, body.exam)
        ordered = weak_spot_order(questions, history, [c.id for c in cells], seed)
        if len(ordered) < lo:
            raise HTTPException(422, "There are not enough questions on your weak topics yet.")
        ids = ordered[: min(wanted, len(ordered))]
        cfg = BlockConfig(
            exam=body.exam,
            mode=body.mode,
            status="all",
            difficulty=None,
            systems=sorted(c.id for c in cells if nodes[c.id]["kind"] == "system"),
            disciplines=sorted(c.id for c in cells if nodes[c.id]["kind"] != "system"),
            seed=seed,
            weak_spot=True,
        )
        block = await _insert_block(conn, user, cfg, ids)
        return WeakSpotBlock(
            block=block,
            cells=[
                WeakCellOut(id=c.id, label=nodes[c.id]["label"], attempts=c.attempts, correct=c.correct) for c in cells
            ],
        )

    return await as_user(_db(request), user, run)


@router.get(
    "/blocks",
    operation_id="listBlocks",
    summary="The caller's past blocks, newest first",
    response_model=BlockList,
    responses={**_ERRORS},
)
async def list_blocks(
    request: Request,
    user: Annotated[CurrentUser, limited("qbank_read")],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> BlockList:
    async def run(conn: psycopg.AsyncConnection[Any]) -> BlockList:
        rows = await _rows(
            conn,
            """
            select b.id, b.created_at, b.started_at, b.finished_at, b.config, b.question_ids, b.score,
              coalesce(a.answered, 0) as answered, coalesce(a.correct, 0) as correct
            from public.blocks b
            left join lateral (
              select count(*) filter (where chosen is not null and correct is not null) as answered,
                     count(*) filter (where correct) as correct
              from public.attempts t where t.block_id = b.id and t.user_id = b.user_id
            ) a on true
            where b.user_id = (select auth.uid())
            order by b.created_at desc, b.id limit %s
            """,
            (limit,),
        )
        return BlockList(blocks=[_summary(r, int(r["answered"]), int(r["correct"])) for r in rows])

    return await as_user(_db(request), user, run)


BlockId = Annotated[uuid.UUID, Path(description="Block id")]
NOT_FOUND = "This block does not exist."


async def _block_row(conn: psycopg.AsyncConnection[Any], block_id: uuid.UUID) -> Row:
    rows = await _rows(conn, f"{_BLOCK_ROW} where id = %s and user_id = (select auth.uid())", (block_id,))
    if not rows:
        raise HTTPException(404, NOT_FOUND)
    return rows[0]


@router.get(
    "/blocks/{block_id}",
    operation_id="getBlock",
    summary="One block with the state of each question (answered, flagged; correct only in tutor mode)",
    response_model=BlockDetail,
    responses={**_ERRORS, **_NOT_FOUND},
)
async def get_block(
    request: Request, block_id: BlockId, user: Annotated[CurrentUser, limited("qbank_read")]
) -> BlockDetail:
    async def run(conn: psycopg.AsyncConnection[Any]) -> BlockDetail:
        return await _detail(conn, await _block_row(conn, block_id))

    return await as_user(_db(request), user, run)


# ── Questions ──

_QUESTION = """
select id, chapter_id, stem_html, options, difficulty, taxonomy_ids
from public.questions where id = %s
"""
_REVEAL = """
select answer, answer_html, explanation_html, why_not_html, educational_objective
from public.questions where id = %s and status = 'published'
"""
_OWN_ATTEMPT = """
select id, chosen, correct, seconds, flagged from public.attempts
where block_id = %s and question_id = %s and user_id = (select auth.uid())
"""


def _explanation(secret: Row) -> Explanation:
    return Explanation(
        answer=secret["answer"],
        answer_html=secret["answer_html"],
        explanation_html=secret["explanation_html"],
        why_not_html=secret["why_not_html"],
        educational_objective=secret["educational_objective"],
    )


def _position(row: Row, n: int) -> str:
    ids: list[str] = row["question_ids"]
    if not 1 <= n <= len(ids):
        raise HTTPException(404, "This block has no such question.")
    return ids[n - 1]


@router.get(
    "/blocks/{block_id}/questions/{n}",
    operation_id="getBlockQuestion",
    summary="Question n of a block: no answer or explanation unless the caller has a graded attempt (tutor mode)",
    response_model=QuestionView,
    responses={**_ERRORS, **_NOT_FOUND},
)
async def get_block_question(
    request: Request,
    block_id: BlockId,
    n: Annotated[int, Path(ge=1, le=200, description="1-based position")],
    user: Annotated[CurrentUser, limited("qbank_read")],
) -> QuestionView:
    db = _db(request)

    async def read(conn: psycopg.AsyncConnection[Any]) -> tuple[Row, str, Row, Row | None, Row | None, list[str]]:
        block = await _block_row(conn, block_id)
        qid = _position(block, n)
        questions = await _rows(conn, _QUESTION, (qid,))
        if not questions:
            raise HTTPException(404, "This question is no longer available.")
        q = questions[0]
        attempt = await _first(conn, _OWN_ATTEMPT, (block_id, qid))
        lesson = None
        if q["chapter_id"]:
            lesson = await _first(conn, "select id, title from public.chapters where id = %s", (q["chapter_id"],))
        labels = [
            r["label"]
            for r in await _rows(
                conn,
                "select label from public.taxonomy_nodes where id = any(%s)"
                " and kind in ('system','discipline','clinical_science')"
                " order by kind desc, sort",
                (list(q["taxonomy_ids"]),),
            )
        ]
        return block, qid, q, attempt, lesson, labels

    block, qid, q, attempt, lesson, labels = await as_user(db, user, read)
    cfg = BlockConfig.model_validate(block["config"])
    graded = attempt is not None and attempt["chosen"] is not None and attempt["correct"] is not None
    reveal = graded and cfg.mode == "tutor"
    explanation: Explanation | None = None
    if reveal:

        async def secret(conn: psycopg.AsyncConnection[Any]) -> Row | None:
            return await _first(conn, _REVEAL, (qid,))

        s = await as_service(db, secret)
        explanation = _explanation(s) if s else None
    return QuestionView(
        block_id=block_id,
        n=n,
        total=len(block["question_ids"]),
        mode=cfg.mode,
        question=QuestionBody(
            id=q["id"],
            stem_html=q["stem_html"],
            options=[QuestionOption(label=o["label"], html=o["html"]) for o in q["options"]],
            difficulty=q["difficulty"],
            topics=labels,
        ),
        lesson=LessonLink(chapter_id=lesson["id"], title=lesson["title"]) if lesson else None,
        flagged=bool(attempt and attempt["flagged"]),
        attempt=(
            AttemptState(
                chosen=attempt["chosen"],
                seconds=attempt["seconds"],
                correct=bool(attempt["correct"]) if reveal else None,
            )
            if graded and attempt is not None
            else None
        ),
        explanation=explanation,
    )


# ── Attempts ──

_LOCK_BLOCK = (
    "select id, config, question_ids, finished_at from public.blocks where id = %s and user_id = %s for update"
)
_EXISTING = """
select id, chosen, correct, seconds, flagged from public.attempts
where block_id = %s and question_id = %s and user_id = %s
"""
_PROGRESS = """
select count(*) filter (where chosen is not null and correct is not null) as answered,
       count(*) filter (where correct) as correct
from public.attempts where block_id = %s and user_id = %s
"""


@router.post(
    "/blocks/{block_id}/attempts",
    operation_id="createAttempt",
    summary="Answer a question: the server grades it; idempotent per (block, question); explanation in tutor mode only",
    response_model=AttemptOut,
    responses={**_ERRORS, **_NOT_FOUND, **_INVALID},
)
async def create_attempt(
    request: Request, block_id: BlockId, body: AttemptIn, user: Annotated[CurrentUser, limited("qbank_write")]
) -> AttemptOut:
    uid = user.user_id

    async def run(conn: psycopg.AsyncConnection[Any]) -> tuple[AttemptOut, bool]:
        blocks = await _rows(conn, _LOCK_BLOCK, (block_id, uid))
        if not blocks:
            raise HTTPException(404, NOT_FOUND)
        block = blocks[0]
        ids: list[str] = block["question_ids"]
        if body.question_id not in ids:
            raise HTTPException(404, "This question is not in this block.")
        cfg = BlockConfig.model_validate(block["config"])
        questions = await _rows(
            conn,
            "select options, answer from public.questions where id = %s and status = 'published'",
            (body.question_id,),
        )
        if not questions:
            raise HTTPException(404, "This question is no longer available.")
        q = questions[0]
        if body.chosen not in {o["label"] for o in q["options"]}:
            raise HTTPException(422, "Choose one of the listed options.")
        existing = await _first(conn, _EXISTING, (block_id, body.question_id, uid))
        if existing is not None and existing["correct"] is not None and existing["chosen"] is not None:
            attempt = existing  # idempotent: the first graded answer stands
        else:
            # A flag-only row (or an ungraded row a client inserted itself) keeps its choice, if it has one.
            chosen = (existing["chosen"] if existing and existing["chosen"] else None) or body.chosen
            correct = chosen == q["answer"]
            if existing is None:
                attempt = (
                    await _rows(
                        conn,
                        "insert into public.attempts"
                        " (user_id, question_id, block_id, chosen, correct, seconds, flagged, mode) "
                        "values (%s, %s, %s, %s, %s, %s, %s, %s) returning id, chosen, correct, seconds, flagged",
                        (uid, body.question_id, block_id, chosen, correct, body.seconds, body.flagged, cfg.mode),
                    )
                )[0]
            else:
                attempt = (
                    await _rows(
                        conn,
                        "update public.attempts set chosen = %s, correct = %s, seconds = %s,"
                        " flagged = flagged or %s, mode = %s "
                        "where id = %s returning id, chosen, correct, seconds, flagged",
                        (chosen, correct, body.seconds, body.flagged, cfg.mode, existing["id"]),
                    )
                )[0]
        progress = (await _rows(conn, _PROGRESS, (block_id, uid)))[0]
        answered, right, total = int(progress["answered"]), int(progress["correct"]), len(ids)
        finished = block["finished_at"] is not None
        if answered >= total and not finished and cfg.mode == "tutor":
            # Tutor blocks end with their last answer. Timed and exam blocks are finished by the results flow (S09).
            await conn.execute(
                "update public.blocks set finished_at = now(), score = %s where id = %s and user_id = %s",
                (round(100.0 * right / total, 1), block_id, uid),
            )
            finished = True
        reveal = cfg.mode == "tutor"
        secret = await _first(conn, _REVEAL, (body.question_id,)) if reveal else None
        return (
            AttemptOut(
                question_id=body.question_id,
                n=ids.index(body.question_id) + 1,
                chosen=attempt["chosen"],
                correct=bool(attempt["correct"]) if reveal else None,
                flagged=bool(attempt["flagged"]),
                seconds=attempt["seconds"],
                explanation=_explanation(secret) if secret else None,
                answered=answered,
                total=total,
                finished=finished,
            ),
            existing is not None and existing["correct"] is not None,
        )

    out, replay = await as_service(_db(request), run)
    log.info("qbank_attempt", replay=replay, mode=out.explanation is not None)
    return out


@router.patch(
    "/blocks/{block_id}/attempts/{question_id}",
    operation_id="flagAttempt",
    summary="Flag or unflag a question of the block (the only field a client may change after answering)",
    response_model=FlagOut,
    responses={**_ERRORS, **_NOT_FOUND},
)
async def flag_attempt(
    request: Request,
    block_id: BlockId,
    question_id: Annotated[str, Path(pattern=ID_PATTERN)],
    body: FlagIn,
    user: Annotated[CurrentUser, limited("qbank_write")],
) -> FlagOut:
    uid = user.user_id

    async def run(conn: psycopg.AsyncConnection[Any]) -> FlagOut:
        blocks = await _rows(conn, _LOCK_BLOCK, (block_id, uid))
        if not blocks:
            raise HTTPException(404, NOT_FOUND)
        cfg = BlockConfig.model_validate(blocks[0]["config"])
        ids: list[str] = blocks[0]["question_ids"]
        if question_id not in ids:
            raise HTTPException(404, "This question is not in this block.")
        updated = await _rows(
            conn,
            "update public.attempts set flagged = %s"
            " where block_id = %s and question_id = %s and user_id = %s returning id",
            (body.flagged, block_id, question_id, uid),
        )
        if not updated:
            # Flagging before answering keeps a row with no choice; it is never treated as an answer.
            await conn.execute(
                "insert into public.attempts (user_id, question_id, block_id, flagged, mode)"
                " values (%s, %s, %s, %s, %s)",
                (uid, question_id, block_id, body.flagged, cfg.mode),
            )
        return FlagOut(question_id=question_id, n=ids.index(question_id) + 1, flagged=body.flagged)

    return await as_service(_db(request), run)


# ── Lab values ──


async def _source(conn: psycopg.AsyncConnection[Any], source_id: str) -> SourceRef:
    rows = await _rows(conn, "select id, name, license, url from public.sources where id = %s", (source_id,))
    if not rows:
        raise HTTPException(503, "The lab values table is not available right now.")
    return SourceRef(**rows[0])


@router.get(
    "/lab-values",
    operation_id="getLabValues",
    summary="Reference ranges for the question view (Naadi's own table; the official one is linked, not copied)",
    response_model=LabValuesOut,
    responses={**_ERRORS},
)
async def get_lab_values(request: Request, user: Annotated[CurrentUser, limited("qbank_read")]) -> LabValuesOut:
    cfg: dict[str, Any] = await _config(request).get("lab_values")

    async def run(conn: psycopg.AsyncConnection[Any]) -> LabValuesOut:
        return LabValuesOut(
            groups=[
                LabGroupOut(id=g["id"], label=g["label"], rows=[LabRowOut(**r) for r in g["rows"]])
                for g in cfg["groups"]
            ],
            source=await _source(conn, cfg["source_id"]),
            official=await _source(conn, cfg["official_source_id"]),
        )

    return await as_user(_db(request), user, run)


def install_qbank_routes(app: Any) -> None:
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
    app.include_router(router)


__all__ = ["install_qbank_routes", "router"]
