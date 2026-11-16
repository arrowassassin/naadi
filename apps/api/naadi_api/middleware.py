"""Outermost ASGI middleware: request id, security headers, access log, last-resort error handler.

Written as plain ASGI (not BaseHTTPMiddleware) so streaming responses (SSE in later segments) pass
through untouched.
"""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import error_response, request_id_var
from .logs import get_logger
from .scrub import scrub
from .security import security_headers
from .settings import Settings

_VALID_RID = re.compile(r"^[A-Za-z0-9._:\-]{8,128}$")
log = get_logger("naadi.request")


def _incoming_request_id(scope: Scope) -> str | None:
    for k, v in scope.get("headers", []):
        if k == b"x-request-id":
            try:
                s = v.decode("ascii")
            except UnicodeDecodeError:
                return None
            return s if _VALID_RID.match(s) else None
    return None


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self._headers = security_headers(settings)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid = _incoming_request_id(scope) or uuid.uuid4().hex
        token = request_id_var.set(rid)
        structlog.contextvars.bind_contextvars(request_id=rid)
        method: str = scope.get("method", "")
        path: str = scope.get("path", "")  # never the query string: it may carry tokens
        start = time.perf_counter()
        state = {"status": 500, "started": False}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["started"] = True
                state["status"] = message["status"]
                headers = MutableHeaders(scope=message)
                for k, v in self._headers:
                    name = k.decode("latin-1")
                    if name not in headers:
                        headers.raw.append((k, v))
                headers["x-request-id"] = rid
                if path.startswith("/api") and "cache-control" not in headers:
                    headers["cache-control"] = "no-store"
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            log.error("unhandled_exception", method=method, path=path, exc_info=exc)
            if not state["started"]:
                if self.settings.is_local:
                    msg = scrub(f"{type(exc).__name__}: {exc}", self.settings.secret_values())
                else:
                    msg = "Something went wrong on our side. Try again in a moment."
                await error_response(500, msg, "internal_error")(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            status = int(state["status"])
            level = "error" if status >= 500 else ("warning" if status >= 400 and status != 404 else "info")
            getattr(log, level)("request", method=method, path=path, status=status, duration_ms=duration_ms)
            structlog.contextvars.unbind_contextvars("request_id")
            request_id_var.reset(token)
