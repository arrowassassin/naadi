"""Per-key sliding-window rate limiter for auth-adjacent routes (`/api/me`, `/api/nav`, admin).

One process serves the Space, so an in-memory limiter is exact. Magic-link sends are rate-limited by
Supabase itself; this protects our own endpoints and the database behind them.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict, deque
from collections.abc import Callable

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
