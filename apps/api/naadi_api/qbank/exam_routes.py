"""Timed/exam endpoints: mock exams, the session clock and its transitions, finishing, Results (segment 09).

The block API itself (create, detail, questions, attempts, flags) lives in `routes.py` (segment 08) and calls
into `exam.py` for timed/exam blocks. Every timing write happens here with the server's clock
(`app.state.now`, injectable for tests); the client only ever reads it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Path, Request
from starlette.exceptions import HTTPException

from ..analytics.queries import score_trend
from ..auth import CurrentUser
from ..errors import CodedHTTPException, ErrorResponse
from . import clock, exam, results
from .exam_models import ActiveExam, ActiveExamOut, BlockClock, BlockResults, MockBlockCreate, TrendPoint
from .exam_state import active_exam
from .models import BlockConfig, BlockDetail
from .routes import (
    _ERRORS,
    _NOT_FOUND,
    NOT_FOUND,
    BlockId,
    _detail_of,
    _rows,
    _rules,
    _timed_available,
    as_service,
    as_user,
    limited,
)
from .routes import _db as db_of

router = APIRouter(prefix="/api", tags=["qbank"])

_CONFLICT: dict[int | str, dict[str, Any]] = {
    409: {"model": ErrorResponse, "description": "Not possible in the session's current state (see error.code)"}
}


def now_of(request: Request) -> dt.datetime:
    fn = getattr(request.app.state, "now", None)
    return fn() if callable(fn) else exam.default_now()


async def _locked(conn: psycopg.AsyncConnection[Any], block_id: uuid.UUID, user: CurrentUser) -> dict[str, Any]:
    block = await exam.lock_block(conn, block_id, user.user_id)
    if block is None:
        raise HTTPException(404, NOT_FOUND)
    if not exam.is_timed(block["config"].get("mode", "tutor")):
        raise exam.conflict("not_timed")
    return block


async def _block_attempts(
    conn: psycopg.AsyncConnection[Any], block_id: uuid.UUID, user_id: uuid.UUID
) -> dict[str, Any]:
    rows = await _rows(
        conn,
        "select question_id, chosen, correct, flagged from public.attempts where block_id = %s and user_id = %s",
        (block_id, user_id),
    )
    return {r["question_id"]: r for r in rows}


# ── Mock exams ──


@router.post(
    "/blocks/mock",
    operation_id="createMockBlock",
    summary="Start a seeded mock exam: its questions in their fixed order, one exam-mode section per seeded block",
    response_model=BlockDetail,
    status_code=201,
    responses={**_ERRORS, **_NOT_FOUND, 422: {"model": ErrorResponse, "description": "Timed modes are off"}},
)
async def create_mock_block(
    request: Request, body: MockBlockCreate, user: Annotated[CurrentUser, limited("qbank_write")]
) -> BlockDetail:
    if not await _timed_available(request, user):
        raise exam.invalid("timed_unavailable")
    rules = await _rules(request)
    now = now_of(request)

    async def read(conn: psycopg.AsyncConnection[Any]) -> tuple[str, list[str], list[int]]:
        # Under RLS: a mock chapter or question the caller may not see is not found.
        chapters = await _rows(
            conn, "select id, exam from public.chapters where id = %s and kind = 'mock_exam'", (body.chapter_id,)
        )
        if not chapters:
            raise CodedHTTPException(404, "mock_not_found", "mock_not_found")
        qs = await _rows(
            conn,
            "select id, block from public.questions where chapter_id = %s and block is not null"
            " and owner_user_id is null order by block, id",
            (body.chapter_id,),
        )
        if not qs:
            raise CodedHTTPException(404, "mock_not_found", "mock_not_found")
        return chapters[0]["exam"], [q["id"] for q in qs], [int(q["block"]) for q in qs]

    exam_id, ids, blocks = await as_user(db_of(request), user, read)
    fmt = rules["exams"].get(exam_id)
    if fmt is None:
        raise CodedHTTPException(404, "mock_not_found", "mock_not_found")
    layout = exam.mock_layout(ids, blocks, int(fmt["minutes_per_block"]))
    cfg = BlockConfig(
        exam=exam_id,
        mode="exam",
        status="all",
        difficulty=None,
        systems=[],
        disciplines=[],
        seed=0,
        weak_spot=False,
        sections=layout,
        break_s=exam.break_s(rules),
        mock=body.chapter_id,
    )
    uid = user.user_id

    async def write(conn: psycopg.AsyncConnection[Any]) -> BlockDetail:
        if await active_exam(conn, uid, rules=rules, now=now) is not None:
            raise exam.conflict("exam_active")  # one timed/exam session at a time
        rows = await _rows(
            conn,
            "insert into public.blocks (id, user_id, config, question_ids, started_at) values (%s, %s, %s, %s, null)"
            " returning id, created_at, started_at, finished_at, config, question_ids, score, break_used_ms",
            (uuid.uuid4(), uid, psycopg.types.json.Jsonb(cfg.model_dump(mode="json")), ids),
        )
        block = rows[0]
        await exam.create_sections(conn, block["id"], uid, layout)
        session = await exam.sync(conn, block, uid, rules, now)
        return _detail_of(block, {}, session=session, now=now)

    return await as_service(db_of(request), write)


# ── Clock ──


@router.get(
    "/blocks/{block_id}/clock",
    operation_id="getBlockClock",
    summary="The server's clock for a timed/exam block (state, deadline, time left, break left)",
    response_model=BlockClock,
    responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT},
)
async def get_clock(
    request: Request, block_id: BlockId, user: Annotated[CurrentUser, limited("qbank_read")]
) -> BlockClock:
    rules = await _rules(request)
    now = now_of(request)

    async def run(conn: psycopg.AsyncConnection[Any]) -> BlockClock:
        block = await _locked(conn, block_id, user)
        return exam.clock_out(await exam.sync(conn, block, user.user_id, rules, now), now)

    return await as_service(db_of(request), run)


Action = Literal["start", "pause", "resume", "end"]


@router.post(
    "/blocks/{block_id}/sections/{n}/{action}",
    operation_id="changeBlockSection",
    summary="Start, pause (timed mode only), resume or end a section; answers 409 with a code when not possible",
    response_model=BlockDetail,
    responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT},
)
async def change_section(
    request: Request,
    block_id: BlockId,
    n: Annotated[int, Path(ge=1, le=12)],
    action: Action,
    user: Annotated[CurrentUser, limited("qbank_write")],
) -> BlockDetail:
    rules = await _rules(request)
    now = now_of(request)
    step = {"start": clock.start, "pause": clock.pause, "resume": clock.resume, "end": clock.end}[action]

    async def run(conn: psycopg.AsyncConnection[Any]) -> BlockDetail:
        if action == "start":
            # One timed/exam session at a time: another one running, paused or on a break blocks this start.
            other = await active_exam(conn, user.user_id, rules=rules, now=now)
            if other is not None and other[0] != block_id:
                raise exam.conflict("exam_active")
        block = await _locked(conn, block_id, user)
        session = await exam.apply(conn, block, user.user_id, rules, now, lambda s: step(s, n, now))
        if action == "start" and block["started_at"] is None:
            await conn.execute(
                "update public.blocks set started_at = %s where id = %s and user_id = %s",
                (now, block_id, user.user_id),
            )
            block["started_at"] = now
        return _detail_of(block, await _block_attempts(conn, block_id, user.user_id), session=session, now=now)

    return await as_service(db_of(request), run)


@router.post(
    "/blocks/{block_id}/finish",
    operation_id="finishBlock",
    summary="End the whole timed/exam session now and grade it (idempotent); answers unlock for review",
    response_model=BlockDetail,
    responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT},
)
async def finish_block(
    request: Request, block_id: BlockId, user: Annotated[CurrentUser, limited("qbank_write")]
) -> BlockDetail:
    rules = await _rules(request)
    now = now_of(request)

    async def run(conn: psycopg.AsyncConnection[Any]) -> BlockDetail:
        block = await _locked(conn, block_id, user)
        session = await exam.apply(conn, block, user.user_id, rules, now, lambda s: clock.finish(s, now))
        return _detail_of(block, await _block_attempts(conn, block_id, user.user_id), session=session, now=now)

    return await as_service(db_of(request), run)


# ── Active exam (shell, assistant guard) ──


@router.get(
    "/exam/active",
    operation_id="getActiveExam",
    summary="The caller's timed/exam session that is running, paused or on a break, if any",
    response_model=ActiveExamOut,
    responses={**_ERRORS},
)
async def get_active_exam(request: Request, user: Annotated[CurrentUser, limited("qbank_read")]) -> ActiveExamOut:
    rules = await _rules(request)
    now = now_of(request)

    async def run(conn: psycopg.AsyncConnection[Any]) -> ActiveExamOut:
        found = await active_exam(conn, user.user_id, rules=rules, now=now)
        if found is None:
            return ActiveExamOut(active=None)
        block_id, mode, session = found
        return ActiveExamOut(active=ActiveExam(block_id=block_id, mode=mode, clock=exam.clock_out(session, now)))

    return await as_service(db_of(request), run)


# ── Results ──


@router.get(
    "/blocks/{block_id}/results",
    operation_id="getBlockResults",
    summary="Results of a finished timed/exam block: score, time, changes, flags, topics, weights, trend",
    response_model=BlockResults,
    responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT},
)
async def get_results(
    request: Request, block_id: BlockId, user: Annotated[CurrentUser, limited("qbank_read")]
) -> BlockResults:
    rules = await _rules(request)
    now = now_of(request)
    uid = user.user_id

    async def run(conn: psycopg.AsyncConnection[Any]) -> BlockResults:
        block = await _locked(conn, block_id, user)
        session = await exam.sync(conn, block, uid, rules, now)
        if block["finished_at"] is None:
            raise exam.conflict("not_finished")
        cfg = BlockConfig.model_validate(block["config"])
        ids: list[str] = list(block["question_ids"])
        qrows = {
            r["id"]: r
            for r in await _rows(
                conn,
                "select id, answer, taxonomy_ids from public.questions where id = any(%s)"
                " and (owner_user_id is null or owner_user_id = %s)",
                (ids, uid),
            )
        }
        arows = {
            r["question_id"]: r
            for r in await _rows(
                conn,
                "select question_id, chosen, first_chosen, seconds, flagged from public.attempts"
                " where block_id = %s and user_id = %s",
                (block_id, uid),
            )
        }
        graded: list[results.GradedQuestion] = []
        for i, qid in enumerate(ids):
            q = qrows.get(qid)
            if q is None:
                continue  # retired since: not scored here either
            a = arows.get(qid, {})
            sec = session.section_of(i)
            graded.append(
                results.GradedQuestion(
                    n=i + 1,
                    section_n=sec.n if sec else 1,
                    question_id=qid,
                    answer=q["answer"],
                    chosen=a.get("chosen"),
                    first_chosen=a.get("first_chosen"),
                    seconds=a.get("seconds"),
                    flagged=bool(a.get("flagged")),
                    taxonomy_ids=tuple(q["taxonomy_ids"]),
                )
            )
        node_rows = await _rows(
            conn,
            "select id, kind, label, sort, weight_min::float as weight_min, weight_max::float as weight_max"
            " from public.taxonomy_nodes where %s = any(exam)",
            (cfg.exam,),
        )
        nodes = {
            r["id"]: results.Node(r["id"], r["kind"], r["label"], int(r["sort"]), r["weight_min"], r["weight_max"])
            for r in node_rows
        }
        trend_rows = await score_trend(conn, uid, until=block["finished_at"])
        trend = [
            TrendPoint(
                block_id=t.block_id,
                finished_at=t.finished_at,
                score=t.score,
                mode=t.mode,
                current=t.block_id == block_id,
            )
            for t in trend_rows
            if t.mode in ("timed", "exam")
        ]
        target = rules["modes"][cfg.mode]["seconds_per_question"]
        total = len(graded)
        right = sum(1 for g in graded if results.outcome(g) == "correct")
        omitted = sum(1 for g in graded if results.outcome(g) == "omitted")
        percent = results.pct(right, total)
        time = results.time_stats(graded, target)
        changes = results.change_stats(graded)
        flags = results.flag_stats(graded)
        systems = results.topic_stats(graded, nodes, "system")
        second: results.TopicKind = (
            "clinical_science" if any(n.kind == "clinical_science" for n in nodes.values()) else "discipline"
        )
        disciplines = results.topic_stats(graded, nodes, second)
        competencies = results.topic_stats(graded, nodes, "competency")
        weights = results.weight_overlay(graded, nodes)
        return BlockResults(
            block_id=block_id,
            exam=cfg.exam,
            mode="exam" if cfg.mode == "exam" else "timed",
            mock=cfg.mock,
            finished_at=block["finished_at"],
            total=total,
            correct=right,
            omitted=omitted,
            percent=percent,
            time=time,
            changes=changes,
            flags=flags,
            questions=results.questions_out(graded),
            systems=systems,
            disciplines=disciplines,
            competencies=competencies,
            weights=weights,
            trend=trend,
            insights=results.insights(
                percent=percent,
                time=time,
                changes=changes,
                flags=flags,
                systems=systems,
                disciplines=disciplines,
                weights=weights,
                trend=trend,
            ),
        )

    return await as_service(db_of(request), run)


__all__ = ["now_of", "router"]
