"""The calendar feed: a private ICS URL a calendar app can subscribe to (`webcal://…`).

- The token is 32 random bytes, shown once; only its SHA-256 is stored (`calendar_feeds.token_hash`), so a database
  read cannot reveal a working URL. Rotating revokes the old token and makes a new one; revoking leaves none.
- The feed carries what the plan already says and nothing else: one all-day event per study day holding the day's
  item titles (the plan template's own text) and durations. No names, e-mail addresses, notes, scores or question
  content. Event ids are stable (derived from the student's id and the date, which reveal neither).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import psycopg
from icalendar import Calendar, Event

from ..copy import render
from .store import rows

Conn = psycopg.AsyncConnection[Any]
UID_NAMESPACE = uuid.UUID("5d1c1a7e-9b0f-4a52-8f06-2f4e0d7a6c31")
TOKEN_BYTES = 32


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return token, hash_token(token)


async def rotate(conn: Conn, user_id: uuid.UUID) -> tuple[str, dt.datetime]:
    """Revoke the student's active token (if any) and make a new one. Returns the token (shown once) and its time."""
    await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"calendar_feed:{user_id}",))
    await conn.execute(
        "update public.calendar_feeds set revoked_at = now() where user_id = %s and revoked_at is null", (user_id,)
    )
    token, digest = new_token()
    made = await rows(
        conn,
        "insert into public.calendar_feeds (user_id, token_hash) values (%s, %s) returning created_at",
        (user_id, digest),
    )
    return token, made[0]["created_at"]


async def revoke(conn: Conn, user_id: uuid.UUID) -> bool:
    done = await rows(
        conn,
        "update public.calendar_feeds set revoked_at = now() where user_id = %s and revoked_at is null returning id",
        (user_id,),
    )
    return bool(done)


async def active_since(conn: Conn, user_id: uuid.UUID) -> dt.datetime | None:
    found = await rows(
        conn, "select created_at from public.calendar_feeds where user_id = %s and revoked_at is null", (user_id,)
    )
    return found[0]["created_at"] if found else None


async def user_for_token(conn: Conn, token: str) -> uuid.UUID | None:
    """The student a live token belongs to; None for an unknown or revoked token (both look the same)."""
    found = await rows(
        conn,
        "select user_id from public.calendar_feeds where token_hash = %s and revoked_at is null",
        (hash_token(token),),
    )
    return found[0]["user_id"] if found else None


def _duration(copy: Mapping[str, str], minutes: int | None) -> str:
    if not minutes:
        return ""
    h, m = divmod(minutes, 60)
    if h and m:
        return render(copy, "planner.duration_hm", {"h": h, "m": m})
    if h:
        return render(copy, "planner.duration_h", {"h": h})
    return render(copy, "planner.duration_m", {"n": m})


def build_ics(
    user_id: uuid.UUID,
    days: Sequence[Mapping[str, Any]],
    items: Sequence[Mapping[str, Any]],
    copy: Mapping[str, str],
    *,
    now: dt.datetime | None = None,
) -> bytes:
    """All-day events for the plan's days that still have something to do. Skipped items are left out."""
    stamp = now or dt.datetime.now(dt.UTC)
    cal = Calendar()
    cal.add("prodid", "-//Naadi//Study plan//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", render(copy, "planner.ics_name"))
    by_day: dict[dt.date, list[Mapping[str, Any]]] = {}
    for it in items:
        if it["status"] != "skipped":
            by_day.setdefault(it["day"], []).append(it)
    for d in days:
        day_items = sorted(by_day.get(d["date"], []), key=lambda i: i["sort"] if i["sort"] is not None else 1 << 30)
        if not day_items:
            continue
        minutes = sum(i["target_minutes"] or 0 for i in day_items)
        ev = Event()
        day_key = f"{user_id}:{d['date'].isoformat()}"
        ev.add("uid", f"{uuid.uuid5(UID_NAMESPACE, day_key)}@naadi")
        ev.add("dtstamp", stamp)
        ev.add("dtstart", d["date"])
        ev.add("dtend", d["date"] + dt.timedelta(days=1))
        ev.add("transp", "TRANSPARENT")
        hours = f"{minutes / 60:.1f}".rstrip("0").rstrip(".")
        ev.add(
            "summary",
            render(copy, "planner.ics_summary", {"hours": hours, "day": d["day_no"]})
            if minutes
            else render(copy, "planner.ics_summary_plain", {"day": d["day_no"]}),
        )
        lines = []
        for i in day_items:
            dur = _duration(copy, i["target_minutes"])
            key = "planner.ics_item" if dur else "planner.ics_item_plain"
            # Items the student (or the tutor) typed are free text: the feed shows a generic label instead, so it
            # carries only the plan template's own words, whatever a student writes.
            title = render(copy, "planner.ics_own_item") if i.get("origin") == "user" else i["title"]
            lines.append(render(copy, key, {"title": title, "duration": dur}).strip())
        ev.add("description", "\n".join(lines))
        cal.add_component(ev)
    return bytes(cal.to_ical())
