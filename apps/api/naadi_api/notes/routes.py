"""Notes API (segment 13): notes, highlights, notebooks and their pages.

    GET    /api/notes                         filters: kind, color, anchor_type, anchor_id, notebook_id, q
    POST   /api/notes                         a note or a highlight (idempotent with a client id)
    GET    /api/notes/{id}                    one note with its body (whiteboard scenes included)
    PATCH  /api/notes/{id}                    partial update
    DELETE /api/notes/{id}                    also removes its index rows (and a page's ink)
    GET    /api/notebooks                     the shelf
    POST   /api/notebooks                     a notebook with its first page
    GET    /api/notebooks/{id}                a notebook with its pages in order
    PATCH  /api/notebooks/{id}
    DELETE /api/notebooks/{id}                its pages go with it; notes filed in it stay, unfiled
    POST   /api/notebooks/{id}/pages          add a page (at `position`, default the end)
    PUT    /api/notebooks/{id}/pages/order    reorder every page

Writes are the API's (migration 0017: clients SELECT only). On every save the server computes `body_text` (TipTap
text, or an Excalidraw scene's text elements) and re-indexes the note through S07's job (`ref_type 'note'`, owner
set); a delete removes the index rows in the same transaction. Question anchors need a graded attempt (403
`note_question_not_attempted`); every other anchor must be visible to the caller (404 `note_anchor_not_found`).

Offline sync (segment 18): every write takes an optional `Idempotency-Key` (a replay returns the stored response)
and an optional `client_updated_at_ms` (query parameter on DELETE). Rows are last-writer-wins on that stamp
(`naadi_api/sync/lww.py`): a create replayed onto an existing row, a PATCH or a DELETE whose stamp is not newer than
the stored row's changes nothing and answers with the stored row and `applied: false` (DELETE: 200 instead of 204).
A delete leaves a tombstone; a create, PATCH or page create at or below a tombstone's stamp answers 409
`sync_deleted` with `tombstone.client_updated_at_ms` (a client whose change is newer re-creates the row with a
create carrying its stamp). Without a stamp a write behaves as before segment 18 (always applies, stamped with the
server clock).

Bodies, titles and quotes are never logged: log lines carry kinds and counts. Errors carry a stable `code` only.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from psycopg.rows import dict_row, tuple_row

from ..auth import CurrentUser, get_account_user
from ..auth_db import user_tx
from ..auth_limits import RateLimiter
from ..db import Database, DatabaseNotConfigured
from ..embeddings.jobs import schedule_reindex
from ..errors import ErrorResponse, error_response
from ..logs import get_logger
from ..sync import lww
from ..sync.idempotency import IDEMPOTENT, IdempotentRoute
from ..sync.models import SYNC_DELETED_DESCRIPTION, ClientStamp, SyncDeleted, SyncDeletedResponse
from . import store
from .models import (
    LIST_LIMIT_DEFAULT,
    LIST_LIMIT_MAX,
    MAX_BODY_BYTES,
    MAX_REQUEST_BYTES,
    AnchorType,
    HighlightColor,
    NotebookCreate,
    NotebookDetail,
    NotebookList,
    NotebookOut,
    NotebookPatch,
    NoteCounts,
    NoteCreate,
    NoteKind,
    NoteList,
    NoteOut,
    NotePatch,
    PageCreate,
    PageOrder,
    check_shape,
)
from .store import NewNote, NoteFilter, NotesError
from .text import MAX_DEPTH, body_text, json_depth, valid_tiptap

log = get_logger("naadi.notes")
router = APIRouter(prefix="/api", tags=["notes"], route_class=IdempotentRoute)

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Database unavailable (code notes_unavailable)"},
}
_NOTE_404: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "note_not_found"},
}
_NOTEBOOK_404: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "notebook_not_found"},
}
_WRITE: dict[int | str, dict[str, Any]] = {
    413: {"model": ErrorResponse, "description": "note_body_too_large (the body is over 1 MB)"},
    422: {"model": ErrorResponse, "description": "validation_error, note_invalid or note_body_invalid"},
}
_STAMP_QUERY = Query(
    description="Offline sync: the device's time of the delete (ms). A stored row with a newer or equal stamp is "
    "kept and returned with 200 and applied false; otherwise the row is deleted (204) and a tombstone recorded"
)


def _conflict(codes: str) -> dict[int | str, dict[str, Any]]:
    return {409: {"model": SyncDeletedResponse, "description": f"{codes}, or {SYNC_DELETED_DESCRIPTION}"}}


def _entity(kind: str) -> lww.Entity:
    return "highlight" if kind == "highlight" else "page" if kind == "page" else "note"


async def _refuse_if_deleted(
    conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, entities: tuple[lww.Entity, ...], key: uuid.UUID, stamp: int
) -> int | None:
    """409 sync_deleted when `key` has a tombstone at least as new as `stamp`; else the tombstone's stamp (or None)."""
    found = await lww.find_tombstone(conn, uid, entities, str(key))
    if found is not None and stamp <= found[1]:
        raise SyncDeleted(found[0], str(key), found[1])
    return found[1] if found is not None else None


async def _guard_create(
    conn: psycopg.AsyncConnection[Any],
    uid: uuid.UUID,
    entities: tuple[lww.Entity, ...],
    key: uuid.UUID,
    client_ms: int | None,
    now: int,
) -> int | None:
    """A create with a stamp at or below the id's tombstone is refused (409 sync_deleted); returns the tombstone."""
    if client_ms is None:
        return await lww.tombstone_ms(conn, uid, entities, str(key))
    return await _refuse_if_deleted(conn, uid, entities, key, lww.clamp(client_ms, now))


_ANCHOR: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse, "description": "note_question_not_attempted (no graded answer on that question yet)"},
    404: {
        "model": ErrorResponse,
        "description": "note_anchor_not_found (anchor not visible), notebook_not_found or note_not_found",
    },
}


def default_limiters() -> dict[str, RateLimiter]:
    # Debounced editor saves (whiteboard scenes every ~2 s) plus list refreshes on several open screens.
    return {"notes_read": RateLimiter(600, 60.0), "notes_write": RateLimiter(600, 60.0)}


def limited(bucket: str) -> Any:
    async def dep(request: Request, user: Annotated[CurrentUser, Depends(get_account_user)]) -> CurrentUser:
        limiters: dict[str, RateLimiter] = request.app.state.rate_limiters
        limiters[bucket].hit(str(user.user_id))
        return user

    return Depends(dep)


def _db(request: Request) -> Database:
    d: Database = request.app.state.db
    return d


async def _body_within_limit(request: Request) -> None:
    """413 before parsing: the declared length first, then the bytes actually read."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_REQUEST_BYTES:
        raise NotesError(413, "note_body_too_large")
    if len(await request.body()) > MAX_REQUEST_BYTES:
        raise NotesError(413, "note_body_too_large")


# ── Transactions ──


@asynccontextmanager
async def _service_tx(db: Database) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    """The API's privileged role (clients cannot write notes); every statement filters on the caller."""
    if not db.configured:
        raise DatabaseNotConfigured("DATABASE_URL is not set")
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    async with db.pool.connection(timeout=5.0) as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]
        try:
            async with conn.transaction():
                await conn.execute("set local statement_timeout = 10000")
                yield conn
        finally:
            conn.row_factory = tuple_row


async def _guard[T](fn: Callable[[], Awaitable[T]]) -> T:
    try:
        return await fn()
    except DatabaseNotConfigured as exc:
        raise NotesError(503, "notes_unavailable") from exc
    except (psycopg.OperationalError, TimeoutError) as exc:
        log.warning("notes_db_unavailable", error=type(exc).__name__)
        raise NotesError(503, "notes_unavailable") from exc


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


# ── Helpers ──


def checked_body(body: dict[str, Any] | None, canvas: str) -> str:
    """Validate a body for its canvas and return its plain text (413 / 422 with a code)."""
    if body is None:
        return ""
    if store.body_bytes(body) > MAX_BODY_BYTES:
        raise NotesError(413, "note_body_too_large")
    if json_depth(body) > MAX_DEPTH:
        raise NotesError(422, "note_body_invalid")
    if canvas == "whiteboard":
        if not isinstance(body.get("elements"), list):
            raise NotesError(422, "note_body_invalid")
    elif not valid_tiptap(body):
        raise NotesError(422, "note_body_invalid")
    return body_text(body, "whiteboard" if canvas == "whiteboard" else "paper")


async def _anchor(db: Database, user: CurrentUser, anchor_type: str | None, anchor_id: str | None) -> None:
    if anchor_type in (None, "free"):
        return

    async def run(conn: psycopg.AsyncConnection[Any]) -> None:
        await store.check_anchor(conn, anchor_type, anchor_id)

    await as_user(db, user, run)


async def read_note(db: Database, user: CurrentUser, note_id: uuid.UUID, *, body: str = store.FULL_BODY) -> NoteOut:
    async def run(conn: psycopg.AsyncConnection[Any]) -> NoteOut:
        row = await store.get_note(conn, user.user_id, note_id, body=body)
        if row is None:
            raise store.not_found()
        sources = await store.load_sources(conn, [row])
        return NoteOut.model_validate(store.shape_note(row, sources))

    return await as_user(db, user, run)


def reindex(request: Request, user: CurrentUser, note_ids: list[str]) -> None:
    """S07's incremental reconcile for these notes (a note whose text is gone loses its rows)."""
    if note_ids:
        schedule_reindex(request.app, ("notes",), owner=user.user_id, ref_ids=note_ids)


LIST_BODY_BUDGET = 4 * 1024 * 1024


def within_budget(rows: list[dict[str, Any]], budget: int = LIST_BODY_BUDGET) -> list[dict[str, Any]]:
    """Keep a list response bounded: once the bodies sent add up to `budget` bytes, later rows omit theirs
    (`body_omitted`; the client fetches the note)."""
    used = 0
    out: list[dict[str, Any]] = []
    for r in rows:
        if r["body"] is not None:
            size = store.body_bytes(r["body"])
            if used + size > budget:
                r = {**r, "body": None, "body_omitted": True}
            else:
                used += size
        out.append(r)
    return out


def forget(request: Request, user: CurrentUser, note_ids: list[str]) -> None:
    """After a delete (whose transaction already removed the index rows): one more reconcile of those refs, queued
    behind any index run still in flight for them, so a run that read the note before the delete cannot leave rows
    behind."""
    if note_ids:
        schedule_reindex(request.app, ("notes",), owner=user.user_id, ref_ids=note_ids)
        schedule_reindex(request.app, ("ink",), owner=user.user_id, ref_ids=[f"notebook_page:{i}" for i in note_ids])


def _created(note: NoteOut, written: bool) -> JSONResponse:
    return JSONResponse(note.model_dump(mode="json", by_alias=True), status_code=201 if written else 200)


# ── Notes ──


@router.get(
    "/notes",
    operation_id="listNotes",
    summary="The caller's notes, highlights and pages (filtered), plus counts over all of them",
    response_model=NoteList,
    responses=_ERRORS,
)
async def list_notes(
    request: Request,
    user: Annotated[CurrentUser, limited("notes_read")],
    kind: NoteKind | None = None,
    color: HighlightColor | None = None,
    anchor_type: AnchorType | None = None,
    anchor_id: Annotated[str | None, Query(max_length=200)] = None,
    notebook_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=200, description="Substring of title, text or quote")] = None,
    limit: Annotated[int, Query(ge=1, le=LIST_LIMIT_MAX)] = LIST_LIMIT_DEFAULT,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    include_body: Annotated[bool, Query(description="false: every body is left out (body_omitted)")] = True,
) -> NoteList:
    f = NoteFilter(kind, color, anchor_type, anchor_id, notebook_id, (q or "").strip() or None)

    async def run(conn: psycopg.AsyncConnection[Any]) -> NoteList:
        rows, more = await store.list_notes(
            conn, user.user_id, f, limit=limit, offset=offset, include_body=include_body
        )
        c = await store.note_counts(conn, user.user_id)
        sources = await store.load_sources(conn, rows)
        return NoteList(
            items=[NoteOut.model_validate(store.shape_note(r, sources)) for r in within_budget(rows)],
            counts=NoteCounts(
                notes=c["notes"],
                highlights=c["highlights"],
                pages=c["pages"],
                by_color={"lime": c["lime"], "violet": c["violet"], "amber": c["amber"]},
                by_anchor={
                    "section": c["a_section"],
                    "question": c["a_question"],
                    "video": c["a_video"],
                    "pdf": c["a_pdf"],
                    "free": c["a_free"],
                },
            ),
            next_offset=offset + limit if more else None,
        )

    return await as_user(_db(request), user, run)


@router.post(
    "/notes",
    operation_id="createNote",
    summary="Create a note or a highlight. A replay with the same id (or a second note on a section) returns the "
    "stored note with 200",
    status_code=201,
    response_model=NoteOut,
    responses={
        **_ERRORS,
        **_WRITE,
        **_ANCHOR,
        200: {
            "model": NoteOut,
            "description": "Replayed onto a stored note: the stored note (applied true when a newer stamp updated it)",
        },
        **_conflict("note_id_conflict or note_limit"),
    },
    dependencies=[Depends(_body_within_limit), IDEMPOTENT],
)
async def create_note(
    request: Request, body: NoteCreate, user: Annotated[CurrentUser, limited("notes_write")]
) -> JSONResponse:
    db = _db(request)
    text = checked_body(body.body, body.canvas)
    await _anchor(db, user, body.anchor_type, body.anchor_id)
    note = NewNote(
        id=body.id or uuid.uuid4(),
        kind=body.kind,
        title=_clean_title(body.title),
        notebook_id=body.notebook_id,
        anchor_type=body.anchor_type,
        anchor_id=body.anchor_id,
        anchor_pos=body.anchor_pos,
        highlight_color=body.highlight_color,
        body=body.body,
        body_text=text,
        paper=None,
        canvas=body.canvas,
        sort=0,
    )
    client_ms = body.client_updated_at_ms
    now = lww.server_now_ms()
    uid = user.user_id

    async def write(conn: psycopg.AsyncConnection[Any]) -> tuple[uuid.UUID, bool, bool]:
        tomb = await _guard_create(conn, uid, lww.NOTE_ENTITIES, note.id, client_ms, now)
        new = replace(note, client_updated_at_ms=lww.decide_write(client_ms, None, tomb, now, False).effective_ms)
        if await store.insert_note(conn, uid, new):
            if tomb is not None:
                await lww.clear_tombstone(conn, uid, lww.NOTE_ENTITIES, str(note.id))
            if note.notebook_id is not None:
                await _touch_notebook(conn, uid, note.notebook_id)
            return note.id, True, True
        existing = await store.resolve_replay(conn, uid, note)
        if client_ms is None:
            return existing, False, False
        return existing, False, await _lww_content(conn, uid, existing, new, client_ms, now)

    note_id, written, applied = await as_service(db, write)
    if written:
        log.info("note_created", kind=note.kind, anchor_type=note.anchor_type or "free", canvas=note.canvas)
    elif applied:
        log.info("note_replay_applied", kind=note.kind)
    if applied:
        reindex(request, user, [str(note_id)])
    out = (await read_note(db, user, note_id)).model_copy(update={"applied": applied})
    return _created(out, written)


# What a create replayed onto an existing row carries over when its stamp wins (the row's identity and place stay).
_CONTENT_COLUMNS = ("title", "body", "body_text", "canvas", "anchor_pos", "highlight_color", "paper")


async def _lww_content(
    conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, note_id: uuid.UUID, note: NewNote, client_ms: int, now: int
) -> bool:
    """A create (offline queue) that met a stored row of the same kind: its content wins if its stamp is newer."""
    head = await store.get_note(conn, uid, note_id, body=store.NO_BODY)
    if head is None or head["kind"] != note.kind:
        return False
    if head["kind"] == "page" and head["notebook_id"] is not None:
        await store.lock_notebook(conn, uid, head["notebook_id"])  # notebook before page rows
    cur = await store.lock_note(conn, uid, note_id)
    decision = lww.decide_write(client_ms, cur["client_updated_at_ms"], None, now, False)
    if not decision.applied:
        return False
    values: dict[str, Any] = {c: getattr(note, c) for c in _CONTENT_COLUMNS}
    if note.kind != "page":
        values.pop("paper")
    values["client_updated_at_ms"] = decision.effective_ms
    await store.update_note(conn, uid, note_id, values)
    if cur["notebook_id"] is not None:
        await _touch_notebook(conn, uid, cur["notebook_id"])
    return True


@router.get(
    "/notes/{note_id}",
    operation_id="getNote",
    summary="One of the caller's notes with its body",
    response_model=NoteOut,
    responses={**_ERRORS, **_NOTE_404},
)
async def get_note(
    request: Request, user: Annotated[CurrentUser, limited("notes_read")], note_id: Annotated[uuid.UUID, Path()]
) -> NoteOut:
    return await read_note(_db(request), user, note_id)


# Columns a PATCH may set, by the field that sets them.
_PATCH_COLUMNS = ("title", "anchor_type", "anchor_id", "anchor_pos", "highlight_color", "notebook_id", "paper")


@router.patch(
    "/notes/{note_id}",
    operation_id="updateNote",
    summary="Partial update of one of the caller's notes (null clears a nullable field)",
    response_model=NoteOut,
    responses={
        **_ERRORS,
        **_WRITE,
        **_ANCHOR,
        **_conflict("note_conflict (if_updated_at did not match), note_section_has_note"),
    },
    dependencies=[Depends(_body_within_limit), IDEMPOTENT],
)
async def update_note(
    request: Request,
    body: NotePatch,
    user: Annotated[CurrentUser, limited("notes_write")],
    note_id: Annotated[uuid.UUID, Path()],
) -> NoteOut:
    db = _db(request)
    sent = body.model_fields_set - {"if_updated_at", "client_updated_at_ms"}
    if "anchor_type" in sent:
        await _anchor(db, user, body.anchor_type, body.anchor_id)
    uid = user.user_id
    client_ms = body.client_updated_at_ms
    now = lww.server_now_ms()

    async def write(conn: psycopg.AsyncConnection[Any]) -> tuple[bool, bool]:
        head = await store.get_note(conn, uid, note_id, body=store.NO_BODY)
        if head is None:
            found = await lww.find_tombstone(conn, uid, lww.NOTE_ENTITIES, str(note_id))
            if found is not None:  # deleted: a newer edit re-creates the row with a create (see the module doc)
                raise SyncDeleted(found[0], str(note_id), found[1])
            raise store.not_found()
        page_book = head["notebook_id"] if head["kind"] == "page" else None
        if page_book is not None:
            await store.lock_notebook(conn, uid, page_book)  # notebook before page rows, as every page operation
        cur = await store.lock_note(conn, uid, note_id)
        if body.if_updated_at is not None and cur["updated_at"] != body.if_updated_at:
            raise NotesError(409, "note_conflict")
        decision = lww.decide_write(client_ms, cur["client_updated_at_ms"], None, now, False)
        if not decision.applied:
            return False, False
        merged = {**cur, **{k: getattr(body, k) for k in sent}}
        if cur["kind"] == "page" and "notebook_id" in sent and merged["notebook_id"] != cur["notebook_id"]:
            raise NotesError(422, "note_invalid")
        try:
            check_shape(
                kind=cur["kind"],
                anchor_type=merged["anchor_type"],
                anchor_id=merged["anchor_id"],
                anchor_pos=body.anchor_pos if "anchor_pos" in sent else _pos(cur["anchor_pos"]),
                color=merged["highlight_color"],
                notebook_id=merged["notebook_id"],
                canvas=merged["canvas"],
            )
        except ValueError as exc:
            raise NotesError(422, "note_invalid") from exc
        values: dict[str, Any] = {c: merged[c] for c in _PATCH_COLUMNS if c in sent}
        values["client_updated_at_ms"] = decision.effective_ms
        if "title" in values:
            values["title"] = _clean_title(values["title"])
        text_changed = bool({"title", "body", "canvas"} & sent)
        if "body" in sent or "canvas" in sent:
            values["body"] = merged["body"]
            values["canvas"] = merged["canvas"]
            values["body_text"] = checked_body(merged["body"], merged["canvas"])
        if "sort" in sent and body.sort is not None:
            if page_book is not None:
                await store.move_page(conn, uid, page_book, note_id, body.sort)
            else:
                values["sort"] = body.sort
        await store.update_note(conn, uid, note_id, values)
        if page_book is not None or merged["notebook_id"] is not None:
            await _touch_notebook(conn, uid, page_book or merged["notebook_id"])
        return text_changed, True

    text_changed, applied = await as_service(db, write)
    log.info("note_updated", fields=sorted(sent), applied=applied)
    if text_changed:
        reindex(request, user, [str(note_id)])
    return (await read_note(db, user, note_id)).model_copy(update={"applied": applied})


@router.delete(
    "/notes/{note_id}",
    operation_id="deleteNote",
    summary="Delete one of the caller's notes with its index rows (a page also loses its ink)",
    status_code=204,
    responses={
        **_ERRORS,
        **_NOTE_404,
        200: {"model": NoteOut, "description": "Not deleted: the stored note is as new or newer (applied false)"},
    },
    dependencies=[IDEMPOTENT],
)
async def delete_note(
    request: Request,
    user: Annotated[CurrentUser, limited("notes_write")],
    note_id: Annotated[uuid.UUID, Path()],
    client_updated_at_ms: Annotated[ClientStamp | None, _STAMP_QUERY] = None,
) -> Response:
    uid = user.user_id
    now = lww.server_now_ms()

    async def write(conn: psycopg.AsyncConnection[Any]) -> str | None:
        head = await store.get_note(conn, uid, note_id, body=store.NO_BODY)
        if head is None:
            found = await lww.find_tombstone(conn, uid, lww.NOTE_ENTITIES, str(note_id))
            if client_updated_at_ms is not None and found is not None:  # a synced delete met its own result
                stamp = lww.clamp(client_updated_at_ms, now)
                await lww.write_tombstone(conn, uid, _entity(found[0]), [str(note_id)], stamp)
                return found[0]
            raise store.not_found()
        if head["kind"] == "page" and head["notebook_id"] is not None:
            await store.lock_notebook(conn, uid, head["notebook_id"])  # notebook before page rows
        cur = await store.lock_note(conn, uid, note_id)
        decision = lww.decide_write(client_updated_at_ms, cur["client_updated_at_ms"], None, now, True)
        if not decision.applied:
            return None
        row = await store.delete_note(conn, uid, note_id)
        await lww.write_tombstone(conn, uid, _entity(str(row["kind"])), [str(note_id)], decision.effective_ms)
        if row["notebook_id"] is not None:
            await _touch_notebook(conn, uid, row["notebook_id"])
        return str(row["kind"])

    db = _db(request)
    kind = await as_service(db, write)
    if kind is None:
        log.info("note_delete_kept")
        kept = (await read_note(db, user, note_id)).model_copy(update={"applied": False})
        return JSONResponse(kept.model_dump(mode="json", by_alias=True), status_code=200)
    log.info("note_deleted", kind=kind)
    forget(request, user, [str(note_id)])
    return Response(status_code=204)


def _clean_title(title: str | None) -> str | None:
    if title is None:
        return None
    t = " ".join(title.split())
    return t or None


def _pos(raw: Any) -> Any:
    from .models import parse_pos

    return parse_pos(raw)


async def _touch_notebook(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, notebook_id: Any) -> None:
    await conn.execute(
        "update public.notebooks set updated_at = now() where user_id = %s and id = %s", (uid, notebook_id)
    )


# ── Notebooks ──


async def read_notebook(db: Database, user: CurrentUser, notebook_id: uuid.UUID) -> NotebookDetail:
    async def run(conn: psycopg.AsyncConnection[Any]) -> NotebookDetail:
        book = await store.get_notebook(conn, user.user_id, notebook_id)
        if book is None:
            raise store.notebook_not_found()
        rows, _ = await store.list_notes(
            conn,
            user.user_id,
            NoteFilter(kind="page", notebook_id=notebook_id),
            limit=10_000,
            offset=0,
            include_body=True,
        )
        sources = await store.load_sources(conn, rows)
        return NotebookDetail.model_validate(
            {**book, "pages": [NoteOut.model_validate(store.shape_note(r, sources)) for r in rows]}
        )

    return await as_user(db, user, run)


@router.get(
    "/notebooks",
    operation_id="listNotebooks",
    summary="The caller's notebooks, most recently changed first, with page and note counts",
    response_model=NotebookList,
    responses=_ERRORS,
)
async def list_notebooks(request: Request, user: Annotated[CurrentUser, limited("notes_read")]) -> NotebookList:
    async def run(conn: psycopg.AsyncConnection[Any]) -> NotebookList:
        rows = await store.list_notebooks(conn, user.user_id)
        return NotebookList(items=[NotebookOut.model_validate(r) for r in rows])

    return await as_user(_db(request), user, run)


@router.post(
    "/notebooks",
    operation_id="createNotebook",
    summary="Create a notebook with its first page. A replay with the same id returns the stored notebook (200)",
    status_code=201,
    response_model=NotebookDetail,
    responses={
        **_ERRORS,
        200: {
            "model": NotebookDetail,
            "description": "Replayed: the stored notebook (applied true when a newer stamp updated it)",
        },
        **_conflict("notebook_id_conflict or notebook_limit"),
        422: {"model": ErrorResponse, "description": "Invalid request"},
    },
    dependencies=[IDEMPOTENT],
)
async def create_notebook(
    request: Request, body: NotebookCreate, user: Annotated[CurrentUser, limited("notes_write")]
) -> JSONResponse:
    db = _db(request)
    notebook_id = body.id or uuid.uuid4()
    title = _clean_title(body.title)
    if title is None:
        raise NotesError(422, "note_invalid")
    page = NewNote(
        id=body.first_page_id or uuid.uuid4(),
        kind="page",
        title=None,
        notebook_id=notebook_id,
        anchor_type=None,
        anchor_id=None,
        anchor_pos=None,
        highlight_color=None,
        body=None,
        body_text="",
        paper=None,
        canvas="paper",
        sort=0,
    )
    client_ms = body.client_updated_at_ms
    now = lww.server_now_ms()
    uid = user.user_id

    async def write(conn: psycopg.AsyncConnection[Any]) -> tuple[bool, bool]:
        tomb = await _guard_create(conn, uid, ("notebook",), notebook_id, client_ms, now)
        stamp = lww.decide_write(client_ms, None, tomb, now, False).effective_ms
        if not await store.insert_notebook(conn, uid, notebook_id, title, body.cover, body.paper, stamp):
            if client_ms is None:
                return False, False
            done = await lww.apply_lww(
                conn,
                table="notebooks",
                key={"user_id": uid, "id": notebook_id},
                values={"title": title, "cover": body.cover, "paper": body.paper},
                client_ms=client_ms,
                user_id=uid,
                entities=("notebook",),
                tombstone_key=str(notebook_id),
                returning=("id",),
                insert=False,
                now_ms=now,
            )
            return False, done.applied
        if tomb is not None:
            await lww.clear_tombstone(conn, uid, ("notebook",), str(notebook_id))
        if not await store.insert_note(conn, uid, replace(page, client_updated_at_ms=stamp)):
            raise NotesError(409, "note_id_conflict")
        return True, True

    written, applied = await as_service(db, write)
    if written:
        log.info("notebook_created", paper=body.paper)
    detail = (await read_notebook(db, user, notebook_id)).model_copy(update={"applied": applied})
    return JSONResponse(detail.model_dump(mode="json", by_alias=True), status_code=201 if written else 200)


@router.get(
    "/notebooks/{notebook_id}",
    operation_id="getNotebook",
    summary="One of the caller's notebooks with its pages in order",
    response_model=NotebookDetail,
    responses={**_ERRORS, **_NOTEBOOK_404},
)
async def get_notebook(
    request: Request,
    user: Annotated[CurrentUser, limited("notes_read")],
    notebook_id: Annotated[uuid.UUID, Path()],
) -> NotebookDetail:
    return await read_notebook(_db(request), user, notebook_id)


@router.patch(
    "/notebooks/{notebook_id}",
    operation_id="updateNotebook",
    summary="Rename a notebook or change its cover or paper",
    response_model=NotebookDetail,
    responses={
        **_ERRORS,
        **_NOTEBOOK_404,
        **_conflict("never otherwise"),
        422: {"model": ErrorResponse, "description": "Invalid request"},
    },
    dependencies=[IDEMPOTENT],
)
async def update_notebook(
    request: Request,
    body: NotebookPatch,
    user: Annotated[CurrentUser, limited("notes_write")],
    notebook_id: Annotated[uuid.UUID, Path()],
) -> NotebookDetail:
    db = _db(request)
    sent = body.model_fields_set
    values: dict[str, Any] = {}
    if "title" in sent:
        values["title"] = _clean_title(body.title)
        if values["title"] is None:
            raise NotesError(422, "note_invalid")
    if "cover" in sent:
        values["cover"] = body.cover
    if "paper" in sent:
        values["paper"] = body.paper

    uid = user.user_id

    async def write(conn: psycopg.AsyncConnection[Any]) -> bool:
        done = await lww.apply_lww(
            conn,
            table="notebooks",
            key={"user_id": uid, "id": notebook_id},
            values=values,
            client_ms=body.client_updated_at_ms,
            user_id=uid,
            entities=("notebook",),
            tombstone_key=str(notebook_id),
            returning=("id",),
            insert=False,
        )
        if done.row is None:
            if done.tombstone_ms is not None:
                raise SyncDeleted("notebook", str(notebook_id), done.tombstone_ms)
            raise store.notebook_not_found()
        return done.applied

    applied = await as_service(db, write)
    log.info("notebook_updated", fields=sorted(sent - {"client_updated_at_ms"}), applied=applied)
    return (await read_notebook(db, user, notebook_id)).model_copy(update={"applied": applied})


@router.delete(
    "/notebooks/{notebook_id}",
    operation_id="deleteNotebook",
    summary="Delete a notebook with its pages (their ink and index rows); notes filed in it stay, unfiled",
    status_code=204,
    responses={
        **_ERRORS,
        **_NOTEBOOK_404,
        200: {
            "model": NotebookDetail,
            "description": "Not deleted: the stored notebook is as new or newer (applied false)",
        },
    },
    dependencies=[IDEMPOTENT],
)
async def delete_notebook(
    request: Request,
    user: Annotated[CurrentUser, limited("notes_write")],
    notebook_id: Annotated[uuid.UUID, Path()],
    client_updated_at_ms: Annotated[ClientStamp | None, _STAMP_QUERY] = None,
) -> Response:
    uid = user.user_id
    now = lww.server_now_ms()

    async def write(conn: psycopg.AsyncConnection[Any]) -> list[str] | None:
        cur = await conn.execute(
            "select client_updated_at_ms from public.notebooks where user_id = %s and id = %s for no key update",
            (uid, notebook_id),
        )
        row = await cur.fetchone()
        if row is None:
            found = await lww.find_tombstone(conn, uid, ("notebook",), str(notebook_id))
            if client_updated_at_ms is not None and found is not None:  # a synced delete met its own result
                await lww.write_tombstone(
                    conn, uid, "notebook", [str(notebook_id)], lww.clamp(client_updated_at_ms, now)
                )
                return []
            raise store.notebook_not_found()
        decision = lww.decide_write(client_updated_at_ms, row["client_updated_at_ms"], None, now, True)
        if not decision.applied:
            return None
        pages = await store.delete_notebook(conn, uid, notebook_id)
        await lww.write_tombstone(conn, uid, "notebook", [str(notebook_id)], decision.effective_ms)
        await lww.write_tombstone(conn, uid, "page", pages, decision.effective_ms)
        return pages

    db = _db(request)
    pages = await as_service(db, write)
    if pages is None:
        log.info("notebook_delete_kept")
        kept = (await read_notebook(db, user, notebook_id)).model_copy(update={"applied": False})
        return JSONResponse(kept.model_dump(mode="json", by_alias=True), status_code=200)
    log.info("notebook_deleted", pages=len(pages))
    forget(request, user, pages)
    return Response(status_code=204)


@router.post(
    "/notebooks/{notebook_id}/pages",
    operation_id="createNotebookPage",
    summary="Add a page to a notebook (at `position`, default the end). A replay with the same id returns it (200)",
    status_code=201,
    response_model=NoteOut,
    responses={
        **_ERRORS,
        **_WRITE,
        **_ANCHOR,
        200: {
            "model": NoteOut,
            "description": "Replayed: the stored page (applied true when a newer stamp updated it)",
        },
        **_conflict("notebook_page_limit, note_id_conflict or note_limit"),
    },
    dependencies=[Depends(_body_within_limit), IDEMPOTENT],
)
async def create_page(
    request: Request,
    body: PageCreate,
    user: Annotated[CurrentUser, limited("notes_write")],
    notebook_id: Annotated[uuid.UUID, Path()],
) -> JSONResponse:
    db = _db(request)
    text = checked_body(body.body, body.canvas)
    await _anchor(db, user, body.anchor_type, body.anchor_id)
    page_id = body.id or uuid.uuid4()
    uid = user.user_id
    client_ms = body.client_updated_at_ms
    now = lww.server_now_ms()

    def new_page(sort: int, stamp: int | None) -> NewNote:
        return NewNote(
            id=page_id,
            kind="page",
            title=_clean_title(body.title),
            notebook_id=notebook_id,
            anchor_type=body.anchor_type,
            anchor_id=body.anchor_id,
            anchor_pos=None,
            highlight_color=None,
            body=body.body,
            body_text=text,
            paper=body.paper,
            canvas=body.canvas,
            sort=sort,
            client_updated_at_ms=stamp,
        )

    async def write(conn: psycopg.AsyncConnection[Any]) -> tuple[bool, bool]:
        await store.lock_notebook(conn, uid, notebook_id)
        tomb = await _guard_create(conn, uid, lww.NOTE_ENTITIES, page_id, client_ms, now)
        existing = await store.get_note(conn, uid, page_id, body=store.NO_BODY)
        if existing is not None:
            if existing["kind"] != "page" or existing["notebook_id"] != notebook_id:
                raise NotesError(409, "note_id_conflict")
            if client_ms is None:
                return False, False
            return False, await _lww_content(conn, uid, page_id, new_page(0, None), client_ms, now)
        sort = await store.open_slot(conn, uid, notebook_id, body.position)
        page = new_page(sort, lww.decide_write(client_ms, None, tomb, now, False).effective_ms)
        if not await store.insert_note(conn, uid, page):
            raise NotesError(409, "note_id_conflict")
        if tomb is not None:
            await lww.clear_tombstone(conn, uid, lww.NOTE_ENTITIES, str(page_id))
        await _touch_notebook(conn, uid, notebook_id)
        return True, True

    written, applied = await as_service(db, write)
    if written:
        log.info("notebook_page_created", canvas=body.canvas)
    if applied and (text or not written):
        reindex(request, user, [str(page_id)])
    out = (await read_note(db, user, page_id)).model_copy(update={"applied": applied})
    return _created(out, written)


@router.put(
    "/notebooks/{notebook_id}/pages/order",
    operation_id="reorderNotebookPages",
    summary="Put a notebook's pages in a new order (every page exactly once)",
    response_model=NotebookDetail,
    responses={
        **_ERRORS,
        **_NOTEBOOK_404,
        409: {"model": ErrorResponse, "description": "notebook_pages_changed: the list is not the current pages"},
        422: {"model": ErrorResponse, "description": "Invalid request"},
    },
    dependencies=[IDEMPOTENT],
)
async def reorder_pages(
    request: Request,
    body: PageOrder,
    user: Annotated[CurrentUser, limited("notes_write")],
    notebook_id: Annotated[uuid.UUID, Path()],
) -> NotebookDetail:
    db = _db(request)
    wanted = [str(i) for i in body.page_ids]

    async def write(conn: psycopg.AsyncConnection[Any]) -> None:
        await store.lock_notebook(conn, user.user_id, notebook_id)
        current = await store.page_ids(conn, user.user_id, notebook_id)
        if len(wanted) != len(current) or set(wanted) != set(current):
            raise NotesError(409, "notebook_pages_changed")
        await store.renumber(conn, user.user_id, notebook_id, wanted)
        await _touch_notebook(conn, user.user_id, notebook_id)

    await as_service(db, write)
    log.info("notebook_pages_reordered", pages=len(wanted))
    return await read_notebook(db, user, notebook_id)


# ── Wiring ──


async def _notes_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, NotesError)
    return error_response(exc.status, exc.code, exc.code)


def install_notes_routes(app: Any) -> None:
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
    app.add_exception_handler(NotesError, _notes_error)
    app.include_router(router)


__all__ = ["install_notes_routes", "router"]
