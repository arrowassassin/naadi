"""Admin console API (segment 19). Every route is behind `ConsoleAdmin` (server-side admin gate + rate limit)."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from ..errors import FieldError, error_response
from . import (
    agent_ops,
    blueprint,
    chapters,
    coverage,
    generated_media,
    lookups,
    media,
    questions,
    queues,
    reports,
    sibling_queues,
)
from .chapters import InvalidBody
from .common import ensure_limiters
from .licence import GateRefused

__all__ = ["install_admin_routes"]

# Imported for their register_queue side effect.
_QUEUE_MODULES = (generated_media, reports, sibling_queues)


async def _gate_refused(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, GateRefused)
    return error_response(
        409,
        "A publishing gate refused this change.",
        "gate_refused",
        details=[FieldError(loc=["gate", str(exc.reason)], msg=exc.ref_id or "", type="gate")],
    )


async def _invalid_body(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, InvalidBody)
    return error_response(
        422,
        "The section body does not match the content schema.",
        "validation_error",
        details=[FieldError(loc=["body", "body", *p.path], msg=p.code, type="content_schema") for p in exc.problems],
    )


def install_admin_routes(app: Any) -> None:
    ensure_limiters(app)
    app.add_exception_handler(GateRefused, _gate_refused)
    app.add_exception_handler(InvalidBody, _invalid_body)
    for mod in (queues, chapters, media, questions, coverage, blueprint, agent_ops, reports, lookups):
        app.include_router(mod.router)
