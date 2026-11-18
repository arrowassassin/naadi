"""`POST /api/csp-report`: CSP and Trusted Types violation reports from browsers (segment 22).

Browsers send two shapes: the older `application/csp-report` (`{"csp-report": {...}}`, WebKit and Firefox via
`report-uri`) and the Reporting API's `application/reports+json` (a list of `{"type": "csp-violation", "body":
{...}}`, Chromium via `report-to`). Either way only a summary is logged:

- the directive that was violated, the disposition (enforce / report) and a hit count;
- the blocked resource reduced to its origin or a keyword (`inline`, `eval`, `trusted-types-sink`, ...);
- the document's path (never its query string or fragment, which may hold tokens).

The report body is never logged: `sample` / `script-sample` can contain page text or note content. Each client
is rate-limited (`csp_report` bucket, per IP), the body is capped, and identical summaries are logged at most once
a minute (a looping page cannot flood the log). The endpoint always answers 204, so it tells a caller nothing.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Request, Response

from .auth_limits import RateLimiter, ip_limited, register_limiters
from .logs import get_logger

log = get_logger("naadi.csp")
router = APIRouter(prefix="/api")

MAX_BODY_BYTES = 16 * 1024
MAX_REPORTS_PER_REQUEST = 20
KEYWORDS = frozenset(
    {"inline", "eval", "wasm-eval", "data", "blob", "self", "trusted-types-sink", "trusted-types-policy"}
)
LOG_EVERY_S = 60.0
_MAX_SUMMARIES = 512


def _blocked(value: Any) -> str:
    """The blocked resource as an origin or a keyword; never a full URL (paths and queries can carry tokens)."""
    if not isinstance(value, str) or not value:
        return "unknown"
    v = value.strip().lower()
    if v in KEYWORDS:
        return v
    if v.startswith(("data:", "blob:")):
        return v.split(":", 1)[0]
    try:
        parts = urlsplit(value)
    except ValueError:
        return "invalid"
    if parts.scheme in ("http", "https", "ws", "wss") and parts.hostname:
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{parts.hostname}{port}"[:120]
    return "other"


def _path(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    try:
        return (urlsplit(value).path or "/")[:120]
    except ValueError:
        return "invalid"


def _word(value: Any, default: str = "unknown") -> str:
    if not isinstance(value, str) or not value:
        return default
    # Directive names and dispositions are short tokens; anything else is cut down to one.
    return value.strip().split(" ", 1)[0][:40]


def summarize(payload: Any) -> list[dict[str, str]]:
    """Reduce a report payload (either browser shape) to loggable summaries; unknown shapes give []."""
    bodies: list[dict[str, Any]] = []
    if isinstance(payload, dict) and isinstance(payload.get("csp-report"), dict):
        bodies.append(payload["csp-report"])
    elif isinstance(payload, list):
        for item in payload[:MAX_REPORTS_PER_REQUEST]:
            if isinstance(item, dict) and item.get("type") == "csp-violation" and isinstance(item.get("body"), dict):
                bodies.append(item["body"])
    out = []
    for b in bodies:
        directive = b.get("effectiveDirective") or b.get("effective-directive") or b.get("violated-directive")
        out.append(
            {
                "directive": _word(directive),
                "disposition": _word(b.get("disposition"), "enforce"),
                "blocked": _blocked(b.get("blockedURL") or b.get("blocked-uri")),
                "document": _path(b.get("documentURL") or b.get("document-uri")),
            }
        )
    return out


class _Dedup:
    """Logs each distinct summary at most once per LOG_EVERY_S, with the number of hits it stood for."""

    def __init__(self, clock: Any = time.monotonic) -> None:
        self._clock = clock
        self._seen: OrderedDict[tuple[str, ...], tuple[float, int]] = OrderedDict()

    def offer(self, s: dict[str, str]) -> int | None:
        key = (s["directive"], s["disposition"], s["blocked"], s["document"])
        now = self._clock()
        last = self._seen.get(key)
        if last is not None and now - last[0] < LOG_EVERY_S:
            self._seen[key] = (last[0], last[1] + 1)
            return None
        count = 1 if last is None else last[1] + 1
        self._seen[key] = (now, 0)
        self._seen.move_to_end(key)
        while len(self._seen) > _MAX_SUMMARIES:
            self._seen.popitem(last=False)
        return count


@router.post(
    "/csp-report",
    operation_id="postCspReport",
    summary="Browser CSP / Trusted Types violation reports (summaries logged, bodies never)",
    status_code=204,
    include_in_schema=False,  # browsers call it from the policy header, never the app's client
    dependencies=[ip_limited("csp_report")],
)
async def csp_report(request: Request) -> Response:
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > MAX_BODY_BYTES:
            log.info("csp_report_too_large")
            return Response(status_code=204)
    try:
        payload = json.loads(raw) if raw else None
    except (ValueError, UnicodeDecodeError):
        payload = None
    dedup: _Dedup = request.app.state.csp_dedup
    for s in summarize(payload):
        hits = dedup.offer(s)
        if hits is not None:
            log.warning("csp_violation", hits=hits, **s)
    return Response(status_code=204)


def install_csp_report(app: Any) -> None:
    app.state.csp_dedup = _Dedup()
    register_limiters(app, {"csp_report": RateLimiter(30, 60.0)})
    app.include_router(router)
