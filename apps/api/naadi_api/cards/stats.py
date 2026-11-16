"""Local-day arithmetic for flashcard counts: "due today" means before the end of the student's own day.

Scheduling itself never looks at a time zone (FSRS counts 24-hour periods); only the counts shown to the student
do. Day boundaries come from the IANA zone database (`zoneinfo`), so DST days are 23 or 25 hours long and zones
with 30- or 45-minute offsets work. The web computes the same `local_date` with Intl (golden vectors).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_ZONE = "Asia/Kolkata"  # profiles.timezone default


def zone(name: str | None) -> ZoneInfo:
    """The named IANA zone, or the profile default when it is missing or unknown."""
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return ZoneInfo(DEFAULT_ZONE)


def valid_zone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def local_date(t: datetime, tz: ZoneInfo) -> date:
    return t.astimezone(tz).date()


def start_of_day(d: date, tz: ZoneInfo) -> datetime:
    """The first instant of local day `d` (midnight, or the first valid time when midnight is skipped by DST)."""
    # A skipped midnight (a DST change at 00:00) does not exist; zoneinfo resolves it with the offset before the
    # change (fold=0), which is the first real instant of the day.
    return datetime.combine(d, time(0), tzinfo=tz).astimezone(UTC)


def day_bounds(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime, datetime]:
    """(start of today, start of tomorrow, start of the day after), as UTC instants."""
    today = local_date(now, tz)
    return (
        start_of_day(today, tz),
        start_of_day(today + timedelta(days=1), tz),
        start_of_day(today + timedelta(days=2), tz),
    )


def streak(review_days: set[date], today: date) -> int:
    """Consecutive local days with at least one review, ending today (or yesterday, if today has none yet)."""
    d = today if today in review_days else today - timedelta(days=1)
    n = 0
    while d in review_days:
        n += 1
        d -= timedelta(days=1)
    return n
