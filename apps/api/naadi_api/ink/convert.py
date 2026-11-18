"""`POST /api/ink/convert`: the lasso's "Convert to text" (segment 13). Nothing is stored.

The body is the PNG the web rendered from the lasso selection (`Content-Type: image/png`, raw bytes, no multipart).
It is checked (size, PNG signature, decodable, at most 4096 px a side), flattened onto white and re-encoded by
Pillow, so no ancillary chunk (text, EXIF, ICC) the client sent ever reaches a model, then sent in one `vision` call
through `naadi_api/llm.py` (purpose `chat`: an interactive request). The text comes back for the student to confirm
before the web replaces the ink with a text box.

Limits: the route's rate limiter (10 a minute), the student's daily count (`ink_ocr.convert_per_user_daily`, counted
in `llm_usage` by run id `ink_convert:<uuid>`) and the gateway's rpm buckets and daily caps. Image bytes and the
recognised text are never logged.
"""

from __future__ import annotations

import datetime as dt
import io
import math
import uuid
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from PIL import Image, UnidentifiedImageError
from psycopg.rows import dict_row, tuple_row
from pydantic import BaseModel, Field

from ..auth import CurrentUser, get_account_user
from ..config_api.schemas import InkOcrRules
from ..db import Database
from ..errors import ErrorResponse, error_response
from ..llm import LLMUnavailable, get_gateway
from ..llm_budget import BudgetExhausted
from ..llm_privacy import PrivacyUnavailable
from ..logs import get_logger
from .ocr.recognize import UnreadableReply, recognize
from .ocr.run import calls_today

log = get_logger("naadi.ink.convert")
router = APIRouter(prefix="/api", tags=["ink"])

MAX_PNG_BYTES = 4 * 1024 * 1024
MAX_SIDE_PX = 4096
MIN_SIDE_PX = 8
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
RUN_PREFIX = "ink_convert:"


class ConvertError(Exception):
    def __init__(self, status: int, code: str, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.retry_after = retry_after


class InkConvertOut(BaseModel):
    text: str = Field(description="Recognised text, lines joined with newlines; empty when nothing was legible")
    lines: list[str]


def clean_png(raw: bytes) -> bytes:
    """Validate and re-encode the lasso image: RGB on white, no metadata. Raises ConvertError."""
    if len(raw) > MAX_PNG_BYTES:
        raise ConvertError(413, "ink_convert_too_large")
    if not raw.startswith(PNG_SIGNATURE):
        raise ConvertError(415, "ink_convert_not_png")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.format != "PNG":
                raise ConvertError(415, "ink_convert_not_png")
            w, h = img.size  # read from the header: nothing decoded yet
            if not (MIN_SIDE_PX <= w <= MAX_SIDE_PX and MIN_SIDE_PX <= h <= MAX_SIDE_PX):
                raise ConvertError(422, "ink_convert_invalid")
            img.load()
            rgba = img.convert("RGBA")
    except ConvertError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ConvertError(422, "ink_convert_invalid") from exc
    page = Image.new("RGB", rgba.size, (255, 255, 255))
    page.paste(rgba, mask=rgba.getchannel("A"))
    out = io.BytesIO()
    page.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _limited(request: Request, user: Annotated[CurrentUser, Depends(get_account_user)]) -> CurrentUser:
    request.app.state.rate_limiters["ink_convert"].hit(str(user.user_id))
    return user


async def _read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_PNG_BYTES:
        raise ConvertError(413, "ink_convert_too_large")
    ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype != "image/png":
        raise ConvertError(415, "ink_convert_not_png")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_PNG_BYTES:
            raise ConvertError(413, "ink_convert_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


async def _used_today(db: Database, uid: uuid.UUID) -> int:
    if db.pool is None:
        await db.open()
    if db.pool is None:
        raise ConvertError(503, "ink_convert_unavailable")
    async with db.pool.connection(timeout=5.0) as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]
        try:
            return await calls_today(conn, uid, RUN_PREFIX)
        finally:
            conn.row_factory = tuple_row


@router.post(
    "/ink/convert",
    operation_id="convertInkToText",
    summary="Recognise the handwriting in a lasso selection (PNG body) and return the text; nothing is stored",
    response_model=InkConvertOut,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
    responses={
        401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
        413: {"model": ErrorResponse, "description": "ink_convert_too_large (over 4 MB)"},
        415: {"model": ErrorResponse, "description": "ink_convert_not_png"},
        422: {"model": ErrorResponse, "description": "ink_convert_invalid (not decodable, or a side over 4096 px)"},
        429: {
            "model": ErrorResponse,
            "description": "rate_limited, ink_convert_daily_cap or ink_convert_budget (Retry-After set)",
        },
        502: {"model": ErrorResponse, "description": "ink_convert_failed (the model did not answer as asked)"},
        503: {"model": ErrorResponse, "description": "ink_convert_unavailable"},
    },
)
async def convert_ink(request: Request, user: Annotated[CurrentUser, Depends(_limited)]) -> InkConvertOut:
    raw = await _read_body(request)
    png = clean_png(raw)
    db: Database = request.app.state.db
    try:
        rules = InkOcrRules.model_validate(await request.app.state.app_config.get("ink_ocr"))
        used = await _used_today(db, user.user_id)
    except (psycopg.OperationalError, TimeoutError, RuntimeError) as exc:
        raise ConvertError(503, "ink_convert_unavailable") from exc
    if used >= rules.convert_per_user_daily:
        raise ConvertError(429, "ink_convert_daily_cap", _seconds_to_midnight())
    try:
        gateway = get_gateway()
    except RuntimeError as exc:  # the agent runtime is not started in this process
        raise ConvertError(503, "ink_convert_unavailable") from exc
    try:
        result = await recognize(
            gateway, png, user_id=user.user_id, run_id=f"{RUN_PREFIX}{uuid.uuid4()}", purpose="chat"
        )
    except BudgetExhausted as exc:
        wait = max(1, math.ceil((exc.reset_at - dt.datetime.now(dt.UTC)).total_seconds()))
        raise ConvertError(429, "ink_convert_budget", wait) from exc
    except (LLMUnavailable, PrivacyUnavailable) as exc:
        raise ConvertError(503, "ink_convert_unavailable") from exc
    except UnreadableReply as exc:
        log.info("ink_convert", outcome="unreadable")
        raise ConvertError(502, "ink_convert_failed") from exc
    log.info("ink_convert", outcome="ok", lines=len(result.lines), chars=len(result.text))
    return InkConvertOut(text=result.text, lines=result.lines)


def _seconds_to_midnight() -> int:
    now = dt.datetime.now(dt.UTC)
    nxt = (now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, math.ceil((nxt - now).total_seconds()))


async def _convert_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ConvertError)
    headers = {"retry-after": str(exc.retry_after)} if exc.retry_after else None
    return error_response(exc.status, exc.code, exc.code, headers=headers)


def install_convert_route(app: Any) -> None:
    app.add_exception_handler(ConvertError, _convert_error)
    app.include_router(router)


__all__ = ["MAX_PNG_BYTES", "InkConvertOut", "clean_png", "install_convert_route", "router"]
