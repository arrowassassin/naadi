"""Flashcards: due list, create / edit / delete, server-scheduled reviews (FSRS-6), stats.

Scheduling is server-authoritative: `card_reviews` and `card_review_logs` are written only here, with the pool's
privileged role (clients may only SELECT their own rows; migration 0012 revokes the rest). Every privileged statement
filters on the caller's id and applies `VISIBLE`, the same row filter as the `flashcards` RLS policy:

- the caller's own cards, or
- shared cards (`owner_user_id is null`) that are not linked to a question, or are linked to a question the caller
  has a *graded* attempt on (`chosen` and `correct` set; lead decision 20261116T200750, segment 08's 0010).

A shared card linked to a question restates its answer, so it never reaches a client before that attempt: not in
the due list, a review, the stats or an edit. Card writes (create / edit / delete) run as the user, so RLS also
enforces ownership and `origin = 'user'`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from psycopg import errors as pg_errors
from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb

from ..auth import CurrentUser, get_account_user
from ..auth_db import user_tx
from ..auth_limits import RateLimiter
from ..chapters_media import MediaUrlResolver
from ..config_api.service import AppConfigService
from ..courses import MediaOut, MediaSource
from ..db import Database, DatabaseNotConfigured
from ..errors import ErrorResponse, error_response
from ..logs import get_logger
from . import docs, stats
from .models import (
    CardCreate,
    CardOut,
    CardPatch,
    CardSchedule,
    CardSource,
    CardStats,
    DueResponse,
    FsrsSchedulerConfig,
    RetentionOut,
    ReviewIn,
    ReviewOut,
)
from .scheduler import FsrsParams, Memory, review

log = get_logger("naadi.cards")
router = APIRouter(prefix="/api/cards", tags=["cards"])

Row = dict[str, Any]
DEFAULT_DECK = "My cards"
DUE_LIMIT_DEFAULT = 50
DUE_LIMIT_MAX = 200
# An offline review may be this old (the offline week plus slack); older ones are refused, not back-dated.
MAX_REVIEW_AGE = dt.timedelta(days=31)
# How far a rating may claim to be before its card was made (device clocks run a little behind) and still count.
CREATED_SKEW = dt.timedelta(minutes=5)
RETENTION_WINDOW = dt.timedelta(days=30)
STREAK_WINDOW_DAYS = 400

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Database unavailable (code cards_unavailable)"},
}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Not found or not visible (code card_not_found)"}
}

# The flashcards RLS row filter for the caller %(uid)s, plus the graded-attempt gate (see module docstring).
VISIBLE = """(
  f.owner_user_id = %(uid)s
  or (f.owner_user_id is null and (f.question_id is null or exists (
    select 1 from public.attempts a
    where a.user_id = %(uid)s and a.question_id = f.question_id and a.chosen is not null and a.correct is not null)))
)"""

_CARD_COLUMNS = """
  f.id, f.owner_user_id, f.deck, f.origin, f.front_html, f.back_html, f.front_doc, f.back_doc, f.question_id,
  f.section_id, f.taxonomy_ids, f.created_at, f.updated_at,
  r.state as r_state, r.step as r_step, r.stability as r_stability, r.difficulty as r_difficulty, r.due as r_due,
  r.last_review as r_last_review, r.reps as r_reps, r.lapses as r_lapses,
  case when q.id is not null and q.status = 'published' then q.id end as source_question_id,
  case when s.id is not null and (c.owner_user_id = %(uid)s or (c.owner_user_id is null and c.status = 'published'))
       then s.id end as source_section_id,
  c.id as source_chapter_id
"""
_CARD_FROM = """
from public.flashcards f
left join public.card_reviews r on r.card_id = f.id and r.user_id = %(uid)s
left join public.questions q on q.id = f.question_id
  and (q.owner_user_id is null or q.owner_user_id = %(uid)s)
left join public.chapter_sections s on s.id = f.section_id
left join public.chapters c on c.id = s.chapter_id
"""


class CardsError(Exception):
    """An error with a stable code the client turns into words (ui_copy `cards.error.<code>`)."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def default_limiters() -> dict[str, RateLimiter]:
    return {"cards_read": RateLimiter(300, 60.0), "cards_write": RateLimiter(240, 60.0)}


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


async def _params(request: Request) -> tuple[FsrsParams, dict[str, Any]]:
    cfg: dict[str, Any] = await _config(request).get("fsrs")
    return FsrsParams.from_config(cfg), cfg


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@asynccontextmanager
async def _service_tx(db: Database) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    """A transaction with the pool's own (privileged) role: the only writer of card scheduling."""
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
                # FSRS state must round-trip exactly (some images default to 15-digit float output).
                await conn.execute("set local extra_float_digits = 3")
                yield conn
        finally:
            conn.row_factory = tuple_row


async def _guard[T](fn: Callable[[], Awaitable[T]]) -> T:
    try:
        return await fn()
    except DatabaseNotConfigured as exc:
        raise CardsError(503, "cards_unavailable") from exc
    except (psycopg.OperationalError, TimeoutError) as exc:
        log.warning("cards_db_unavailable", error=type(exc).__name__)
        raise CardsError(503, "cards_unavailable") from exc


async def as_service[T](db: Database, fn: Callable[[psycopg.AsyncConnection[Any]], Awaitable[T]]) -> T:
    async def run() -> T:
        async with _service_tx(db) as conn:
            return await fn(conn)

    return await _guard(run)


async def as_user[T](db: Database, user: CurrentUser, fn: Callable[[psycopg.AsyncConnection[Any]], Awaitable[T]]) -> T:
    async def run() -> T:
        async with user_tx(db, user, deadline_s=8.0) as conn:
            return await fn(conn)

    return await _guard(run)


async def _rows(conn: psycopg.AsyncConnection[Any], query: str, params: dict[str, Any]) -> list[Row]:
    cur = await conn.execute(query, params)
    return [dict(r) for r in await cur.fetchall()]


async def _first(conn: psycopg.AsyncConnection[Any], query: str, params: dict[str, Any]) -> Row | None:
    rows = await _rows(conn, query, params)
    return rows[0] if rows else None


# ── Shaping ──


def _memory(row: Row) -> Memory:
    if row.get("r_state") is None:
        return Memory()
    return Memory(
        state=int(row["r_state"]),
        step=row["r_step"],
        stability=row["r_stability"],
        difficulty=row["r_difficulty"],
        due=row["r_due"],
        last_review=row["r_last_review"],
        reps=int(row["r_reps"] or 0),
        lapses=int(row["r_lapses"] or 0),
    )


def _schedule(m: Memory) -> CardSchedule:
    return CardSchedule(
        state=m.state,
        step=m.step,
        stability=m.stability,
        difficulty=m.difficulty,
        due=m.due,
        last_review=m.last_review,
        reps=m.reps,
        lapses=m.lapses,
    )


def _card(row: Row, uid: uuid.UUID) -> CardOut:
    source: CardSource | None = None
    if row.get("source_section_id"):
        source = CardSource(kind="section", id=row["source_section_id"], chapter_id=row["source_chapter_id"])
    elif row.get("source_question_id"):
        source = CardSource(kind="question", id=row["source_question_id"], chapter_id=None)
    return CardOut(
        id=row["id"],
        deck=row["deck"],
        origin=row["origin"],
        editable=row["owner_user_id"] == uid and row["origin"] == "user",
        front=docs.face_doc(row["front_doc"], row["front_html"]),
        back=docs.face_doc(row["back_doc"], row["back_html"]),
        front_text=docs.face_text(row["front_doc"], row["front_html"]),
        back_text=docs.face_text(row["back_doc"], row["back_html"]),
        source=source,
        taxonomy_ids=list(row["taxonomy_ids"] or []),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        schedule=_schedule(_memory(row)),
    )


def _figure_ids(doc: object) -> set[str]:
    found: set[str] = set()
    stack: list[object] = [doc]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            if n.get("type") == "figure" and isinstance(n.get("attrs"), dict):
                mid = n["attrs"].get("mediaId")
                if isinstance(mid, str):
                    found.add(mid)
            stack.extend(n.get("content") or [])
    return found


async def _attach_media(
    request: Request, conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, cards: list[CardOut]
) -> None:
    """Fill `CardOut.media` for the pictures the faces use. Only the caller's own private pictures and approved
    shared ones resolve (the same row filter as `media_assets` RLS); anything else is left out."""
    ids = sorted({m for c in cards for d in (c.front, c.back) for m in _figure_ids(d)})
    if not ids:
        return
    rows = await _rows(
        conn,
        "select m.id, m.kind, m.url, m.title, m.caption, m.attribution, m.width, m.height, m.youtube_id,"
        " m.duration_s, m.embeddable, src.id as source_id, src.name as source_name, src.license as source_license,"
        " src.url as source_url from public.media_assets m left join public.sources src on src.id = m.source_id"
        " where m.id = any(%(ids)s) and m.review_status = 'approved'"
        " and (m.owner_user_id is null or m.owner_user_id = %(uid)s)",
        {"ids": ids, "uid": uid},
    )
    resolver: MediaUrlResolver = request.app.state.media_resolver
    urls = await resolver.resolve(((r["id"], r["url"]) for r in rows), uid)
    media = {
        r["id"]: MediaOut(
            id=r["id"],
            kind=r["kind"],
            url=urls.get(r["id"]),
            title=r["title"],
            caption=r["caption"],
            attribution=r["attribution"],
            width=r["width"],
            height=r["height"],
            youtube_id=r["youtube_id"],
            duration_s=r["duration_s"],
            embeddable=r["embeddable"],
            source=MediaSource(
                id=r["source_id"], name=r["source_name"], license=r["source_license"], url=r["source_url"]
            )
            if r["source_id"]
            else None,
        )
        for r in rows
    }
    for c in cards:
        wanted = {m for d in (c.front, c.back) for m in _figure_ids(d)}
        c.media = {m: media[m] for m in wanted if m in media}


async def _visible_card(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, card_id: uuid.UUID) -> Row | None:
    return await _first(
        conn,
        f"select {_CARD_COLUMNS} {_CARD_FROM} where f.id = %(id)s and {VISIBLE}",
        {"uid": uid, "id": card_id},
    )


# ── Due list ──


@router.get(
    "/due",
    operation_id="getDueCards",
    summary="Cards due for review now (due reviews first, then new cards), with the scheduler parameters",
    response_model=DueResponse,
    responses={**_ERRORS},
)
async def get_due(
    request: Request,
    user: Annotated[CurrentUser, limited("cards_read")],
    limit: Annotated[int, Query(ge=1, le=DUE_LIMIT_MAX)] = DUE_LIMIT_DEFAULT,
) -> DueResponse:
    _, cfg = await _params(request)
    now = _now()
    p = {"uid": user.user_id, "now": now, "limit": limit}

    async def run(conn: psycopg.AsyncConnection[Any]) -> DueResponse:
        rows = await _rows(
            conn,
            f"select {_CARD_COLUMNS} {_CARD_FROM}"
            f" where {VISIBLE} and (r.card_id is null or r.due <= %(now)s)"
            " order by (r.card_id is null), r.due, f.created_at, f.id limit %(limit)s",
            p,
        )
        counts = await _first(
            conn,
            "select count(*) filter (where r.card_id is null or r.due <= %(now)s) as due,"  # noqa: S608
            " min(r.due) filter (where r.due > %(now)s) as next_due"
            f" from public.flashcards f left join public.card_reviews r on r.card_id = f.id and r.user_id = %(uid)s"
            f" where {VISIBLE}",
            p,
        )
        assert counts is not None
        cards = [_card(r, user.user_id) for r in rows]
        await _attach_media(request, conn, user.user_id, cards)
        return DueResponse(
            now=now,
            cards=cards,
            due_count=int(counts["due"]),
            next_due=counts["next_due"],
            scheduler=FsrsSchedulerConfig(**cfg),
        )

    return await as_service(_db(request), run)


# ── Create / edit / delete (as the user: RLS enforces own + origin 'user') ──


async def _has_graded_attempt(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, question_id: str) -> bool:
    row = await _first(
        conn,
        "select exists (select 1 from public.attempts a join public.questions q on q.id = a.question_id"
        " where a.user_id = %(uid)s and a.question_id = %(q)s and a.chosen is not null and a.correct is not null"
        " and q.status = 'published' and (q.owner_user_id is null or q.owner_user_id = %(uid)s)) as ok",
        {"uid": uid, "q": question_id},
    )
    return bool(row and row["ok"])


@router.post(
    "",
    operation_id="createCard",
    summary="Create your own card from text, optionally linked to a question you answered or a section",
    status_code=201,
    response_model=CardOut,
    responses={
        **_ERRORS,
        404: {
            "model": ErrorResponse,
            "description": "card_question_not_attempted (no graded attempt) or card_section_not_found",
        },
        422: {"model": ErrorResponse, "description": "Invalid request"},
    },
)
async def create_card(
    request: Request, body: CardCreate, user: Annotated[CurrentUser, limited("cards_write")]
) -> CardOut:
    db = _db(request)
    if body.question_id is not None:
        q = body.question_id

        async def gate(conn: psycopg.AsyncConnection[Any]) -> bool:
            return await _has_graded_attempt(conn, user.user_id, q)

        if not await as_service(db, gate):
            raise CardsError(404, "card_question_not_attempted")
    card_id = uuid.uuid4()

    async def insert(conn: psycopg.AsyncConnection[Any]) -> None:
        try:
            await conn.execute(
                "insert into public.flashcards (id, owner_user_id, deck, front_html, back_html, front_doc, back_doc,"
                " question_id, section_id, origin)"
                " values (%s, (select auth.uid()), %s, %s, %s, %s, %s, %s, %s, 'user')",
                (
                    card_id,
                    body.deck or DEFAULT_DECK,
                    docs.text_to_html(body.front_text),
                    docs.text_to_html(body.back_text),
                    Jsonb(docs.text_to_doc(body.front_text)),
                    Jsonb(docs.text_to_doc(body.back_text)),
                    body.question_id,
                    body.section_id,
                ),
            )
        except pg_errors.InsufficientPrivilege as exc:  # the RLS check: a hidden question or section
            raise CardsError(404, "card_section_not_found" if body.section_id else "card_question_not_found") from exc
        except pg_errors.ForeignKeyViolation as exc:
            raise CardsError(404, "card_section_not_found" if body.section_id else "card_question_not_found") from exc

    await as_user(db, user, insert)
    log.info("card_created", linked_question=body.question_id is not None, linked_section=body.section_id is not None)

    async def read(conn: psycopg.AsyncConnection[Any]) -> CardOut:
        row = await _visible_card(conn, user.user_id, card_id)
        assert row is not None
        return _card(row, user.user_id)

    return await as_service(db, read)


async def _editable_or_error(db: Database, uid: uuid.UUID, card_id: uuid.UUID) -> None:
    async def run(conn: psycopg.AsyncConnection[Any]) -> Row | None:
        return await _first(
            conn,
            f"select f.owner_user_id, f.origin from public.flashcards f where f.id = %(id)s and {VISIBLE}",  # noqa: S608
            {"uid": uid, "id": card_id},
        )

    row = await as_service(db, run)
    if row is None:
        raise CardsError(404, "card_not_found")
    if row["owner_user_id"] != uid or row["origin"] != "user":
        raise CardsError(403, "card_not_editable")


@router.patch(
    "/{card_id}",
    operation_id="updateCard",
    summary="Edit your own card (origin user); the schedule is kept",
    response_model=CardOut,
    responses={
        **_ERRORS,
        **_NOT_FOUND,
        403: {"model": ErrorResponse, "description": "card_not_editable"},
        422: {"model": ErrorResponse, "description": "Invalid request"},
    },
)
async def update_card(
    request: Request,
    body: CardPatch,
    user: Annotated[CurrentUser, limited("cards_write")],
    card_id: Annotated[uuid.UUID, Path()],
) -> CardOut:
    db = _db(request)
    await _editable_or_error(db, user.user_id, card_id)
    sets: list[str] = []
    values: list[Any] = []
    if body.front_text is not None:
        sets += ["front_html = %s", "front_doc = %s"]
        values += [docs.text_to_html(body.front_text), Jsonb(docs.text_to_doc(body.front_text))]
    if body.back_text is not None:
        sets += ["back_html = %s", "back_doc = %s"]
        values += [docs.text_to_html(body.back_text), Jsonb(docs.text_to_doc(body.back_text))]
    if body.deck is not None:
        sets.append("deck = %s")
        values.append(body.deck)

    if sets:

        async def write(conn: psycopg.AsyncConnection[Any]) -> int:
            cur = await conn.execute(
                f"update public.flashcards set {', '.join(sets)} where id = %s",  # noqa: S608 - fixed fragments
                (*values, card_id),
            )
            return cur.rowcount

        if await as_user(db, user, write) == 0:
            raise CardsError(404, "card_not_found")

    async def read(conn: psycopg.AsyncConnection[Any]) -> CardOut:
        row = await _visible_card(conn, user.user_id, card_id)
        if row is None:
            raise CardsError(404, "card_not_found")
        return _card(row, user.user_id)

    return await as_service(db, read)


@router.delete(
    "/{card_id}",
    operation_id="deleteCard",
    summary="Delete your own card (origin user) with its schedule and review history",
    status_code=204,
    responses={**_ERRORS, **_NOT_FOUND, 403: {"model": ErrorResponse, "description": "card_not_editable"}},
)
async def delete_card(
    request: Request, user: Annotated[CurrentUser, limited("cards_write")], card_id: Annotated[uuid.UUID, Path()]
) -> Response:
    db = _db(request)
    await _editable_or_error(db, user.user_id, card_id)

    async def run(conn: psycopg.AsyncConnection[Any]) -> int:
        cur = await conn.execute("delete from public.flashcards where id = %s", (card_id,))
        return cur.rowcount

    if await as_user(db, user, run) == 0:
        raise CardsError(404, "card_not_found")
    return Response(status_code=204)


# ── Review ──


def _replay(row: Row) -> ReviewOut:
    next_due: dt.datetime = row["next_due"]
    reviewed_at: dt.datetime = row["review"]
    return ReviewOut(
        card_id=row["card_id"],
        client_review_id=row["client_review_id"],
        replayed=True,
        rating=int(row["rating"]),
        reviewed_at=reviewed_at,
        scheduled_days=(next_due - reviewed_at).days,
        schedule=CardSchedule(
            state=int(row["next_state"]),
            step=row["next_step"],
            stability=row["next_stability"],
            difficulty=row["next_difficulty"],
            due=next_due,
            last_review=reviewed_at,
            reps=int(row["reps"]),
            lapses=int(row["lapses"]),
        ),
    )


@router.post(
    "/{card_id}/review",
    operation_id="reviewCard",
    summary="Rate a card (1 Again … 4 Easy); the server schedules it. Idempotent on client_review_id",
    response_model=ReviewOut,
    responses={
        **_ERRORS,
        **_NOT_FOUND,
        409: {
            "model": ErrorResponse,
            "description": "review_out_of_order (older than the card's last review), review_before_card (older"
            " than the card itself) or review_id_reused",
        },
        422: {"model": ErrorResponse, "description": "Invalid request, or review_too_old"},
    },
)
async def review_card(
    request: Request,
    body: ReviewIn,
    user: Annotated[CurrentUser, limited("cards_write")],
    card_id: Annotated[uuid.UUID, Path()],
) -> ReviewOut:
    params, _ = await _params(request)
    now = _now()
    reviewed_at = min(body.reviewed_at.astimezone(dt.UTC), now) if body.reviewed_at else now
    if now - reviewed_at > MAX_REVIEW_AGE:
        raise CardsError(422, "review_too_old")
    uid = user.user_id

    async def run(conn: psycopg.AsyncConnection[Any]) -> ReviewOut:
        # One use of a review id at a time per student (the same id sent for two cards at once must not race to
        # the unique index), then one review of a card at a time (a new card has no row to lock yet). Always in
        # this order, so two requests never wait on each other's second lock.
        await conn.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"naadi.card_review_id:{uid}:{body.client_review_id}",),
        )
        await conn.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"naadi.card_review:{uid}:{card_id}",)
        )
        done = await _first(
            conn,
            "select * from public.card_review_logs where user_id = %(uid)s and client_review_id = %(rid)s",
            {"uid": uid, "rid": body.client_review_id},
        )
        if done is not None:
            if done["card_id"] != card_id:
                raise CardsError(409, "review_id_reused")
            return _replay(done)
        row = await _visible_card(conn, uid, card_id)
        if row is None:
            raise CardsError(404, "card_not_found")
        # A rating cannot predate the card: refusing it keeps a fresh card from carrying a made-up history. A device
        # clock a little behind the server's would refuse the first rating of a card just made, so a claim within
        # CREATED_SKEW before the card counts as made at its creation.
        at = reviewed_at
        if at < row["created_at"]:
            if row["created_at"] - at > CREATED_SKEW:
                raise CardsError(409, "review_before_card")
            at = row["created_at"]
        memory = _memory(row)
        if memory.last_review is not None and at < memory.last_review:
            raise CardsError(409, "review_out_of_order")
        out = review(params, str(card_id), memory, body.rating, at)
        m = out.memory
        prev = await _first(
            conn,
            "select elapsed_days from public.card_review_logs where user_id = %(uid)s and card_id = %(id)s"
            " order by review desc, created_at desc limit 1",
            {"uid": uid, "id": card_id},
        )
        prev_scheduled = (
            (memory.due - memory.last_review).days if memory.due is not None and memory.last_review is not None else 0
        )
        await conn.execute(
            "insert into public.card_reviews (user_id, card_id, due, stability, difficulty, elapsed_days,"
            " scheduled_days, reps, lapses, state, last_review, step)"
            " values (%(uid)s, %(id)s, %(due)s, %(s)s, %(d)s, %(ed)s, %(sd)s, %(reps)s, %(lapses)s, %(state)s,"
            " %(at)s, %(step)s)"
            " on conflict (user_id, card_id) do update set due = excluded.due, stability = excluded.stability,"
            " difficulty = excluded.difficulty, elapsed_days = excluded.elapsed_days,"
            " scheduled_days = excluded.scheduled_days, reps = excluded.reps, lapses = excluded.lapses,"
            " state = excluded.state, last_review = excluded.last_review, step = excluded.step",
            {
                "uid": uid,
                "id": card_id,
                "due": m.due,
                "s": m.stability,
                "d": m.difficulty,
                "ed": out.elapsed_days,
                "sd": out.scheduled_days,
                "reps": m.reps,
                "lapses": m.lapses,
                "state": m.state,
                "at": at,
                "step": m.step,
            },
        )
        # ts-fsrs ReviewLog fields describe the card before this review; next_* is the outcome.
        logged = await _first(
            conn,
            "insert into public.card_review_logs (user_id, card_id, client_review_id, rating, state, due, stability,"
            " difficulty, elapsed_days, last_elapsed_days, scheduled_days, learning_step, review, duration_ms,"
            " next_state, next_step, next_due, next_stability, next_difficulty, reps, lapses)"
            " values (%(uid)s, %(id)s, %(rid)s, %(rating)s, %(state)s, %(due)s, %(s)s, %(d)s, %(ed)s, %(led)s,"
            " %(sd)s, %(step)s, %(at)s, %(ms)s, %(ns)s, %(nstep)s, %(ndue)s, %(nst)s, %(nd)s, %(reps)s, %(lapses)s)"
            " on conflict (user_id, client_review_id) do nothing returning *",
            {
                "uid": uid,
                "id": card_id,
                "rid": body.client_review_id,
                "rating": body.rating,
                "state": memory.state,
                "due": memory.last_review or memory.due or at,
                "s": memory.stability,
                "d": memory.difficulty,
                "ed": out.elapsed_days,
                "led": int(prev["elapsed_days"]) if prev else 0,
                "sd": max(0, prev_scheduled),
                "step": memory.step,
                "at": at,
                "ms": body.duration_ms,
                "ns": m.state,
                "nstep": m.step,
                "ndue": m.due,
                "nst": m.stability,
                "nd": m.difficulty,
                "reps": m.reps,
                "lapses": m.lapses,
            },
        )
        if logged is None:  # the id was taken after the check above (the lock makes this unreachable); roll back
            raise CardsError(409, "review_id_reused")
        result = _replay(logged)
        return result.model_copy(update={"replayed": False})

    return await as_service(_db(request), run)


# ── Stats ──

# When a review counts for "reviewed today" and the streak: the time the student rated, unless the client claimed a
# time more than a day before the server received it (an offline replay is that old only rarely; a made-up history
# always is). Then the receive time counts, so a backdated batch cannot fill in a streak or someone else's today.
CLAIM_TRUSTED_FOR = dt.timedelta(hours=24)
_COUNTED_AT = (
    f"(case when l.created_at - l.review > interval '{int(CLAIM_TRUSTED_FOR.total_seconds())} seconds'"
    " then l.created_at else l.review end)"
)


@router.get(
    "/stats",
    operation_id="getCardStats",
    summary="Due today / tomorrow (in your time zone), new, reviewed today, 30-day retention, streak",
    response_model=CardStats,
    responses={**_ERRORS, 422: {"model": ErrorResponse, "description": "card_zone_unknown"}},
)
async def get_stats(
    request: Request,
    user: Annotated[CurrentUser, limited("cards_read")],
    tz: Annotated[str | None, Query(max_length=64, description="IANA zone; default your profile's")] = None,
) -> CardStats:
    if tz is not None and not stats.valid_zone(tz):
        raise CardsError(422, "card_zone_unknown")
    now = _now()
    uid = user.user_id

    async def run(conn: psycopg.AsyncConnection[Any]) -> CardStats:
        zone_name = tz
        if zone_name is None:
            prof = await _first(conn, "select timezone from public.profiles where user_id = %(uid)s", {"uid": uid})
            zone_name = prof["timezone"] if prof and stats.valid_zone(prof["timezone"]) else stats.DEFAULT_ZONE
        zone = stats.zone(zone_name)
        today_start, tomorrow_start, after_start = stats.day_bounds(now, zone)
        p = {
            "uid": uid,
            "now": now,
            "tomorrow": tomorrow_start,
            "after": after_start,
            "today": today_start,
            "since": now - RETENTION_WINDOW,
            "streak_since": now - dt.timedelta(days=STREAK_WINDOW_DAYS),
        }
        c = await _first(
            conn,
            "select count(*) as total, count(*) filter (where r.card_id is null) as new,"  # noqa: S608
            " count(*) filter (where r.card_id is null or r.due <= %(now)s) as due_now,"
            " count(*) filter (where r.card_id is null or r.due < %(tomorrow)s) as due_today,"
            " count(*) filter (where r.due >= %(tomorrow)s and r.due < %(after)s) as due_tomorrow,"
            " min(r.due) filter (where r.due > %(now)s) as next_due"
            " from public.flashcards f left join public.card_reviews r on r.card_id = f.id and r.user_id = %(uid)s"
            f" where {VISIBLE}",
            p,
        )
        # Only reviews of cards the student can still see (a card's history goes with it).
        logs = await _first(
            conn,
            f"select count(*) filter (where {_COUNTED_AT} >= %(today)s) as today,"  # noqa: S608
            " count(*) filter (where l.review >= %(since)s and l.state = 2) as retention_reviews,"
            " count(*) filter (where l.review >= %(since)s and l.state = 2 and l.rating > 1) as recalled"
            " from public.card_review_logs l join public.flashcards f on f.id = l.card_id"
            f" where l.user_id = %(uid)s and {VISIBLE}",
            p,
        )
        stamps = await _rows(
            conn,
            f"select distinct date_bin('15 minutes', {_COUNTED_AT}, timestamptz '2000-01-01') as t"  # noqa: S608
            " from public.card_review_logs l where l.user_id = %(uid)s"
            f" and {_COUNTED_AT} >= %(streak_since)s",
            p,
        )
        assert c is not None
        assert logs is not None
        # 15-minute bins: every zone offset is a multiple of 15 minutes, so a bin never straddles a local midnight.
        days = {stats.local_date(r["t"], zone) for r in stamps}
        reviews, recalled = int(logs["retention_reviews"]), int(logs["recalled"])
        return CardStats(
            zone=zone_name,
            total=int(c["total"]),
            new=int(c["new"]),
            due_now=int(c["due_now"]),
            due_today=int(c["due_today"]),
            due_tomorrow=int(c["due_tomorrow"]),
            reviewed_today=int(logs["today"]),
            streak_days=stats.streak(days, stats.local_date(now, zone)),
            retention=RetentionOut(reviews=reviews, recalled=recalled, rate=recalled / reviews if reviews else None),
            next_due=c["next_due"],
        )

    return await as_service(_db(request), run)


# Declared after /due and /stats so those paths are not read as a card id.
@router.get(
    "/{card_id}",
    operation_id="getCard",
    summary="One card you can see, with your schedule for it",
    response_model=CardOut,
    responses={**_ERRORS, **_NOT_FOUND},
)
async def get_card(
    request: Request, user: Annotated[CurrentUser, limited("cards_read")], card_id: Annotated[uuid.UUID, Path()]
) -> CardOut:
    async def run(conn: psycopg.AsyncConnection[Any]) -> CardOut:
        row = await _visible_card(conn, user.user_id, card_id)
        if row is None:
            raise CardsError(404, "card_not_found")
        card = _card(row, user.user_id)
        await _attach_media(request, conn, user.user_id, [card])
        return card

    return await as_service(_db(request), run)


async def _cards_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, CardsError)
    return error_response(exc.status, exc.code, exc.code)


def install_cards_routes(app: Any) -> None:
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
    app.add_exception_handler(CardsError, _cards_error)
    app.include_router(router)


__all__ = ["VISIBLE", "install_cards_routes", "router"]
