"""Waiting for the gateway's rpm bucket instead of failing an item.

When every provider's bucket is empty for longer than `budget.rate_wait_s`, `LLMGateway` raises
`BudgetExhausted("rate_limited")` before sending anything (the turn slot and the ledger row are not taken), so
waiting and calling again respects the rpm guard and never double-counts a request.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from naadi_api.llm_budget import BudgetExhausted, TurnLimitReached

MAX_RATE_WAITS = 40
MAX_WAIT_S = 65.0
Sleep = Callable[[float], Awaitable[Any]]
_sleep: Sleep = asyncio.sleep


def use_sleep(fn: Sleep | None) -> None:
    """The gateway's sleep must be this one too: a dry run's virtual clock only moves when something sleeps on it."""
    global _sleep
    _sleep = fn or asyncio.sleep


async def with_rate_wait[T](call: Callable[[], Awaitable[T]], *, sleep: Sleep | None = None) -> T:
    """Retry `call` while the gateway refuses with `rate_limited`; any other refusal propagates."""
    sleep = sleep or _sleep
    for _ in range(MAX_RATE_WAITS):
        try:
            return await call()
        except TurnLimitReached:
            raise
        except BudgetExhausted as exc:
            if exc.reason != "rate_limited":
                raise
            wait = (exc.reset_at - datetime.now(UTC)).total_seconds()
            await sleep(min(MAX_WAIT_S, max(1.0, wait)))
    return await call()


__all__ = ["MAX_RATE_WAITS", "use_sleep", "with_rate_wait"]
