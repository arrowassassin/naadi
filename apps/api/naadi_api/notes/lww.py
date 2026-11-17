"""Bounded last-writer-wins for notes and notebooks (segment 13 rework; segment 12b's ink rule). Pure functions.

Every write carries `client_updated_at`: when the student made the edit, in epoch ms on the server's clock as the
device sees it (the web keeps an offset to the server clock). The rule:

- absent: the edit happened now (an online client without a stamp, a script);
- a stamp more than `FUTURE_SKEW_MS` ahead of the server is clamped to the server's time + that skew, so a device
  with a wrong date cannot win every later merge (ink/store.py clamps the same way);
- a stamp older than the stored `edited_at` loses: nothing is written and the API answers 409 with the stored copy,
  so the device keeps its own version as a conflict copy instead of overwriting a newer edit made elsewhere;
- a tie passes (a replay of the same write, or two writes in the same millisecond).

`edited_at` is compared at millisecond precision (the stamps are whole ms; rows written before 0039 or without a
stamp carry microseconds that a client, which reads ms, cannot repeat).
"""

from __future__ import annotations

import datetime as dt
import time

from ..ink.store import FUTURE_SKEW_MS

__all__ = ["FUTURE_SKEW_MS", "edit_time", "is_stale", "ms", "now_ms"]


def now_ms() -> int:
    return time.time_ns() // 1_000_000


_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
_MS = dt.timedelta(milliseconds=1)


def ms(t: dt.datetime) -> int:
    """A stored timestamptz as whole epoch ms (floor; integer arithmetic, no float rounding)."""
    return (t - _EPOCH) // _MS


def edit_time(client_ms: int | None, server_ms: int) -> dt.datetime:
    """The edit time a write is stamped with: the client's, clamped to `server_ms + FUTURE_SKEW_MS`; absent = now."""
    t = server_ms if client_ms is None else min(client_ms, server_ms + FUTURE_SKEW_MS)
    return _EPOCH + t * _MS


def is_stale(edit: dt.datetime, stored: dt.datetime | None) -> bool:
    """True when `edit` is older than the stored edit (ms precision; ties pass)."""
    return stored is not None and ms(edit) < ms(stored)
