"""`/api/anki`: import and export `.apkg`, deck list, delete an imported deck.

Everything runs as the signed-in student and only ever touches their own rows. Responses carry counts and stable
error codes (`ui_copy` `anki.error.<code>` words them). The upload is the raw request body (no multipart), streamed
to a temp file with a hard size cap, then read by `importer`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from ..auth import CurrentUser, get_account_user
from ..auth_limits import RateLimiter
from ..db import Database, DatabaseNotConfigured
from ..errors import ErrorResponse, error_response
from ..logs import get_logger
from . import exporter, importer
from .apkg import AnkiLimits
from .db import AnkiError, service_tx
from .images import ImageStore
from .models import DeckCount, DecksOut, DeletedOut, ImportReport
from .storage import SupabaseImageStore

log = get_logger("naadi.anki")
router = APIRouter(prefix="/api/anki", tags=["anki"])

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
DECK_MAX = 60

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    404: {"model": ErrorResponse, "description": "Anki import is switched off (code anki_unavailable)"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Database or storage unavailable (code anki_unavailable)"},
}


def default_limiters() -> dict[str, RateLimiter]:
    return {
        "anki_read": RateLimiter(120, 60.0),
        "anki_import": RateLimiter(6, 600.0),
        "anki_export": RateLimiter(10, 600.0),
    }


def limited(bucket: str) -> Any:
    async def dep(request: Request, user: Annotated[CurrentUser, Depends(get_account_user)]) -> CurrentUser:
        limiters: dict[str, RateLimiter] = request.app.state.rate_limiters
        limiters[bucket].hit(str(user.user_id))
        flag = await request.app.state.app_config.feature_enabled(
            "anki", is_admin=user.is_admin, app_env=request.app.state.settings.app_env
        )
        if not flag:
            raise AnkiError(404, "anki_unavailable")
        return user

    return Depends(dep)


def _db(request: Request) -> Database:
    d: Database = request.app.state.db
    return d


def _store(request: Request) -> ImageStore:
    s = getattr(request.app.state, "anki_image_store", None)
    if s is None:
        s = SupabaseImageStore(request.app.state.settings)
        request.app.state.anki_image_store = s
    return s


async def _guard[T](coro: Any) -> T:
    try:
        return await coro  # type: ignore[no-any-return]
    except DatabaseNotConfigured as exc:
        raise AnkiError(503, "anki_unavailable") from exc


def _deck(raw: str | None) -> str | None:
    if raw is None:
        return None
    t = " ".join(raw.split())
    if not t or len(t) > DECK_MAX:
        raise AnkiError(422, "anki_bad_deck")
    return t


@router.get(
    "/decks",
    operation_id="getAnkiDecks",
    summary="Your decks with card counts (the picker for Export deck)",
    response_model=DecksOut,
    responses={**_ERRORS},
)
async def get_decks(request: Request, user: Annotated[CurrentUser, limited("anki_read")]) -> DecksOut:
    async def run() -> DecksOut:
        async with service_tx(_db(request), timeout_ms=8000) as conn:
            cur = await conn.execute(
                "select deck, count(*)::int as cards, count(*) filter (where origin = 'anki_import')::int as imported"
                " from public.flashcards where owner_user_id = %s and origin = any(%s) group by deck order by deck",
                (user.user_id, list(exporter.EXPORT_ORIGINS)),
            )
            rows = await cur.fetchall()
        decks = [DeckCount(deck=r["deck"], cards=r["cards"], imported=r["imported"]) for r in rows]
        return DecksOut(decks=decks, total_cards=sum(d.cards for d in decks))

    return await _guard(run())


@router.post(
    "/import",
    operation_id="importAnkiDeck",
    summary="Import an .apkg (the raw request body) into your own flashcards; cards start new, a repeat is a no-op",
    response_model=ImportReport,
    responses={
        **_ERRORS,
        413: {"model": ErrorResponse, "description": "Larger than 100 MB (code anki_too_large) or over the card cap"},
        422: {"model": ErrorResponse, "description": "Not a readable Anki package (anki_* code)"},
    },
)
async def import_deck(
    request: Request,
    user: Annotated[CurrentUser, limited("anki_import")],
    prefix: Annotated[str | None, Query(max_length=DECK_MAX, description="Optional parent deck name")] = None,
) -> ImportReport:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise AnkiError(413, "anki_too_large")
    with tempfile.TemporaryDirectory(prefix="naadi-apkg-") as tmp:
        path = Path(tmp) / "upload.apkg"
        size = 0
        with path.open("wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise AnkiError(413, "anki_too_large")
                fh.write(chunk)
        if size == 0:
            raise AnkiError(422, "anki_not_a_package")
        report: ImportReport = await _guard(
            importer.import_package(
                _db(request), _store(request), user.user_id, path, deck_prefix=_deck(prefix), limits=AnkiLimits()
            )
        )
    # Counts only: never the deck's names or text.
    log.info("anki_import", cards=report.cards_added, existing=report.cards_existing, images=report.images_added)
    return report


@router.get(
    "/export",
    operation_id="exportAnkiDeck",
    summary="Download your own cards as an .apkg (one deck with its sub-decks, or all)",
    response_class=Response,
    responses={
        **_ERRORS,
        200: {"content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}},
        404: {"model": ErrorResponse, "description": "No cards to export (code anki_no_cards_to_export)"},
    },
)
async def export_deck(
    request: Request,
    user: Annotated[CurrentUser, limited("anki_export")],
    deck: Annotated[str | None, Query(max_length=DECK_MAX)] = None,
) -> Response:
    name = _deck(deck)
    data: bytes = await _guard(exporter.build_package(_db(request), _store(request), user.user_id, name))
    fname = f"naadi-{exporter.slug(name or 'all-decks')}.apkg"
    return Response(
        data,
        media_type="application/octet-stream",
        headers={"content-disposition": f"attachment; filename=\"{fname}\"; filename*=UTF-8''{quote(fname)}"},
    )


@router.delete(
    "/decks",
    operation_id="deleteImportedAnkiDeck",
    summary="Delete the cards you imported into a deck (your own typed cards stay)",
    response_model=DeletedOut,
    responses={**_ERRORS},
)
async def delete_deck(
    request: Request,
    user: Annotated[CurrentUser, limited("anki_import")],
    deck: Annotated[str, Query(max_length=DECK_MAX)],
) -> DeletedOut:
    name = _deck(deck)

    async def run() -> DeletedOut:
        async with service_tx(_db(request), timeout_ms=15_000) as conn:
            cur = await conn.execute(
                "delete from public.flashcards where owner_user_id = %s and origin = 'anki_import' and deck = %s",
                (user.user_id, name),
            )
            return DeletedOut(deleted=cur.rowcount)

    return await _guard(run())


async def _anki_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AnkiError)
    return error_response(exc.status, exc.code, exc.code)


def install_anki_routes(app: Any) -> None:
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
    app.add_exception_handler(AnkiError, _anki_error)
    app.include_router(router)


__all__ = ["install_anki_routes", "router"]
