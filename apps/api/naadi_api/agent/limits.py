"""Per-student limits on Aegra's own Agent Protocol routes (segment 22 rate-limit audit).

The Naadi API's routes carry their own buckets (tests/test_rate_limit_audit.py); Aegra adds `/threads`, `/runs`,
`/assistants`, `/store` and `/crons` to the same app at startup, outside that audit. This middleware gives them two
buckets, keyed by the bearer credential (a hash of it, never the token; a caller cannot make credentials without an
account, and Aegra's auth handler rejects a bad one right after):

- `agent_requests`: every request to those routes, 600/min (streams poll and join often);
- `agent_runs`: starting a run (POST .../runs, .../runs/stream, .../runs/wait), 30/min. A run is up to 4 LLM calls
  under the daily budget in config/models.yaml; this stops a loop from draining it in minutes.

Requests without a bearer token pass through to Aegra's auth, which answers 401. The 429 uses the app's error shape.
"""

from __future__ import annotations

import hashlib
import re

from starlette.types import ASGIApp, Receive, Scope, Send

from ..auth_limits import RateLimited, RateLimiter
from ..errors import error_response

AGENT_PREFIXES = ("/threads", "/runs", "/assistants", "/store", "/crons")
_RUN_START = re.compile(r"^(?:/threads/[^/]+)?/runs(?:/stream|/wait)?/?$")


def bearer_key(scope: Scope) -> str | None:
    for k, v in scope.get("headers", []):
        if k == b"authorization":
            scheme, _, token = v.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return hashlib.sha256(token.strip().encode()).hexdigest()[:32]
    return None


class AgentRateLimit:
    def __init__(self, app: ASGIApp, requests_per_min: int = 600, runs_per_min: int = 30) -> None:
        self.app = app
        self.requests = RateLimiter(requests_per_min, 60.0)
        self.runs = RateLimiter(runs_per_min, 60.0)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        if scope["type"] == "http" and path.startswith(AGENT_PREFIXES):
            key = bearer_key(scope)
            if key is not None:
                try:
                    self.requests.hit(key)
                    if scope.get("method") == "POST" and _RUN_START.match(path):
                        self.runs.hit(key)
                except RateLimited as exc:
                    await error_response(
                        429,
                        "Too many requests. Wait a moment and try again.",
                        "rate_limited",
                        headers={"retry-after": str(exc.retry_after_s)},
                    )(scope, receive, send)
                    return
        await self.app(scope, receive, send)
