"""Wires the pathway, vault, cost and admin routers into the app."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from ..auth_limits import RateLimiter
from ..errors import error_response
from . import admin, routes, vault_routes
from .db import PathwayError, default_limiters


async def _pathway_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, PathwayError)
    resp = error_response(exc.status, exc.code, exc.code)
    if exc.code.startswith("vault_"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def install_pathway_routes(app: Any) -> None:
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
    app.add_exception_handler(PathwayError, _pathway_error)
    app.include_router(routes.router)
    app.include_router(vault_routes.router)
    app.include_router(admin.router)
