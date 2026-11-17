"""Ingest and Library API (segment 15).

    POST   /api/ingest/uploads                  a signed upload URL (Storage) + the new item's id
    POST   /api/ingest/uploads/batch            the same for many files dropped together (one batch)
    POST   /api/ingest/items/{id}/start         start the item's background run once the file is uploaded
    POST   /api/ingest/batches/{id}/start       start every uploaded item of a batch
    POST   /api/ingest/urls                     add a link (article, online PDF, YouTube)
    POST   /api/ingest/paste                    add pasted text
    GET    /api/ingest/items?status=&before=     the student's items, newest first
    GET    /api/ingest/items/{id}
    POST   /api/ingest/items/{id}/confirm       {placement: {option_id}} or {vault: vault|continue|discard}
    POST   /api/ingest/batches/{id}/confirm     one grouped confirm for a batch
    POST   /api/ingest/items/{id}/undo          delete exactly what the placement wrote
    POST   /api/ingest/items/{id}/retry         run a failed item again
    DELETE /api/ingest/items/{id}               the item, its files and anything it placed
    GET    /api/ingest/uploads/{id}/file        a short-lived signed URL of the original (PDF canvas)
    GET    /api/ingest/uploads/{id}/pages/{n}   one PDF page's text (the assistant's page citations)
    GET    /api/library                         items, private chapters, KB entries, storage used / remaining

Everything runs as the signed-in student; the `library` flag gates every route. Errors carry a stable `code`
(`ingest.error.<code>`), never a sentence. File names, URLs, pasted text and extracted text are never logged.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse, Response

from ..auth import CurrentUser, get_account_user
from ..auth_limits import RateLimiter
from ..chapters_media import configured_buckets
from ..config_api.schemas import FeatureFlags, IngestRules
from ..db import Database, DatabaseNotConfigured
from ..errors import ErrorResponse, error_response
from ..logs import get_logger
from . import errors as codes
from . import pipeline
from . import store as st
from .deps import deps_for
from .extract import extract_text, page_text
from .kinds import KIND_MIME, UPLOAD_MIMES
from .models import (
    LIST_LIMIT_MAX,
    BatchConfirm,
    ConfirmBody,
    IngestCounts,
    IngestFileOut,
    IngestGateOut,
    IngestItemList,
    IngestItemOut,
    IngestOptionOut,
    IngestPageTextOut,
    IngestPlacedOut,
    IngestStorageOut,
    LibraryChapter,
    LibraryKb,
    LibraryLimits,
    LibraryOut,
    PasteCreate,
    PlacementChoice,
    UploadBatchCreate,
    UploadBatchOut,
    UploadCreate,
    UploadTicket,
    UrlCreate,
)
from .placements import accepted_inputs
from .runner import get_runner
from .secure import MB
from .storage import SIGNED_READ_TTL_S, SIGNED_UPLOAD_TTL_S, StorageError, get_storage, upload_path

log = get_logger("naadi.ingest.api")
router = APIRouter(prefix="/api", tags=["ingest"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    403: {"model": ErrorResponse, "description": "library_disabled (the Library is off for this account)"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "ingest_unavailable"},
}
_404: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse, "description": "ingest_item_not_found"}}
_409: dict[int | str, dict[str, Any]] = {
    409: {"model": ErrorResponse, "description": "ingest_state_conflict (the item is not in a state for this)"}
}
_422: dict[int | str, dict[str, Any]] = {
    422: {
        "model": ErrorResponse,
        "description": "validation_error, file_too_large, unsupported_type, storage_full, daily_limit, ingest_disabled",
    }
}
# Content types a browser may report for a file Storage accepts under another name.
_MIME_ALIASES = {"application/x-zip-compressed": "application/zip", "text/rtf": "application/rtf"}
_KIND_OF_MIME = {v: k for k, v in KIND_MIME.items()}


class IngestError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


def default_limiters() -> dict[str, RateLimiter]:
    # Heavy writes (uploads, runs, placements) stay within S22's 30/min per student; batches are one request.
    return {"ingest_read": RateLimiter(600, 60.0), "ingest_write": RateLimiter(30, 60.0)}


def limited(bucket: str) -> Any:
    async def dep(request: Request, user: Annotated[CurrentUser, Depends(get_account_user)]) -> CurrentUser:
        limiters: dict[str, RateLimiter] = request.app.state.rate_limiters
        limiters[bucket].hit(str(user.user_id))
        flags = FeatureFlags.model_validate(await request.app.state.app_config.get("feature_flags")).root
        flag = flags.get("library")
        if flag is None or not flag.on_for(is_admin=user.is_admin, app_env=request.app.state.settings.app_env):
            raise IngestError(403, "library_disabled")
        return user

    return Depends(dep)


def _db(request: Request) -> Database:
    d: Database = request.app.state.db
    return d


def _deps(request: Request, user: CurrentUser) -> pipeline.Deps:
    from ..llm import get_gateway

    try:
        gateway = get_gateway()
    except RuntimeError:
        gateway = None
    return deps_for(_db(request), request.app.state.settings, is_admin=user.is_admin, gateway=gateway, search=None)


async def _rules(request: Request) -> IngestRules:
    return IngestRules.model_validate(await request.app.state.app_config.get("ingest"))


async def _guard[T](fn: Callable[[], Awaitable[T]]) -> T:
    try:
        result: T = await fn()
        return result
    except DatabaseNotConfigured as exc:
        raise IngestError(503, "ingest_unavailable") from exc
    except (psycopg.OperationalError, TimeoutError) as exc:
        log.warning("ingest_db_unavailable", error=type(exc).__name__)
        raise IngestError(503, "ingest_unavailable") from exc


# ── output ──


def item_out(row: dict[str, Any]) -> IngestItemOut:
    detail = row.get("stage_detail") or {}
    status = row["status"]
    stage = detail.get("stage")
    if status == "received":
        stage = "upload" if not row.get("run_id") and row.get("input_kind") not in ("link", "paste") else "receive"
    elif status == "extracting":
        stage = stage if stage in ("extract", "understand", "propose") else "extract"
    elif status == "needs_confirmation":
        stage = "vault" if row.get("sensitivity") == "personal" and stage == "vault" else "confirm"
    elif status == "placing":
        stage = "place"
    elif status == "vaulted":
        stage = "vaulted"
    elif status in ("ready", "failed"):
        stage = "done"
    gate = None
    if stage == "vault":
        g = detail.get("gate") or {}
        gate = IngestGateOut(signals=[str(s) for s in g.get("signals") or []], doc_type=g.get("doc_type"))
    options: list[IngestOptionOut] = []
    if stage == "confirm":
        for o in (row.get("proposed_placement") or {}).get("options") or []:
            options.append(
                IngestOptionOut(
                    option_id=o["option_id"],
                    kind=o["kind"],
                    recommended=bool(o.get("recommended")),
                    preview=dict(o.get("preview") or {}),
                )
            )
    records = row.get("placements") or []
    placed = None
    if records:
        kind = (row.get("confirmed_placement") or {}).get("kind") or records[0].get("kind")
        chapter = next((r["id"] for r in records if r.get("table") == "chapters"), None)
        placed = IngestPlacedOut(kind=str(kind), chapter_id=chapter, rows=len(records), can_undo=True)
    name = row.get("original_name") or row.get("kb_title")
    if not name and row.get("kb_url"):
        from urllib.parse import urlparse

        name = urlparse(row["kb_url"]).hostname
    counts = detail.get("counts")
    bundle = detail.get("bundle") or {}
    return IngestItemOut(
        id=row["id"],
        status=status,
        stage=stage or "receive",
        error=row.get("error"),
        progress=int(row.get("progress") or 0),
        input_kind=row.get("input_kind"),
        name=name,
        url=row.get("kb_url"),
        upload_id=row.get("upload_id"),
        bytes=row.get("bytes"),
        pages=row.get("pages"),
        batch_id=row.get("batch_id"),
        duplicate_of=row.get("duplicate_of"),
        duplicate=detail.get("duplicate"),
        chapter_id=(placed.chapter_id if placed else None)
        or detail.get("chapter_id")
        or row.get("processed_chapter_id"),
        gate=gate,
        options=options,
        placed=placed,
        counts=IngestCounts.model_validate(counts) if isinstance(counts, dict) else None,
        bundle_files=bundle.get("files") if bundle else None,
        retryable=status == "failed" and row.get("error") not in (codes.DISCARDED, *_FINAL),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# Rejections a retry cannot change (the bytes are what they are).
_FINAL = (
    codes.EXECUTABLE,
    codes.MACRO_OFFICE,
    codes.LEGACY_OFFICE,
    codes.ENCRYPTED_PDF,
    codes.ENCRYPTED_ARCHIVE,
    codes.ZIP_BOMB,
    codes.ZIP_TOO_MANY,
    codes.ZIP_TOO_LARGE,
    codes.ZIP_TOO_DEEP,
    codes.ZIP_UNSAFE_PATH,
    codes.IMAGE_TOO_LARGE,
    codes.SVG_UNSUPPORTED,
    codes.FILE_TOO_LARGE,
    codes.UNSUPPORTED_TYPE,
    codes.EMPTY_FILE,
    codes.TOO_MANY_PAGES,
    codes.AUDIO_TOO_LONG,
    codes.ANKI_UNSUPPORTED,
    codes.VAULT_TOO_LARGE,
    codes.VAULT_TYPE,
)


async def _read_item(db: Database, user: CurrentUser, item_id: uuid.UUID) -> dict[str, Any]:
    async def run() -> dict[str, Any] | None:
        async with st.service_tx(db) as conn:
            return await st.get_item(conn, item_id, user.user_id)

    row = await _guard(run)
    if row is None:
        raise IngestError(404, "ingest_item_not_found")
    return row


# ── create ──


async def _quota(
    conn: psycopg.AsyncConnection[Any],
    request: Request,
    user: CurrentUser,
    rules: IngestRules,
    adding: int,
    *,
    adding_items: int = 1,
) -> None:
    if not rules.enabled:
        raise IngestError(422, "ingest_disabled")
    if await st.items_today(conn, user.user_id) + adding_items > rules.items_per_user_daily:
        raise IngestError(422, codes.DAILY_LIMIT)
    b = configured_buckets(request.app.state.settings)
    used = await st.storage_used(conn, user.user_id, [b.user_uploads])
    if used + adding > rules.user_storage_mb * MB:
        raise IngestError(422, codes.STORAGE_FULL)


def _kind_of(stored_mime: str) -> str:
    kind = _KIND_OF_MIME.get(stored_mime) or stored_mime.split("/", 1)[0]
    return kind if kind in ("image", "audio", "video") or kind in KIND_MIME else "txt"


async def _unreserve(request: Request, user: CurrentUser, planned: list[Any]) -> None:
    """Drop the rows of a reservation whose upload URLs could not be signed (no ticket ever left the server)."""
    items = [p[1] for p in planned]
    upload_ids = [p[2] for p in planned]

    async def run() -> None:
        async with st.service_tx(_db(request)) as conn:
            await conn.execute(
                "delete from public.ingest_items where user_id = %s and id = any(%s)", (user.user_id, items)
            )
            await conn.execute(
                "delete from public.uploads where user_id = %s and id = any(%s)", (user.user_id, upload_ids)
            )

    try:
        await _guard(run)
    except IngestError:
        log.warning("ingest.unreserve_failed", items=len(items))


async def _reserve(
    request: Request, user: CurrentUser, bodies: list[UploadCreate], batch: uuid.UUID | None
) -> list[UploadTicket]:
    rules = await _rules(request)
    planned: list[tuple[UploadCreate, uuid.UUID, uuid.UUID, str, str, str]] = []
    for body in bodies:
        mime = body.content_type.split(";", 1)[0].strip().lower()
        if mime not in UPLOAD_MIMES:
            raise IngestError(422, codes.UNSUPPORTED_TYPE)
        if body.size > rules.max_file_mb * MB:
            raise IngestError(422, codes.FILE_TOO_LARGE)
        item_id = uuid.uuid4()
        stored = _MIME_ALIASES.get(mime, mime)
        planned.append((body, item_id, uuid.uuid4(), upload_path(user.user_id, item_id), stored, _kind_of(stored)))
    settings = request.app.state.settings
    buckets = configured_buckets(settings)

    async def run() -> None:
        async with st.service_tx(_db(request)) as conn:
            await _quota(conn, request, user, rules, sum(b.size for b in bodies), adding_items=len(bodies))
            for body, item_id, upload_id, path, stored, kind in planned:
                await conn.execute(
                    "insert into public.uploads (id, user_id, kind, storage_path, original_name, bytes, mime, status)"
                    " values (%s, %s, %s, %s, %s, %s, %s, 'uploaded')",
                    (upload_id, user.user_id, kind, path, body.name.strip()[:255], body.size, stored),
                )
                await conn.execute(
                    "insert into public.ingest_items (id, user_id, upload_id, input_kind, batch_id, bytes, status)"
                    " values (%s, %s, %s, %s, %s, %s, 'received')",
                    (item_id, user.user_id, upload_id, kind, batch or body.batch_id, body.size),
                )

    await _guard(run)
    tickets = []
    storage = get_storage(settings)
    for _body, item_id, upload_id, path, _stored, kind in planned:
        try:
            t = await storage.signed_upload(buckets.user_uploads, path)
        except Exception as exc:
            log.warning("ingest.sign_failed", error=type(exc).__name__)
            await _unreserve(request, user, planned)
            raise IngestError(503, "ingest_unavailable") from exc
        tickets.append(
            UploadTicket(
                item_id=item_id,
                upload_id=upload_id,
                bucket=buckets.user_uploads,
                path=path,
                token=t["token"],
                expires_in=SIGNED_UPLOAD_TTL_S,
            )
        )
        log.info("ingest.upload_reserved", item_id=str(item_id), kind=kind)
    return tickets


@router.post(
    "/ingest/uploads/batch",
    operation_id="createIngestUploadBatch",
    summary="Reserve items for many files dropped together and get their signed upload URLs",
    response_model=UploadBatchOut,
    status_code=201,
    responses={**_ERRORS, **_422},
)
async def create_upload_batch(
    request: Request, body: UploadBatchCreate, user: Annotated[CurrentUser, limited("ingest_write")]
) -> UploadBatchOut:
    batch = uuid.uuid4()
    return UploadBatchOut(batch_id=batch, tickets=await _reserve(request, user, body.files, batch))


@router.post(
    "/ingest/uploads",
    operation_id="createIngestUpload",
    summary="Reserve an item and get a signed upload URL for user-uploads/{user}/{item}/original",
    response_model=UploadTicket,
    status_code=201,
    responses={**_ERRORS, **_422},
)
async def create_upload(
    request: Request, body: UploadCreate, user: Annotated[CurrentUser, limited("ingest_write")]
) -> UploadTicket:
    return (await _reserve(request, user, [body], None))[0]


async def _start(request: Request, user: CurrentUser, item_id: uuid.UUID, payload: dict[str, Any]) -> None:
    runner = get_runner(request.app.state.settings)
    try:
        await runner.start(user.user_id, user.is_admin, item_id, payload, _db(request))
    except Exception as exc:
        log.warning("ingest.start_failed", item_id=str(item_id), error=type(exc).__name__)
        raise IngestError(503, "ingest_unavailable") from exc


@router.post(
    "/ingest/items/{item_id}/start",
    operation_id="startIngestItem",
    summary="Start processing an uploaded file (its background run)",
    response_model=IngestItemOut,
    status_code=202,
    responses={**_ERRORS, **_404, **_409, **_422},
)
async def start_item(
    request: Request, item_id: uuid.UUID, user: Annotated[CurrentUser, limited("ingest_write")]
) -> IngestItemOut:
    row = await _read_item(_db(request), user, item_id)
    if row["status"] != "received" or row["run_id"] or row["input_kind"] in ("link", "paste"):
        raise IngestError(409, "ingest_state_conflict")
    rules = await _rules(request)
    size = await get_storage(request.app.state.settings).size(
        configured_buckets(request.app.state.settings).user_uploads, row["storage_path"] or ""
    )
    if size is None:
        raise IngestError(422, codes.UPLOAD_MISSING)
    if size > rules.max_file_mb * MB:
        raise IngestError(422, codes.FILE_TOO_LARGE)
    await _start(request, user, item_id, {"item_id": str(item_id), "mode": "start"})
    return item_out(await _read_item(_db(request), user, item_id))


@router.post(
    "/ingest/batches/{batch_id}/start",
    operation_id="startIngestBatch",
    summary="Start every uploaded item of a batch (files that never arrived are skipped)",
    response_model=IngestItemList,
    status_code=202,
    responses={**_ERRORS, **_404},
)
async def start_batch(
    request: Request, batch_id: uuid.UUID, user: Annotated[CurrentUser, limited("ingest_write")]
) -> IngestItemList:
    db = _db(request)

    async def load() -> list[dict[str, Any]]:
        async with st.service_tx(db) as conn:
            return await st.rows(
                conn,
                f"select {st.ITEM_COLUMNS} from {st.ITEM_FROM} where i.user_id = %s and i.batch_id = %s"  # noqa: S608
                " order by i.created_at",
                (user.user_id, batch_id),
            )

    rows = await _guard(load)
    if not rows:
        raise IngestError(404, "ingest_item_not_found")
    rules = await _rules(request)
    settings = request.app.state.settings
    storage, bucket = get_storage(settings), configured_buckets(settings).user_uploads
    for row in rows:
        if row["status"] != "received" or row["run_id"] or not row["storage_path"]:
            continue
        size = await storage.size(bucket, row["storage_path"])
        if size is None or size > rules.max_file_mb * MB:
            continue
        await _start(request, user, row["id"], {"item_id": str(row["id"]), "mode": "start"})
    return IngestItemList(items=[item_out(r) for r in await _guard(load)], next_before=None)


@router.post(
    "/ingest/urls",
    operation_id="createIngestUrl",
    summary="Add a link (article, online PDF, YouTube) to the knowledge base",
    response_model=IngestItemOut,
    status_code=202,
    responses={**_ERRORS, **_422},
)
async def create_url(
    request: Request, body: UrlCreate, user: Annotated[CurrentUser, limited("ingest_write")]
) -> IngestItemOut:
    rules = await _rules(request)
    item_id, kb_id = uuid.uuid4(), uuid.uuid4()

    async def run() -> None:
        async with st.service_tx(_db(request)) as conn:
            await _quota(conn, request, user, rules, 0)
            await conn.execute(
                "insert into public.ingest_items (id, user_id, input_kind, batch_id, status)"
                " values (%s, %s, 'link', %s, 'received')",
                (item_id, user.user_id, body.batch_id),
            )
            await conn.execute(
                "insert into public.kb_sources (id, user_id, kind, url, status, ingest_item_id)"
                " values (%s, %s, 'url', %s, 'processing', %s)",
                (kb_id, user.user_id, body.url, item_id),
            )
            await conn.execute("update public.ingest_items set kb_source_id = %s where id = %s", (kb_id, item_id))

    await _guard(run)
    await _start(request, user, item_id, {"item_id": str(item_id), "mode": "start"})
    return item_out(await _read_item(_db(request), user, item_id))


@router.post(
    "/ingest/paste",
    operation_id="createIngestPaste",
    summary="Add pasted text",
    response_model=IngestItemOut,
    status_code=202,
    responses={**_ERRORS, **_422},
)
async def create_paste(
    request: Request, body: PasteCreate, user: Annotated[CurrentUser, limited("ingest_write")]
) -> IngestItemOut:
    rules = await _rules(request)
    if len(body.text) > rules.max_paste_chars:
        raise IngestError(422, codes.FILE_TOO_LARGE)
    text = body.text.strip()
    if not text:
        raise IngestError(422, codes.NO_TEXT)
    title = (body.title or "").strip()[:200]
    doc = extract_text(text, "paste", title)
    item_id, kb_id = uuid.uuid4(), uuid.uuid4()

    async def run() -> None:
        async with st.service_tx(_db(request)) as conn:
            await _quota(conn, request, user, rules, 0)
            await conn.execute(
                "insert into public.ingest_items (id, user_id, input_kind, status)"
                " values (%s, %s, 'paste', 'received')",
                (item_id, user.user_id),
            )
            await conn.execute(
                "insert into public.kb_sources (id, user_id, kind, title, status, ingest_item_id)"
                " values (%s, %s, 'paste', %s, 'processing', %s)",
                (kb_id, user.user_id, title or None, item_id),
            )
            await conn.execute("update public.ingest_items set kb_source_id = %s where id = %s", (kb_id, item_id))
            await st.save_extract(conn, item_id, user.user_id, doc)

    await _guard(run)
    await _start(request, user, item_id, {"item_id": str(item_id), "mode": "start"})
    return item_out(await _read_item(_db(request), user, item_id))


# ── read ──


async def _list(
    db: Database, user: CurrentUser, status: str | None, before: datetime | None, limit: int
) -> list[dict[str, Any]]:
    async def run() -> list[dict[str, Any]]:
        where = ["i.user_id = %(uid)s"]
        params: dict[str, Any] = {"uid": user.user_id, "limit": limit}
        if status:
            where.append("i.status = %(status)s")
            params["status"] = status
        if before:
            where.append("i.created_at < %(before)s")
            params["before"] = before
        async with st.service_tx(db) as conn:
            return await st.rows(
                conn,
                f"select {st.ITEM_COLUMNS} from {st.ITEM_FROM} where {' and '.join(where)}"  # noqa: S608 - constants
                " order by i.created_at desc, i.id desc limit %(limit)s",
                params,
            )

    return await _guard(run)


@router.get(
    "/ingest/items",
    operation_id="listIngestItems",
    summary="The student's ingest items, newest first",
    response_model=IngestItemList,
    responses=_ERRORS,
)
async def list_items(
    request: Request,
    user: Annotated[CurrentUser, limited("ingest_read")],
    status: Annotated[
        str | None, Query(pattern=r"^(received|extracting|needs_confirmation|placing|ready|failed|vaulted)$")
    ] = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=LIST_LIMIT_MAX)] = 50,
) -> IngestItemList:
    rows = await _list(_db(request), user, status, before, limit)
    items = [item_out(r) for r in rows]
    return IngestItemList(items=items, next_before=items[-1].created_at if len(items) == limit else None)


@router.get(
    "/ingest/items/{item_id}",
    operation_id="getIngestItem",
    summary="One ingest item",
    response_model=IngestItemOut,
    responses={**_ERRORS, **_404},
)
async def get_item(
    request: Request, item_id: uuid.UUID, user: Annotated[CurrentUser, limited("ingest_read")]
) -> IngestItemOut:
    return item_out(await _read_item(_db(request), user, item_id))


# ── confirm / undo / retry / delete ──


async def _resume(request: Request, user: CurrentUser, row: dict[str, Any], value: dict[str, Any]) -> None:
    runner = get_runner(request.app.state.settings)
    item_id = row["id"]
    detail = row["stage_detail"] or {}
    option = value.get("option_id")
    fresh = option is not None and (detail.get("undone") or not row["thread_id"])
    try:
        if fresh:
            await runner.start(
                user.user_id,
                user.is_admin,
                item_id,
                {"item_id": str(item_id), "mode": "place", "option_id": option},
                _db(request),
            )
            return
        try:
            await runner.resume(user.user_id, user.is_admin, item_id, str(row["thread_id"]), value, _db(request))
        except Exception:
            if option is None:
                raise
            # The waiting run is gone (expired thread, restarted process): place from the stored proposal.
            await runner.start(
                user.user_id,
                user.is_admin,
                item_id,
                {"item_id": str(item_id), "mode": "place", "option_id": option},
                _db(request),
            )
    except Exception as exc:
        log.warning("ingest.resume_failed", item_id=str(item_id), error=type(exc).__name__)
        raise IngestError(503, "ingest_unavailable") from exc


async def confirm_one(request: Request, user: CurrentUser, item_id: uuid.UUID, body: ConfirmBody) -> IngestItemOut:
    if (body.placement is None) == (body.vault is None):
        raise IngestError(422, "validation_error")
    db = _db(request)
    row = await _read_item(db, user, item_id)
    if row["status"] != "needs_confirmation":
        raise IngestError(409, "ingest_state_conflict")
    waiting_vault = row["sensitivity"] == "personal" and (row["stage_detail"] or {}).get("stage") == "vault"
    if body.vault is not None:
        if not waiting_vault:
            raise IngestError(409, "ingest_state_conflict")
        await _resume(request, user, row, {"decision": body.vault})
    else:
        assert body.placement is not None
        if waiting_vault:
            raise IngestError(409, "ingest_state_conflict")
        options = {o["option_id"] for o in (row["proposed_placement"] or {}).get("options") or []}
        if body.placement.option_id not in options:
            raise IngestError(422, "ingest_option_unknown")
        await _resume(request, user, row, {"option_id": body.placement.option_id})
    log.info("ingest.confirmed", item_id=str(item_id), vault=body.vault is not None)
    return item_out(await _read_item(db, user, item_id))


@router.post(
    "/ingest/items/{item_id}/confirm",
    operation_id="confirmIngestItem",
    summary="Answer the item's confirm card (a placement) or its Vault prompt",
    response_model=IngestItemOut,
    status_code=202,
    responses={**_ERRORS, **_404, **_409, **_422},
)
async def confirm_item(
    request: Request, item_id: uuid.UUID, body: ConfirmBody, user: Annotated[CurrentUser, limited("ingest_write")]
) -> IngestItemOut:
    return await confirm_one(request, user, item_id, body)


@router.post(
    "/ingest/batches/{batch_id}/confirm",
    operation_id="confirmIngestBatch",
    summary="One grouped confirm for the items of a batch",
    response_model=IngestItemList,
    status_code=202,
    responses={**_ERRORS, **_404, **_409, **_422},
)
async def confirm_batch(
    request: Request, batch_id: uuid.UUID, body: BatchConfirm, user: Annotated[CurrentUser, limited("ingest_write")]
) -> IngestItemList:
    out = []
    for choice in body.items:
        row = await _read_item(_db(request), user, choice.item_id)
        if row["batch_id"] != batch_id:
            raise IngestError(404, "ingest_item_not_found")
    for choice in body.items:
        out.append(
            await confirm_one(
                request, user, choice.item_id, ConfirmBody(placement=PlacementChoice(option_id=choice.option_id))
            )
        )
    return IngestItemList(items=out, next_before=None)


@router.post(
    "/ingest/items/{item_id}/undo",
    operation_id="undoIngestPlacement",
    summary="Undo the placement: delete exactly what it created; the item waits for a new choice",
    response_model=IngestItemOut,
    responses={**_ERRORS, **_404, **_409},
)
async def undo_item(
    request: Request, item_id: uuid.UUID, user: Annotated[CurrentUser, limited("ingest_write")]
) -> IngestItemOut:
    db = _db(request)
    row = await _read_item(db, user, item_id)
    if row["status"] != "ready" or not row["placements"]:
        raise IngestError(409, "ingest_state_conflict")
    await _guard(lambda: pipeline.undo(_deps(request, user), item_id, user.user_id))
    return item_out(await _read_item(db, user, item_id))


@router.post(
    "/ingest/items/{item_id}/retry",
    operation_id="retryIngestItem",
    summary="Run a failed item again from the start",
    response_model=IngestItemOut,
    status_code=202,
    responses={**_ERRORS, **_404, **_409},
)
async def retry_item(
    request: Request, item_id: uuid.UUID, user: Annotated[CurrentUser, limited("ingest_write")]
) -> IngestItemOut:
    db = _db(request)
    row = await _read_item(db, user, item_id)
    if not item_out(row).retryable:
        raise IngestError(409, "ingest_state_conflict")

    async def reset() -> None:
        async with st.service_tx(db) as conn:
            await conn.execute(
                "update public.ingest_items set status = 'received', error = null, progress = 0, run_id = null,"
                " thread_id = null, proposed_placement = null, confirmed_placement = null, sensitivity = 'normal',"
                " stage_detail = stage_detail - 'stage' - 'gate' - 'failed' where id = %s and user_id = %s",
                (item_id, user.user_id),
            )

    await _guard(reset)
    await _start(request, user, item_id, {"item_id": str(item_id), "mode": "start"})
    return item_out(await _read_item(db, user, item_id))


@router.delete(
    "/ingest/items/{item_id}",
    operation_id="deleteIngestItem",
    summary="Delete an item with its files and anything it placed",
    status_code=204,
    responses={**_ERRORS, **_404, **_409},
)
async def delete_item(
    request: Request, item_id: uuid.UUID, user: Annotated[CurrentUser, limited("ingest_write")]
) -> Response:
    db = _db(request)
    row = await _read_item(db, user, item_id)
    if row["status"] == "placing":
        raise IngestError(409, "ingest_state_conflict")
    deps = _deps(request, user)
    if row["placements"]:
        await _guard(lambda: pipeline.undo(deps, item_id, user.user_id))
    buckets = configured_buckets(request.app.state.settings)
    objects = await deps.storage.list_prefix(buckets.user_uploads, f"{user.user_id}/{item_id}")
    try:
        await deps.storage.remove(buckets.user_uploads, objects)
    except StorageError:
        log.warning("ingest.delete_objects_failed", item_id=str(item_id))

    async def drop() -> None:
        async with st.service_tx(db) as conn:
            if row["upload_id"]:
                await conn.execute(
                    "delete from public.uploads where id = %s and user_id = %s", (row["upload_id"], user.user_id)
                )
            if row["kb_source_id"]:
                await conn.execute(
                    "delete from public.kb_sources where id = %s and user_id = %s", (row["kb_source_id"], user.user_id)
                )
            await conn.execute(
                "delete from public.ingest_items where id = %s and user_id = %s", (item_id, user.user_id)
            )

    await _guard(drop)
    log.info("ingest.deleted", item_id=str(item_id))
    return Response(status_code=204)


# ── files ──


async def _upload_row(db: Database, user: CurrentUser, upload_id: uuid.UUID) -> dict[str, Any]:
    async def run() -> dict[str, Any] | None:
        async with st.service_tx(db) as conn:
            return await st.one(
                conn,
                "select u.id, u.kind, u.original_name, u.storage_path, u.mime, u.status, i.pages"
                " from public.uploads u left join public.ingest_items i on i.upload_id = u.id"
                " where u.id = %s and u.user_id = %s order by i.created_at limit 1",
                (upload_id, user.user_id),
            )

    row = await _guard(run)
    if row is None or not row["storage_path"] or row["status"] == "vaulted":
        raise IngestError(404, "ingest_item_not_found")
    return row


@router.get(
    "/ingest/uploads/{upload_id}/file",
    operation_id="getIngestFile",
    summary="A short-lived signed URL of the student's original file",
    response_model=IngestFileOut,
    responses={**_ERRORS, **_404},
)
async def get_file(
    request: Request, upload_id: uuid.UUID, user: Annotated[CurrentUser, limited("ingest_read")]
) -> IngestFileOut:
    row = await _upload_row(_db(request), user, upload_id)
    settings = request.app.state.settings
    bucket = configured_buckets(settings).user_uploads
    try:
        url = await get_storage(settings).signed_url(bucket, row["storage_path"], SIGNED_READ_TTL_S)
    except Exception as exc:
        raise IngestError(503, "ingest_unavailable") from exc
    return IngestFileOut(
        upload_id=upload_id,
        kind=row["kind"],
        name=row["original_name"],
        url=url,
        expires_in=SIGNED_READ_TTL_S,
        pages=row["pages"],
        mime=row["mime"],
    )


async def read_page_text(
    db: Database, settings: Any, user_id: uuid.UUID, upload_id: uuid.UUID, page: int
) -> IngestPageTextOut | None:
    """One PDF page's text (sandboxed), for the Library API and the tutor's `get_page` (pdf_page)."""
    async with st.service_tx(db) as conn:
        row = await st.one(
            conn,
            "select u.kind, u.storage_path, u.status, i.id as item_id from public.uploads u"
            " left join public.ingest_items i on i.upload_id = u.id"
            " where u.id = %s and u.user_id = %s and u.kind = 'pdf' order by i.created_at limit 1",
            (upload_id, user_id),
        )
        doc = await st.load_extract(conn, row["item_id"], user_id) if row and row["item_id"] else None
    if row is None or not row["storage_path"] or row["status"] == "vaulted":
        return None
    if doc is not None and doc.pages:
        hit = next((p for p in doc.pages if p.number == page), None)
        if hit is not None:
            return IngestPageTextOut(
                upload_id=upload_id, page=page, pages=doc.page_count or len(doc.pages), text=hit.text
            )
    bucket = configured_buckets(settings).user_uploads
    data = await get_storage(settings).download(bucket, row["storage_path"], 50 * MB)
    text, pages = await page_text(data, page)
    if not 1 <= page <= pages:
        return None
    return IngestPageTextOut(upload_id=upload_id, page=page, pages=pages, text=text)


@router.get(
    "/ingest/uploads/{upload_id}/pages/{page}",
    operation_id="getIngestPageText",
    summary="One page's text of the student's PDF",
    response_model=IngestPageTextOut,
    responses={**_ERRORS, **_404},
)
async def get_page_text(
    request: Request,
    upload_id: uuid.UUID,
    page: Annotated[int, Path(ge=1, le=10_000)],
    user: Annotated[CurrentUser, limited("ingest_read")],
) -> IngestPageTextOut:
    out = await _guard(lambda: read_page_text(_db(request), request.app.state.settings, user.user_id, upload_id, page))
    if out is None:
        raise IngestError(404, "ingest_item_not_found")
    return out


# ── library ──


@router.get(
    "/library",
    operation_id="getLibrary",
    summary="The student's Library: files, links and pastes, private chapters, KB entries and storage use",
    response_model=LibraryOut,
    responses=_ERRORS,
)
async def get_library(request: Request, user: Annotated[CurrentUser, limited("ingest_read")]) -> LibraryOut:
    rules = await _rules(request)
    settings = request.app.state.settings
    db = _db(request)
    buckets = configured_buckets(settings)

    async def run() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
        async with st.service_tx(db) as conn:
            items = await st.rows(
                conn,
                f"select {st.ITEM_COLUMNS} from {st.ITEM_FROM} where i.user_id = %s"  # noqa: S608 - constants only
                " order by i.created_at desc, i.id desc limit 200",
                (user.user_id,),
            )
            chapters = await st.rows(
                conn,
                "select c.id, c.kind, c.title, c.updated_at, count(s.id)::int as sections from public.chapters c"
                " left join public.chapter_sections s on s.chapter_id = c.id where c.owner_user_id = %s"
                " and c.kind in ('user_upload','user_kb') group by c.id order by c.updated_at desc limit 500",
                (user.user_id,),
            )
            kb = await st.rows(
                conn,
                "select id, kind, title, url, status, ingest_item_id, created_at from public.kb_sources"
                " where user_id = %s order by created_at desc limit 500",
                (user.user_id,),
            )
            used = await st.storage_used(conn, user.user_id, [buckets.user_uploads])
        return items, chapters, kb, used

    items, chapters, kb, used = await _guard(run)
    item_rows = {r["id"]: r for r in items}
    chapter_item: dict[str, uuid.UUID] = {}
    for r in items:
        for rec in r.get("placements") or []:
            if rec.get("table") == "chapters":
                chapter_item[str(rec["id"])] = r["id"]
    limit = rules.user_storage_mb * MB
    flags = FeatureFlags.model_validate(await request.app.state.app_config.get("feature_flags")).root
    on = {k: f.on_for(is_admin=user.is_admin, app_env=settings.app_env) for k, f in flags.items()}
    return LibraryOut(
        items=[item_out(r) for r in items],
        chapters=[
            LibraryChapter(
                id=c["id"],
                kind=c["kind"],
                title=c["title"],
                sections=c["sections"],
                item_id=chapter_item.get(c["id"]),
                updated_at=c["updated_at"],
            )
            for c in chapters
        ],
        kb=[
            LibraryKb(
                id=k["id"],
                kind=k["kind"],
                title=k["title"],
                url=k["url"],
                status=k["status"],
                chapter_id=next(
                    (
                        str(rec["id"])
                        for rec in (item_rows.get(k["ingest_item_id"]) or {}).get("placements") or []
                        if rec.get("table") == "chapters"
                    ),
                    None,
                ),
                item_id=k["ingest_item_id"],
                created_at=k["created_at"],
            )
            for k in kb
        ],
        storage=IngestStorageOut(used_bytes=used, limit_bytes=limit, remaining_bytes=max(0, limit - used)),
        accepts=accepted_inputs(on),
        limits=LibraryLimits(
            max_file_mb=rules.max_file_mb,
            max_paste_chars=rules.max_paste_chars,
            enabled=rules.enabled,
            content_types=sorted(UPLOAD_MIMES),
        ),
    )


# ── wiring ──


async def _ingest_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, IngestError)
    return error_response(exc.status, exc.code, exc.code)


def install_ingest_routes(app: Any) -> None:
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
    app.add_exception_handler(IngestError, _ingest_error)
    app.include_router(router)


__all__ = ["IngestError", "install_ingest_routes", "item_out", "read_page_text", "router"]
