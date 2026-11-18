"""Pure pathway rules: nothing here touches the database, the network or a model."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

ReverifyReason = Literal["age", "flagged"]
DUE_ANCHORS = frozenset(
    {
        "start",
        "step1_date",
        "step2ck_date",
        "oet_date",
        "observership_start",
        "eras_open",
        "eras_submit",
        "rol_deadline",
        "match_day",
        "residency_start",
        "residency_end",
        "arrival",
    }
)
_DUE_ANCHOR = re.compile(r"^(?P<anchor>[a-z0-9_]+?)(?:(?P<sign>[+-])(?P<days>\d{1,4})d)?$")
_DUE_DATE = re.compile(r"^date:(?P<date>\d{4}-\d{2}-\d{2})$")
_DUE_ANNUAL = re.compile(r"^annual:(?P<month>\d{2})-(?P<day>\d{2})$")


def reverify_reason(
    last_verified: dt.date | None, needs_review: bool, today: dt.date, reverify_days: int
) -> ReverifyReason | None:
    """Why a task shows "Re-verify": the weekly check flagged it, or its last verification is more than
    `reverify_days` old (day 90 is still fine, day 91 is stale; a task never verified is stale)."""
    if needs_review:
        return "flagged"
    if last_verified is None or (today - last_verified).days > reverify_days:
        return "age"
    return None


def stale(last_verified: dt.date, today: dt.date, reverify_days: int) -> bool:
    """True once the last human check is more than `reverify_days` old (day 90 is fine, day 91 is stale)."""
    return (today - last_verified).days > reverify_days


def days_since(last_verified: dt.date | None, today: dt.date) -> int | None:
    return None if last_verified is None else max((today - last_verified).days, 0)


def parse_due_rule(rule: str | None) -> dict[str, object] | None:
    """A seeded `due_rule` as structured fields the web words with ui_copy: `{kind: 'anchor', anchor, days}`
    (days < 0 = before the anchor), `{kind: 'date', date}` or `{kind: 'annual', month, day}`. An unknown anchor
    gives None (the task shows no due rule rather than a wrong one)."""
    if not rule:
        return None
    m = _DUE_DATE.match(rule)
    if m:
        try:
            return {"kind": "date", "date": dt.date.fromisoformat(m["date"]).isoformat()}
        except ValueError:
            return None
    m = _DUE_ANNUAL.match(rule)
    if m:
        month, day = int(m["month"]), int(m["day"])
        try:
            dt.date(2000, month, day)
        except ValueError:
            return None
        return {"kind": "annual", "month": month, "day": day}
    m = _DUE_ANCHOR.match(rule)
    if m and m["anchor"] in DUE_ANCHORS:
        days = int(m["days"] or 0) * (-1 if m["sign"] == "-" else 1)
        return {"kind": "anchor", "anchor": m["anchor"], "days": days}
    return None


def host_matches(host: str, domains: Iterable[str]) -> bool:
    """`host` is one of `domains` or a subdomain of one (`www.ecfmg.org` matches `ecfmg.org`)."""
    h = host.lower().rstrip(".")
    return any(h == d.lower() or h.endswith("." + d.lower()) for d in domains)


def https_host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname:
        return None
    return parts.hostname.lower()


def port_ok(url: str) -> bool:
    """No port in the URL, or 443: nothing else is fetched."""
    try:
        port = urlsplit(url).port
    except ValueError:
        return False
    return port in (None, 443)


def is_official_url(url: str | None, domains: Iterable[str]) -> bool:
    host = https_host(url)
    return host is not None and host_matches(host, domains)


Money = Mapping[str, Decimal]


def add_money(into: dict[str, Decimal], currency: str, amount: Decimal | None) -> None:
    """Sum per currency. There is no exchange rate anywhere: 10 USD and 800 INR stay two numbers."""
    if amount is None:
        return
    into[currency] = into.get(currency, Decimal(0)) + amount


def money_list(m: Money) -> list[tuple[str, Decimal]]:
    """Currencies in a stable order, zero totals kept out."""
    return [(c, a) for c, a in sorted(m.items()) if a != 0]


def expiry_state(expires_on: dt.date | None, today: dt.date, soon_days: int) -> tuple[int | None, str]:
    """`(days left, 'none' | 'ok' | 'soon' | 'expired')`. `soon` = under `soon_days`; today is the last good day."""
    if expires_on is None:
        return None, "none"
    days = (expires_on - today).days
    if days < 0:
        return days, "expired"
    return days, "soon" if days < soon_days else "ok"
