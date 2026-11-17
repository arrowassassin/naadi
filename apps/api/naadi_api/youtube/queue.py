"""The `youtube_candidates` approval queue, used by the admin console (segment 19) and the admin routes here.

A candidate is a `video_candidates` row for a `media_assets` row that stays hidden from students (`review_status
'unreviewed'`) until an admin approves it. Approving links the video to a section (`section_media`, with an optional
start and end second); rejecting keeps the row so the mapper never proposes the video again and drops the stored
metadata at once. Everything runs with the pool's privileged role; the caller (an admin route) has already checked
the admin gate.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

import psycopg

from ..anki.db import service_tx
from ..db import Database
from . import mapper
from .client import YouTubeApi, iso_duration_s, keep, media_id
from .models import ApproveOut, CandidateOut, CandidatesOut, CandidateStatus


class QueueError(Exception):
    """Stable code (ui_copy `admin.youtube.error.<code>`); `status` is the HTTP status."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_URL = re.compile(
    r"^(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/)|youtu\.be/|youtube-nocookie\.com/embed/)"
    r"([A-Za-z0-9_-]{11})(?:[?&#/].*)?$"
)


def parse_video_id(raw: str) -> str | None:
    s = raw.strip()
    if _YT_ID.fullmatch(s):
        return s
    m = _URL.match(s)
    return m.group(1) if m else None


_SELECT = """
select vc.id, vc.media_id, m.youtube_id, m.title, m.channel_id, m.duration_s, vc.score, vc.matched_terms,
       vc.section_id, s.heading as section_heading, s.chapter_id, vc.taxonomy_ids, vc.status, vc.created_at
from public.video_candidates vc
join public.media_assets m on m.id = vc.media_id
left join public.chapter_sections s on s.id = vc.section_id
"""


def _out(r: dict[str, Any]) -> CandidateOut:
    return CandidateOut(
        candidate_id=str(r["id"]),
        media_id=r["media_id"],
        youtube_id=r["youtube_id"] or "",
        title=r["title"],
        channel_id=r["channel_id"],
        duration_s=r["duration_s"],
        score=float(r["score"]),
        matched_terms=list(r["matched_terms"] or []),
        section_id=r["section_id"],
        section_heading=r["section_heading"],
        chapter_id=r["chapter_id"],
        taxonomy_ids=list(r["taxonomy_ids"] or []),
        status=r["status"],
        created_at=r["created_at"],
    )


async def pending_count(db: Database) -> int:
    async with service_tx(db, timeout_ms=8000) as conn:
        cur = await conn.execute("select count(*)::int as n from public.video_candidates where status = 'pending'")
        row = await cur.fetchone()
    return int(row["n"]) if row else 0


async def list_candidates(
    db: Database, *, status: CandidateStatus = "pending", limit: int = 50, offset: int = 0
) -> CandidatesOut:
    async with service_tx(db, timeout_ms=8000) as conn:
        cur = await conn.execute(
            _SELECT + " where vc.status = %s order by vc.score desc, vc.created_at, vc.id limit %s offset %s",
            (status, limit, offset),
        )
        items = [_out(dict(r)) for r in await cur.fetchall()]
        cur = await conn.execute("select count(*)::int as n from public.video_candidates where status = 'pending'")
        row = await cur.fetchone()
    return CandidatesOut(items=items, pending=int(row["n"]) if row else 0)


async def _candidate(conn: psycopg.AsyncConnection[Any], mid: str) -> list[dict[str, Any]]:
    cur = await conn.execute("select * from public.video_candidates where media_id = %s for update", (mid,))
    return [dict(r) for r in await cur.fetchall()]


async def approve(
    db: Database,
    media: str,
    *,
    reviewer: uuid.UUID,
    section_id: str | None = None,
    start_s: int | None = None,
    end_s: int | None = None,
) -> ApproveOut:
    if start_s is not None and end_s is not None and end_s <= start_s:
        raise QueueError(422, "video_bad_range")
    async with service_tx(db, timeout_ms=10_000) as conn:
        rows = await _candidate(conn, media)
        if not rows:
            raise QueueError(404, "video_candidate_not_found")
        if all(r["status"] == "rejected" for r in rows):
            raise QueueError(409, "video_rejected")
        target = section_id or next((r["section_id"] for r in rows if r["status"] in ("pending", "approved")), None)
        if target is None:
            raise QueueError(422, "video_needs_section")
        cur = await conn.execute(
            "select 1 as ok from public.chapter_sections s join public.chapters c on c.id = s.chapter_id"
            " where s.id = %s and c.owner_user_id is null",
            (target,),
        )
        if await cur.fetchone() is None:
            raise QueueError(404, "video_section_not_found")
        cur = await conn.execute(
            "select review_status, embeddable, metadata_fetched_at from public.media_assets where id = %s", (media,)
        )
        m = await cur.fetchone()
        if m is None or m["embeddable"] is False or all(r["status"] == "stale" for r in rows):
            raise QueueError(409, "video_unavailable")
        await conn.execute("update public.media_assets set review_status = 'approved' where id = %s", (media,))
        await conn.execute(
            "insert into public.section_media (section_id, media_id, sort, start_s, end_s)"
            " values (%s, %s, (select coalesce(max(sort), 0) + 1 from public.section_media where section_id = %s),"
            " %s, %s) on conflict (section_id, media_id) do update"
            " set start_s = excluded.start_s, end_s = excluded.end_s",
            (target, media, target, start_s, end_s),
        )
        # The decision is on this (video, section); the same video proposed for other sections stays pending.
        await conn.execute(
            "insert into public.video_candidates (media_id, section_id, score, status, reviewed_by, reviewed_at)"
            " values (%s, %s, 0, 'approved', %s, now())"
            " on conflict (media_id, section_id) do update set status = 'approved', reviewed_by = excluded.reviewed_by,"
            " reviewed_at = excluded.reviewed_at",
            (media, target, reviewer),
        )
    return ApproveOut(media_id=media, section_id=target, start_s=start_s, end_s=end_s)


async def reject(db: Database, media: str, *, reviewer: uuid.UUID) -> None:
    async with service_tx(db, timeout_ms=10_000) as conn:
        rows = await _candidate(conn, media)
        if not rows:
            raise QueueError(404, "video_candidate_not_found")
        await conn.execute("delete from public.section_media where media_id = %s", (media,))
        await conn.execute(
            "update public.video_candidates set status = 'rejected', reviewed_by = %s, reviewed_at = now()"
            " where media_id = %s",
            (reviewer, media),
        )
        # Only the id and the decision are kept: a rejected video's title and description are not stored.
        await conn.execute(
            "update public.media_assets set review_status = 'rejected', title = null, caption = null,"
            " attribution = null, duration_s = null, metadata_fetched_at = null where id = %s",
            (media,),
        )


async def add_by_url(
    db: Database,
    api: YouTubeApi,
    raw: str,
    section_id: str,
    *,
    reviewer: uuid.UUID,
    approve_now: bool = False,
    start_s: int | None = None,
    end_s: int | None = None,
) -> CandidateOut:
    """An admin adds a video by link: checked with `videos.list` (public + embeddable), stored like a mapper find."""
    vid = parse_video_id(raw)
    if vid is None:
        raise QueueError(422, "video_bad_url")
    items = await asyncio.to_thread(api.videos, [vid])
    item = items.get(vid)
    if item is None or not keep(item):
        raise QueueError(422, "video_not_embeddable")
    mid = media_id(vid)
    sn, cd = item.get("snippet") or {}, item.get("contentDetails") or {}
    title = str(sn.get("title") or "")[:300] or None
    async with service_tx(db, timeout_ms=10_000) as conn:
        cur = await conn.execute("select 1 as ok from public.chapter_sections where id = %s", (section_id,))
        if await cur.fetchone() is None:
            raise QueueError(404, "video_section_not_found")
        cur = await conn.execute("select review_status from public.media_assets where id = %s", (mid,))
        existing = await cur.fetchone()
        if existing and existing["review_status"] == "rejected":
            raise QueueError(409, "video_rejected")
        await conn.execute(
            "insert into public.media_assets (id, kind, source_id, title, youtube_id, channel_id, duration_s,"
            " embeddable, metadata_fetched_at, attribution, review_status)"
            " values (%s, 'video_youtube', 'youtube', %s, %s, %s, %s, true, now(), %s, 'unreviewed')"
            " on conflict (id) do update set title = excluded.title,"
            " duration_s = excluded.duration_s, embeddable = true, metadata_fetched_at = now(),"
            " attribution = excluded.attribution",
            (
                mid,
                title,
                vid,
                sn.get("channelId"),
                iso_duration_s(cd.get("duration")),
                mapper.ATTRIBUTION.format(title=title, channel=sn.get("channelTitle") or "") if title else None,
            ),
        )
        await conn.execute(
            "insert into public.video_candidates (media_id, section_id, score) values (%s, %s, 1)"
            " on conflict (media_id, section_id) do update set status = case when public.video_candidates.status"
            " in ('stale', 'rejected') then 'pending' else public.video_candidates.status end,"
            " reviewed_at = case when public.video_candidates.status in ('stale', 'rejected') then null"
            " else public.video_candidates.reviewed_at end",
            (mid, section_id),
        )
    if approve_now:
        await approve(db, mid, reviewer=reviewer, section_id=section_id, start_s=start_s, end_s=end_s)
    async with service_tx(db, timeout_ms=8000) as conn:
        cur = await conn.execute(_SELECT + " where vc.media_id = %s and vc.section_id = %s", (mid, section_id))
        row = await cur.fetchone()
    assert row is not None
    return _out(dict(row))
