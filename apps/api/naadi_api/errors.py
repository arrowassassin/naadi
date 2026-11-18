"""One JSON error shape for every failure: `{"error": {"code", "message", "request_id"}}`."""

from __future__ import annotations

import contextvars
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


class FieldError(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class ErrorBody(BaseModel):
    code: str = Field(description="Stable machine-readable code, e.g. not_found, validation_error")
    message: str = Field(description="Human-readable message, safe to show to the user")
    request_id: str | None = Field(description="Echoes the X-Request-ID response header")
    details: list[FieldError] | None = Field(default=None, description="Present for validation errors")


class ErrorResponse(BaseModel):
    error: ErrorBody


def _code_for(status: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        413: "payload_too_large",
        415: "unsupported_media_type",
        422: "validation_error",
        429: "rate_limited",
        500: "internal_error",
        502: "bad_gateway",
        503: "service_unavailable",
        504: "gateway_timeout",
    }.get(status, "http_error")


def error_payload(
    status: int, message: str | None = None, code: str | None = None, details: list[FieldError] | None = None
) -> dict[str, Any]:
    try:
        default_msg = HTTPStatus(status).phrase
    except ValueError:
        default_msg = "Error"
    body = ErrorBody(
        code=code or _code_for(status),
        message=message or default_msg,
        request_id=request_id_var.get(),
        details=details,
    )
    return ErrorResponse(error=body).model_dump(mode="json", exclude_none=True)


def error_response(
    status: int,
    message: str | None = None,
    code: str | None = None,
    headers: dict[str, str] | None = None,
    details: list[FieldError] | None = None,
) -> JSONResponse:
    return JSONResponse(error_payload(status, message, code, details), status_code=status, headers=headers)


class CodedHTTPException(StarletteHTTPException):
    """An HTTP error with a stable `code` the client maps to its own (ui_copy) wording."""

    def __init__(self, status_code: int, code: str, detail: str | None = None) -> None:
        super().__init__(status_code, detail)
        self.code = code


async def _http_exc(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    # Only pass through messages we chose; Starlette's defaults are the status phrase anyway.
    msg = exc.detail if isinstance(exc.detail, str) else None
    code = exc.code if isinstance(exc, CodedHTTPException) else None
    return error_response(exc.status_code, msg, code, headers=getattr(exc, "headers", None))


async def _validation_exc(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    details = [
        # Never echo `input`: it can contain whatever the client sent (notes, tokens).
        FieldError(
            loc=[p for p in e.get("loc", ()) if isinstance(p, (str, int))],
            msg=str(e.get("msg", "")),
            type=str(e.get("type", "")),
        )
        for e in exc.errors()
    ]
    return error_response(422, "The request was not valid.", "validation_error", details=details)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exc)
    app.add_exception_handler(RequestValidationError, _validation_exc)
