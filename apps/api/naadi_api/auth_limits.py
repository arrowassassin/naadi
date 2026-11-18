"""Per-key sliding-window rate limiter for auth-adjacent routes (`/api/me`, `/api/nav`, admin).

One process serves the Space, so an in-memory limiter is exact. Magic-link sends are rate-limited by
Supabase itself; this protects our own endpoints and the database behind them.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import Depends, Request

from .auth import CurrentUser, get_account_user
from .errors import error_response


class RateLimited(Exception):
    def __init__(self, retry_after_s: int) -> None:
        super().__init__("rate_limited")
        self.retry_after_s = retry_after_s


class RateLimiter:
    def __init__(
        self, limit: int, window_s: float, *, max_keys: int = 10_000, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if limit < 1 or window_s <= 0:
            raise ValueError("limit must be >= 1 and window_s > 0")
        self.limit = limit
        self.window_s = window_s
        self._max_keys = max_keys
        self._clock = clock
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()

    def hit(self, key: str) -> None:
        """Record one request for `key`; raise RateLimited when the window is full."""
        now = self._clock()
        q = self._hits.get(key)
        if q is None:
            q = deque()
            self._hits[key] = q
            while len(self._hits) > self._max_keys:
                self._hits.popitem(last=False)  # evict the least recently seen key
        else:
            self._hits.move_to_end(key)
        cutoff = now - self.window_s
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= self.limit:
            raise RateLimited(max(1, math.ceil(q[0] + self.window_s - now)))
        q.append(now)


async def rate_limited_handler(_: object, exc: Exception) -> object:
    assert isinstance(exc, RateLimited)
    return error_response(
        429,
        "Too many requests. Wait a moment and try again.",
        "rate_limited",
        headers={"retry-after": str(exc.retry_after_s)},
    )


# ── Shared limiter dependencies and the per-client floor (segment 22) ─────────────────────────────────────
# Every /api operation must carry a limiter (tests/test_rate_limit_audit.py). Feature modules may keep their own
# `limited(bucket)` closures; these helpers do the same and are tagged so the audit can name the bucket.
RATE_LIMIT_TAG = "__naadi_rate_limit__"


def client_ip(scope: Mapping[str, Any]) -> str:
    """The client address used as a rate-limit key for anonymous routes.

    The Space runs uvicorn with `--forwarded-allow-ips '*'`, which takes the LEFT-most X-Forwarded-For entry:
    that one is whatever the client sent, so keying on it would let a caller rotate keys at will. The
    right-most entry is the one Hugging Face's own proxy appended (the peer it saw), so it is used instead.
    Without the header (direct connections: local runs, CI) the socket peer is the key.
    """
    xff: bytes | None = None
    for k, v in scope.get("headers", []):
        if k == b"x-forwarded-for":
            xff = v if xff is None else xff + b"," + v
    if xff:
        last = xff.decode("latin-1").split(",")[-1].strip()
        if last:
            return last[:64]
    client = scope.get("client")
    return str(client[0]) if client else "unknown"


def counted_client(scope: Mapping[str, Any]) -> str | None:
    """`client_ip`, or None for a direct loopback connection (see CLIENT_FLOOR_PER_MIN): not counted."""
    has_xff = any(k == b"x-forwarded-for" for k, _ in scope.get("headers", []))
    ip = client_ip(scope)
    return None if (not has_xff and ip in LOOPBACK) else ip


def _tag(dep: Callable[..., Any], bucket: str, keyed_by: str) -> Callable[..., Any]:
    setattr(dep, RATE_LIMIT_TAG, (bucket, keyed_by))
    return dep


def rate_limited(bucket: str) -> Any:
    """`Depends(...)`: authenticate the account, then count the request against the user's `bucket`."""

    async def dep(request: Request, user: CurrentUser = Depends(get_account_user)) -> CurrentUser:  # noqa: B008
        limiters: dict[str, RateLimiter] = request.app.state.rate_limiters
        limiters[bucket].hit(str(user.user_id))
        return user

    return Depends(_tag(dep, bucket, "user"))


def ip_limited(bucket: str) -> Any:
    """`Depends(...)` for anonymous routes (public config, ICS feeds, violation reports): keyed per client IP."""

    async def dep(request: Request) -> None:
        key = counted_client(request.scope)
        if key is None:
            return
        limiters: dict[str, RateLimiter] = request.app.state.rate_limiters
        limiters[bucket].hit(key)

    return Depends(_tag(dep, bucket, "ip"))


def register_limiters(app: Any, limiters: Mapping[str, RateLimiter]) -> None:
    """Add buckets to `app.state.rate_limiters` without replacing ones a test or another module set."""
    current: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", None) or {}
    for k, v in limiters.items():
        current.setdefault(k, v)
    app.state.rate_limiters = current


# A floor under every bucket: requests per client IP per minute to anything but the hashed static assets. It is
# far above what one student generates (the busiest page makes ~40 API calls) and only stops floods; per-user
# buckets do the real limiting. Direct loopback connections (local runs, CI, the container's own probes) carry
# no X-Forwarded-For and are not counted: on the Space every outside request arrives through HF's proxy.
CLIENT_FLOOR_PER_MIN = 600
CLIENT_FLOOR_EXEMPT_PREFIXES = ("/assets/", "/fonts/", "/icons/", "/sandbox/")
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
