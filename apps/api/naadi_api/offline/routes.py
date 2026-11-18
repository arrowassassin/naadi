"""`GET /api/offline/week?start=YYYY-MM-DD`: the manifest "Download this week" stores on the device (segment 18).

What the week is, from the data:
- dates: `start` and the 6 days after it (default: today in the profile's time zone);
- plan: the active plan's `study_plan_days` on those dates and every `plan_items` row on them;
- chapters: the chapters those days read (`plan_items` with `ref_type 'chapter'`, and the days' `chapter_ids`),
  through the reader's own loader (`courses.load_chapter`, RLS as the student);
- questions: plan items point at an exam's Qbank builder, not at blocks, so "planned blocks" are the blocks the
  student has open: their unfinished tutor blocks. Each question goes through Qbank's own serializer
  (`qbank.routes.question_view` + `tutor_reveal`): stem and options always, the explanation only after a graded
  tutor answer, and no explanation at all while a timed/exam session is active (S09: a tutor block would reveal
  that session's answers). Timed/exam blocks are never in the manifest: their questions are served one running
  section at a time by S09;
- cards: due up to the end of the week, with the flashcard attempt gate (`cards.routes.load_due`);
- notes on the week's sections and questions, every notebook with its pages, the student's ink on those
  surfaces, and pathway task status.

Reads run as the student (RLS) except the ones the online endpoints also run privileged (attempt grading state,
due cards, explanations), each filtered on the caller. The response is `private, no-store`: the device keeps it in
IndexedDB only, never in the HTTP cache. Nothing here logs content; log lines carry counts.
"""

from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException

from ..auth import CurrentUser, get_account_user
from ..auth_limits import RateLimiter
from ..cards.models import DueResponse
from ..cards.routes import _params as card_params
from ..cards.routes import load_due
from ..chapters_media import configured_buckets
from ..courses import ChapterDetail, ChapterPositions, chapter_positions, load_chapter
from ..db import Database
from ..errors import CodedHTTPException, ErrorResponse
from ..ink.models import InkLayerOut
from ..ink.store import Surface, SurfaceNotFound, read_layer, resolve_surface
from ..logs import get_logger
from ..notes import store as notes_store
from ..notes.models import AnchorCounts, ColorCounts, NotebookDetail, NoteCounts, NoteList, NoteOut
from ..notes.routes import within_budget
from ..plan.models import PlanDayMeta, PlanItemOut
from ..plan.routes import _zone, local_today
from ..plan.store import DAY_COLS, ITEM_COLS, item_row_out
from ..qbank.exam_state import active_exam
from ..qbank.models import BlockConfig, BlockDetail
from ..qbank.routes import (
    _QUESTION,
    _REVEAL,
    _explanation,
    _priv_rows,
    _rules,
    as_service,
    as_user,
    question_view,
    tutor_reveal,
)
from ..qbank.routes import (
    _detail_of as qbank_detail_of,
)
from .models import (
    OfflineDay,
    OfflineFigure,
    OfflineInkLayer,
    OfflinePlan,
    OfflineQuestion,
    OfflineVideo,
    OfflineWeek,
    PartName,
    PartSize,
    PathwayTaskStatus,
    SectionRevision,
    SkippedMedia,
)

log = get_logger("naadi.offline")
router = APIRouter(prefix="/api", tags=["offline"])

Row = dict[str, Any]
DAYS = 7
MAX_CHAPTERS = 21
MAX_BLOCKS = 10
MAX_QUESTIONS = 400
MAX_CARDS = 200
MAX_NOTES = 2000
MAX_INK_LAYERS = 300
MAX_VIDEO_HEIGHT = 1080
START_RANGE = dt.timedelta(days=366)

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    422: {"model": ErrorResponse, "description": "start is not a date within a year of today"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Database unavailable"},
}


def default_limiters() -> dict[str, RateLimiter]:
    # A download is one manifest; a few retries or a second device are fine, a loop is not.
    return {"offline_read": RateLimiter(30, 60.0)}


def limited(bucket: str) -> Any:
    async def dep(request: Request, user: Annotated[CurrentUser, Depends(get_account_user)]) -> CurrentUser:
        limiters: dict[str, RateLimiter] = request.app.state.rate_limiters
        limiters[bucket].hit(str(user.user_id))
        return user

    return Depends(dep)


def _db(request: Request) -> Database:
    d: Database = request.app.state.db
    return d


def json_bytes(value: BaseModel | list[Any]) -> int:
    """Length of a model's (or a list of models') JSON as the API sends it: UTF-8, compact."""
    data: Any = (
        value.model_dump(mode="json", by_alias=True)
        if isinstance(value, BaseModel)
        else [v.model_dump(mode="json", by_alias=True) if isinstance(v, BaseModel) else v for v in value]
    )
    return len(json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode())


def part_size(name: PartName, count: int, *values: BaseModel | list[Any]) -> PartSize:
    """One row of `parts`: the bytes of the fields that make up the part, and how many items it holds."""
    return PartSize(part=name, bytes=sum(json_bytes(v) for v in values), count=count)


# ── Dates and plan ──


async def _week_start(db: Database, user: CurrentUser, start: dt.date | None) -> tuple[dt.date, str]:
    async def run(conn: psycopg.AsyncConnection[Any]) -> str | None:
        cur = await conn.execute("select timezone from public.profiles where user_id = (select auth.uid())")
        row = await cur.fetchone()
        return row["timezone"] if row else None

    tz = await as_user(db, user, run)
    zone = _zone(tz)
    today = local_today(tz)
    if start is not None and abs(start - today) > START_RANGE:
        raise CodedHTTPException(422, "offline_start_out_of_range", "offline_start_out_of_range")
    return start or today, zone.key


async def _plan(conn: psycopg.AsyncConnection[Any], start: dt.date, end: dt.date) -> OfflinePlan:
    cur = await conn.execute("select id from public.study_plans where user_id = (select auth.uid()) and active")
    plan = await cur.fetchone()
    days: dict[dt.date, Row] = {}
    if plan is not None:
        cur = await conn.execute(
            f"select {DAY_COLS} from public.study_plan_days where plan_id = %s and date between %s and %s",  # noqa: S608
            (plan["id"], start, end),
        )
        days = {r["date"]: dict(r) for r in await cur.fetchall()}
    cur = await conn.execute(
        f"select {ITEM_COLS} from public.plan_items where user_id = (select auth.uid())"  # noqa: S608 - constant
        " and day between %s and %s order by day, (plan_id is null), sort nulls last, id",
        (start, end),
    )
    items: dict[dt.date, list[PlanItemOut]] = defaultdict(list)
    for r in await cur.fetchall():
        items[r["day"]].append(PlanItemOut.model_validate(item_row_out(dict(r))))
    out = []
    for i in range(DAYS):
        d = start + dt.timedelta(days=i)
        meta = days.get(d)
        out.append(
            OfflineDay(
                date=d,
                plan_day=PlanDayMeta.model_validate(
                    {**meta, "chapter_ids": list(meta["chapter_ids"]), "merged_days": list(meta["merged_days"])}
                )
                if meta
                else None,
                items=items.get(d, []),
            )
        )
    return OfflinePlan(plan_id=plan["id"] if plan else None, days=out)


def planned_chapters(plan: OfflinePlan) -> list[str]:
    """Chapters the week reads, in plan order: reading items first, then the days' chapter lists."""
    seen: dict[str, None] = {}
    for day in plan.days:
        for it in day.items:
            if it.ref_type == "chapter" and it.ref_id:
                seen.setdefault(it.ref_id, None)
        for cid in day.plan_day.chapter_ids if day.plan_day else []:
            seen.setdefault(cid, None)
    return list(seen)[:MAX_CHAPTERS]


# ── Chapters and media ──


def offline_media(
    chapters: list[ChapterDetail], public: set[str]
) -> tuple[list[OfflineFigure], list[OfflineVideo], list[SkippedMedia]]:
    """Files to download: figures stored in content-public (`public`: their ids), and own videos at a known height
    of at most 1080 px. YouTube is never cached; a signed (private) URL would expire on the device."""
    figures: list[OfflineFigure] = []
    videos: list[OfflineVideo] = []
    skipped: list[SkippedMedia] = []
    seen: set[str] = set()
    for ch in chapters:
        for m in ch.media.values():
            if m.id in seen:
                continue
            seen.add(m.id)
            reason: str | None = None
            if m.kind == "video_youtube":
                reason = "youtube"
            elif m.kind not in ("svg", "image", "video_own"):
                reason = "not_offline"
            elif not m.url:
                reason = "no_url"
            elif m.kind == "video_own":
                if m.height is None:
                    reason = "size_unknown"
                elif m.height > MAX_VIDEO_HEIGHT:
                    reason = "over_1080p"
                else:
                    videos.append(OfflineVideo(media_id=m.id, url=m.url, height=m.height))
            elif m.id not in public:
                reason = "not_public"
            else:
                figures.append(OfflineFigure(media_id=m.id, url=m.url))
            if reason is not None:
                skipped.append(SkippedMedia.model_validate({"media_id": m.id, "reason": reason}))
    return figures, videos, skipped


@dataclass(frozen=True, slots=True)
class _Chapters:
    chapters: list[ChapterDetail]
    revisions: list[SectionRevision]
    positions: list[ChapterPositions]
    public_media: set[str]


async def _chapters(request: Request, user: CurrentUser, ids: list[str]) -> _Chapters:
    chapters: list[ChapterDetail] = []
    for cid in ids:
        try:
            chapters.append(await load_chapter(request, user, cid))
        except HTTPException as exc:
            if exc.status_code != 404:  # a chapter the student can no longer read is left out
                raise
    public_bucket = configured_buckets(request.app.state.settings).content_public

    async def run(conn: psycopg.AsyncConnection[Any]) -> tuple[list[Row], list[ChapterPositions], set[str]]:
        cids = [c.id for c in chapters]
        cur = await conn.execute(
            "select chapter_id, id as section_id, md5(coalesce(body::text, body_html, '')) as revision, updated_at"
            " from public.chapter_sections where chapter_id = any(%s) order by chapter_id, sort, id",
            (cids,),
        )
        revisions = [dict(r) for r in await cur.fetchall()]
        media_ids = sorted({m for c in chapters for m in c.media})
        cur = await conn.execute(
            "select id from public.media_assets where id = any(%s) and split_part(url, '/', 1) = %s",
            (media_ids, public_bucket),
        )
        public = {r["id"] for r in await cur.fetchall()}
        return revisions, await chapter_positions(conn, cids), public

    if not chapters:
        return _Chapters([], [], [], set())
    revisions, positions, public = await as_user(_db(request), user, run)
    return _Chapters(chapters, [SectionRevision.model_validate(r) for r in revisions], positions, public)


# ── Questions ──

_OPEN_BLOCKS = (
    "select id, created_at, started_at, finished_at, config, question_ids, score from public.blocks"
    " where user_id = (select auth.uid()) and finished_at is null order by created_at desc limit 50"
)
_ATTEMPTS = (
    "select block_id, question_id, chosen, correct, seconds, flagged from public.attempts"
    " where user_id = (select auth.uid()) and block_id = any(%s)"
)
_LABELS = (
    "select id, label from public.taxonomy_nodes where id = any(%s)"
    " and kind in ('system','discipline','clinical_science') order by kind desc, sort"
)


def _mode(block: Row) -> str | None:
    """The block's mode, or None for a config Qbank itself could not read (such a block is left out)."""
    try:
        return BlockConfig.model_validate(block["config"]).mode
    except ValidationError:
        return None


async def _questions(request: Request, user: CurrentUser) -> tuple[list[BlockDetail], list[OfflineQuestion], bool]:
    db = _db(request)
    rules = await _rules(request)
    now = dt.datetime.now(dt.UTC)

    async def exam_running(conn: psycopg.AsyncConnection[Any]) -> bool:
        # active_exam advances (and on expiry grades) the session as every timed request does; this GET writes
        # nothing, so that work is rolled back here and left to the student's next timed request.
        async with conn.transaction(force_rollback=True):
            return await active_exam(conn, user.user_id, rules=rules, now=now) is not None

    exam_on = await as_service(db, exam_running)

    async def read(
        conn: psycopg.AsyncConnection[Any],
    ) -> tuple[list[Row], dict[str, Row], list[Row], dict[str, Row], dict[str, Row]]:
        cur = await conn.execute(_OPEN_BLOCKS)
        blocks = [dict(r) for r in await cur.fetchall()]
        blocks = [b for b in blocks if _mode(b) == "tutor"][:MAX_BLOCKS]
        qids = list(dict.fromkeys(q for b in blocks for q in b["question_ids"]))[:MAX_QUESTIONS]
        questions: dict[str, Row] = {}
        for qid in qids:  # `_QUESTION` is Qbank's own column list (client-readable columns, under RLS)
            cur = await conn.execute(_QUESTION, (qid,))
            row = await cur.fetchone()
            if row is not None:
                questions[qid] = dict(row)
        attempts = await _priv_rows(conn, _ATTEMPTS, ([b["id"] for b in blocks],)) if blocks else []
        chapter_ids = sorted({q["chapter_id"] for q in questions.values() if q["chapter_id"]})
        lessons: dict[str, Row] = {}
        if chapter_ids:
            cur = await conn.execute("select id, title from public.chapters where id = any(%s)", (chapter_ids,))
            lessons = {r["id"]: dict(r) for r in await cur.fetchall()}
        tax = sorted({t for q in questions.values() for t in q["taxonomy_ids"]})
        labels: dict[str, Row] = {}
        if tax:
            cur = await conn.execute(_LABELS, (tax,))
            labels = {r["id"]: {**dict(r), "order": i} for i, r in enumerate(await cur.fetchall())}
        return blocks, questions, attempts, lessons, labels

    blocks, questions, attempt_rows, lessons, labels = await as_user(db, user, read)
    attempts = {(a["block_id"], a["question_id"]): a for a in attempt_rows}

    # Explanations: Qbank's own reveal rule, and never while an exam session runs.
    reveal_ids: set[str] = set()
    plan: list[tuple[Row, int, Row, Row | None, bool, bool]] = []
    for b in blocks:
        mode = _mode(b) or "tutor"
        for n, qid in enumerate(b["question_ids"], start=1):
            q = questions.get(qid)
            if q is None:
                continue
            a = attempts.get((b["id"], qid))
            graded, reveal = tutor_reveal(mode, a)
            reveal = reveal and not exam_on
            if reveal:
                reveal_ids.add(qid)
            plan.append((b, n, q, a, graded, reveal))

    async def secrets(conn: psycopg.AsyncConnection[Any]) -> dict[str, Row]:
        out: dict[str, Row] = {}
        for qid in sorted(reveal_ids):
            cur = await conn.execute(_REVEAL, (qid, user.user_id))
            row = await cur.fetchone()
            if row is not None:
                out[qid] = dict(row)
        return out

    revealed = await as_service(db, secrets) if reveal_ids else {}
    questions_out: list[OfflineQuestion] = []
    for b, n, q, a, graded, reveal in plan:
        topic = sorted((labels[t] for t in q["taxonomy_ids"] if t in labels), key=lambda r: r["order"])
        secret = revealed.get(q["id"]) if reveal else None
        view = question_view(
            b,
            n,
            q,
            a,
            lessons.get(q["chapter_id"]) if q["chapter_id"] else None,
            [t["label"] for t in topic],
            graded=graded,
            reveal=reveal and secret is not None,
            explanation=_explanation(secret) if secret else None,
        )
        questions_out.append(OfflineQuestion(block_id=b["id"], n=n, view=view))
    # GET /api/blocks/{id}'s own serializer (tutor blocks only: no exam session), so the device shows the block offline.
    out: list[BlockDetail] = []
    for b in blocks:
        mine = {qid: a for (bid, qid), a in attempts.items() if bid == b["id"]}
        out.append(qbank_detail_of(b, mine))
    return out, questions_out, exam_on


# ── Notes, notebooks, ink, pathway ──


async def _notes(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, sections: list[str], qids: list[str]) -> NoteList:
    cur = await conn.execute(
        f"select {notes_store.NOTE_COLS}, {notes_store.LIST_BODY} from public.notes n"  # noqa: S608 - constants
        " where n.user_id = %(uid)s and n.kind <> 'page' and ("
        " (n.anchor_type = 'section' and n.anchor_id = any(%(sections)s))"
        " or (n.anchor_type = 'question' and n.anchor_id = any(%(questions)s)))"
        " order by n.updated_at desc, n.id limit %(limit)s",
        {"uid": uid, "sections": sections, "questions": qids, "limit": MAX_NOTES},
    )
    rows = [dict(r) for r in await cur.fetchall()]
    sources = await notes_store.load_sources(conn, rows)
    c = await notes_store.note_counts(conn, uid)
    return NoteList(  # GET /api/notes's shape; counts are over all of the caller's notes, as there
        items=[NoteOut.model_validate(notes_store.shape_note(r, sources)) for r in within_budget(rows)],
        counts=NoteCounts(
            notes=c["notes"],
            highlights=c["highlights"],
            pages=c["pages"],
            by_color=ColorCounts(lime=c["lime"], violet=c["violet"], amber=c["amber"]),
            by_anchor=AnchorCounts(
                section=c["a_section"], question=c["a_question"], video=c["a_video"], pdf=c["a_pdf"], free=c["a_free"]
            ),
        ),
        next_offset=None,
    )


async def _notebooks(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID) -> list[NotebookDetail]:
    books = await notes_store.list_notebooks(conn, uid)
    cur = await conn.execute(
        f"select {notes_store.NOTE_COLS}, {notes_store.LIST_BODY} from public.notes n"  # noqa: S608 - constants
        " where n.user_id = %s and n.kind = 'page' and n.notebook_id is not null"
        " order by n.notebook_id, n.sort, n.id",
        (uid,),
    )
    rows = within_budget([dict(r) for r in await cur.fetchall()])
    sources = await notes_store.load_sources(conn, rows)
    pages: dict[Any, list[NoteOut]] = defaultdict(list)
    for r in rows:
        pages[r["notebook_id"]].append(NoteOut.model_validate(notes_store.shape_note(r, sources)))
    return [NotebookDetail.model_validate({**b, "pages": pages.get(b["id"], [])}) for b in books]


async def _ink(
    conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, surfaces: dict[str, list[str]]
) -> list[OfflineInkLayer]:
    cur = await conn.execute(
        "select surface_type, surface_id from public.ink_layers where user_id = (select auth.uid())"
        " and item_count > 0 and ((surface_type = 'section' and surface_id = any(%s))"
        " or (surface_type = 'question' and surface_id = any(%s))"
        " or (surface_type = 'notebook_page' and surface_id = any(%s))) order by surface_type, surface_id limit %s",
        (surfaces["section"], surfaces["question"], surfaces["notebook_page"], MAX_INK_LAYERS),
    )
    found = [(r["surface_type"], r["surface_id"]) for r in await cur.fetchall()]
    now_ms = time.time_ns() // 1_000_000
    layers: list[OfflineInkLayer] = []
    for stype, sid in found:
        try:
            surface: Surface = await resolve_surface(conn, stype, sid)  # the ink API's own visibility check
        except SurfaceNotFound:
            continue
        r = await read_layer(conn, uid, surface, None)
        layers.append(
            OfflineInkLayer(
                surface_type=stype,
                surface_id=sid,
                layer=InkLayerOut(
                    layer_id=r.layer_id,
                    rev=r.rev,
                    items=r.items,
                    reset=r.reset,
                    hash=r.hash,
                    item_count=r.item_count,
                    outdated=r.outdated,
                    server_now=now_ms,
                ),
            )
        )
    return layers


async def _pathway(conn: psycopg.AsyncConnection[Any]) -> list[PathwayTaskStatus]:
    cur = await conn.execute(
        "select task_id, status, due from public.user_pathway_tasks where user_id = (select auth.uid())"
        " order by task_id"
    )
    return [PathwayTaskStatus.model_validate(dict(r)) for r in await cur.fetchall()]


# ── Route ──


@router.get(
    "/offline/week",
    operation_id="getOfflineWeek",
    summary="Everything 'Download this week' stores on the device for 7 plan days, with a size per part",
    response_model=OfflineWeek,
    responses=_ERRORS,
)
async def get_offline_week(
    request: Request,
    user: Annotated[CurrentUser, limited("offline_read")],
    start: Annotated[
        dt.date | None, Query(description="First day (YYYY-MM-DD); default today in the profile's time zone")
    ] = None,
) -> JSONResponse:
    db = _db(request)
    first, tz = await _week_start(db, user, start)
    last = first + dt.timedelta(days=DAYS - 1)
    uid = user.user_id

    plan = await as_user(db, user, lambda conn: _plan(conn, first, last))
    ch = await _chapters(request, user, planned_chapters(plan))
    blocks, questions, exam_on = await _questions(request, user)
    _, card_cfg = await card_params(request)
    due_by = dt.datetime.combine(last + dt.timedelta(days=1), dt.time(), tzinfo=_zone(tz)).astimezone(dt.UTC)
    due: DueResponse = await as_service(db, lambda conn: load_due(conn, uid, due_by, MAX_CARDS, card_cfg))
    section_ids = [s.id for c in ch.chapters for s in c.sections]
    question_ids = sorted({q.view.question.id for q in questions})

    async def own(
        conn: psycopg.AsyncConnection[Any],
    ) -> tuple[NoteList, list[NotebookDetail], list[OfflineInkLayer], list[PathwayTaskStatus]]:
        notes = await _notes(conn, uid, section_ids, question_ids)
        books = await _notebooks(conn, uid)
        page_ids = [str(pg.id) for b in books for pg in b.pages]
        ink = await _ink(conn, uid, {"section": section_ids, "question": question_ids, "notebook_page": page_ids})
        return notes, books, ink, await _pathway(conn)

    notes, books, ink, pathway = await as_user(db, user, own)
    figures, videos, skipped = offline_media(ch.chapters, ch.public_media)
    parts = [
        part_size("plan", sum(len(d.items) for d in plan.days), plan),
        part_size("chapters", len(ch.chapters), ch.chapters, ch.revisions, ch.positions),
        part_size("figures", len(figures), figures),
        part_size("videos", len(videos), videos),
        part_size("questions", len(questions), blocks, questions),
        part_size("due", len(due.cards), due),
        part_size("notes", len(notes.items), notes),
        part_size("notebooks", len(books), books),
        part_size("ink", len(ink), ink),
        part_size("pathway", len(pathway), pathway),
    ]
    total = sum(s.bytes for s in parts)
    week = OfflineWeek(
        start=first,
        end=last,
        timezone=tz,
        generated_at=dt.datetime.now(dt.UTC),
        server_now_ms=time.time_ns() // 1_000_000,
        plan=plan,
        chapters=ch.chapters,
        section_revisions=ch.revisions,
        positions=ch.positions,
        figures=figures,
        videos=videos,
        skipped_media=skipped,
        blocks=blocks,
        questions=questions,
        exam_active=exam_on,
        due_by=due_by,
        due=due,
        notes=notes,
        notebooks=books,
        ink=ink,
        pathway=pathway,
        parts=parts,
        bytes=total,
    )
    log.info("offline_week", **{s.part: s.count for s in parts}, bytes=total)
    return JSONResponse(
        week.model_dump(mode="json", by_alias=True),
        headers={"cache-control": "private, no-store", "vary": "authorization"},
    )


def install_offline_routes(app: Any) -> None:
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
    app.include_router(router)


__all__ = ["install_offline_routes", "offline_media", "planned_chapters", "router"]
