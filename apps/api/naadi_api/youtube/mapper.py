"""The weekly `youtube_map` run: refresh what is stored, then find new uploads and propose them for approval.

Order matters for policy and quota: first re-fetch metadata that is older than `refresh_days` (a video that is now
private, deleted or not embeddable is unlinked from every section, rejected, and its stored title dropped), then
read the newest uploads of each allow-listed channel, keep the public embeddable ones that are new, score them
against the published sections offline, and write `pending` candidates (at most `max_candidates_per_section` pending
per section). A spent unit budget stops the run early without losing what was already written.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
import yaml

from ..anki.db import service_tx
from ..config_api.schemas import YoutubeRules
from ..db import Database
from ..logs import get_logger
from .client import QuotaExceeded, YouTubeApi, YouTubeError, iso_duration_s, keep, media_id, uploads_playlist
from .models import RunReportOut
from .scoring import Embedder, Match, Section, score_videos

log = get_logger("naadi.youtube")

MAX_PER_VIDEO = 3
ATTRIBUTION = 'Video: "{title}" by {channel} on YouTube'


@dataclass(frozen=True, slots=True)
class Channel:
    channel_id: str
    name: str
    subjects: tuple[str, ...]


def load_channels(config_dir: Path) -> list[Channel]:
    """Only `include: true` and `verified: true` channels with a known id (an unverified handle is a guess)."""
    data = yaml.safe_load((config_dir / "youtube_channels.yaml").read_text(encoding="utf-8")) or {}
    out: list[Channel] = []
    for c in data.get("channels", []):
        if c.get("include") is True and c.get("verified") is True and c.get("channel_id"):
            out.append(
                Channel(str(c["channel_id"]), str(c.get("name") or c["channel_id"]), tuple(c.get("subjects") or ()))
            )
    return out


@dataclass(slots=True)
class Report:
    channels: int = 0
    listed: int = 0
    new_videos: int = 0
    candidates_added: int = 0
    refreshed: int = 0
    dropped: int = 0
    quota_used: int = 0
    stopped_early: bool = False
    errors: list[str] = field(default_factory=list)

    def out(self) -> RunReportOut:
        return RunReportOut(
            channels=self.channels,
            listed=self.listed,
            new_videos=self.new_videos,
            candidates_added=self.candidates_added,
            refreshed=self.refreshed,
            dropped=self.dropped,
            quota_used=self.quota_used,
            stopped_early=self.stopped_early,
        )


async def load_sections(conn: psycopg.AsyncConnection[Any]) -> list[Section]:
    cur = await conn.execute(
        "select s.id, s.heading, s.chapter_id, s.taxonomy_ids from public.chapter_sections s"
        " join public.chapters c on c.id = s.chapter_id where c.owner_user_id is null and c.status = 'published'"
        " order by s.chapter_id, s.sort, s.id"
    )
    rows = [dict(r) for r in await cur.fetchall()]
    ids = sorted({t for r in rows for t in (r["taxonomy_ids"] or [])})
    labels: dict[str, str] = {}
    if ids:
        cur = await conn.execute("select id, label from public.taxonomy_nodes where id = any(%s)", (ids,))
        labels = {r["id"]: r["label"] for r in await cur.fetchall()}
    return [
        Section(
            id=r["id"],
            heading=r["heading"] or "",
            chapter_id=r["chapter_id"],
            taxonomy_ids=tuple(r["taxonomy_ids"] or ()),
            text=" ".join([r["heading"] or "", *(labels.get(t, "") for t in (r["taxonomy_ids"] or []))]).strip(),
        )
        for r in rows
        if (r["heading"] or "").strip()
    ]


def pick(matches: Sequence[tuple[str, list[Match]]], room: dict[str, int]) -> list[tuple[str, Match]]:
    """Best-first (score) assignment of `(youtube_id, matches)` to sections with room; at most 3 sections a video."""
    flat = sorted(((m.score, vid, m) for vid, ms in matches for m in ms), key=lambda t: (-t[0], t[1], t[2].section_id))
    taken: dict[str, int] = {}
    out: list[tuple[str, Match]] = []
    for _, vid, m in flat:
        if room.get(m.section_id, 0) <= 0 or taken.get(vid, 0) >= MAX_PER_VIDEO:
            continue
        room[m.section_id] -= 1
        taken[vid] = taken.get(vid, 0) + 1
        out.append((vid, m))
    return out


async def run(
    db: Database,
    api: YouTubeApi,
    embedder: Embedder,
    rules: YoutubeRules,
    channels: Sequence[Channel],
    *,
    now: dt.datetime | None = None,
) -> Report:
    rep = Report(channels=len(channels))
    at = now or dt.datetime.now(dt.UTC)
    try:
        await _refresh(db, api, rules, at, rep)
        await _discover(db, api, embedder, rules, channels, at, rep)
    except QuotaExceeded:
        rep.stopped_early = True
        log.warning("youtube_map_quota_stop")
    except YouTubeError as exc:
        rep.errors.append(str(exc))
        rep.stopped_early = True
        log.warning("youtube_map_api_error", error=str(exc))
    rep.quota_used = api.quota.used
    return rep


async def _refresh(db: Database, api: YouTubeApi, rules: YoutubeRules, at: dt.datetime, rep: Report) -> None:
    cutoff = at - dt.timedelta(days=rules.refresh_days)
    async with service_tx(db, timeout_ms=15_000) as conn:
        cur = await conn.execute(
            "select id, youtube_id from public.media_assets where kind = 'video_youtube' and youtube_id is not null"
            " and metadata_fetched_at is not null and metadata_fetched_at < %s order by metadata_fetched_at limit 2000",
            (cutoff,),
        )
        due = [dict(r) for r in await cur.fetchall()]
    for i in range(0, len(due), 50):
        chunk = due[i : i + 50]
        items = await asyncio.to_thread(api.videos, [r["youtube_id"] for r in chunk])
        async with service_tx(db, timeout_ms=15_000) as conn:
            for r in chunk:
                item = items.get(r["youtube_id"])
                if item is not None and keep(item):
                    await _update_metadata(conn, r["id"], item, at)
                    rep.refreshed += 1
                else:
                    await drop_video(conn, r["id"], at)
                    rep.dropped += 1


async def _update_metadata(conn: psycopg.AsyncConnection[Any], mid: str, item: dict[str, Any], at: dt.datetime) -> None:
    sn, cd = item.get("snippet") or {}, item.get("contentDetails") or {}
    title = str(sn.get("title") or "")[:300] or None
    await conn.execute(
        "update public.media_assets set title = case when review_status = 'rejected' then null else %s end,"
        " duration_s = %s, embeddable = true, metadata_fetched_at = %s,"
        " attribution = case when review_status = 'rejected' then null else %s end where id = %s",
        (
            title,
            iso_duration_s(cd.get("duration")),
            at,
            ATTRIBUTION.format(title=title, channel=sn.get("channelTitle") or "") if title else None,
            mid,
        ),
    )


async def drop_video(conn: psycopg.AsyncConnection[Any], mid: str, at: dt.datetime) -> None:
    """A video that is gone, private or no longer embeddable: unlink, reject, forget its metadata."""
    await conn.execute("delete from public.section_media where media_id = %s", (mid,))
    await conn.execute(
        "update public.media_assets set review_status = 'rejected', title = null, caption = null, attribution = null,"
        " duration_s = null, embeddable = false, metadata_fetched_at = null where id = %s",
        (mid,),
    )
    await conn.execute(
        "update public.video_candidates set status = 'stale', reviewed_at = coalesce(reviewed_at, %s)"
        " where media_id = %s and status in ('pending', 'approved')",
        (at, mid),
    )


async def _discover(
    db: Database,
    api: YouTubeApi,
    embedder: Embedder,
    rules: YoutubeRules,
    channels: Sequence[Channel],
    at: dt.datetime,
    rep: Report,
) -> None:
    if not rules.enabled or not channels:
        return
    async with service_tx(db) as conn:
        sections = await load_sections(conn)
        cur = await conn.execute(
            "select section_id, count(*)::int as n from public.video_candidates where status = 'pending'"
            " and section_id is not null group by 1"
        )
        pending = {r["section_id"]: r["n"] for r in await cur.fetchall()}
    if not sections:
        return
    room = {s.id: max(0, rules.max_candidates_per_section - pending.get(s.id, 0)) for s in sections}
    for ch in channels:
        playlist = uploads_playlist(ch.channel_id)
        if playlist is None:
            continue
        ids = await asyncio.to_thread(api.playlist_video_ids, playlist, rules.max_new_per_channel)
        rep.listed += len(ids)
        async with service_tx(db) as conn:
            cur = await conn.execute("select youtube_id from public.media_assets where youtube_id = any(%s)", (ids,))
            known = {r["youtube_id"] for r in await cur.fetchall()}
        fresh = [i for i in ids if i not in known]
        if not fresh:
            continue
        items = await asyncio.to_thread(api.videos, fresh)
        good = [items[i] for i in fresh if i in items and keep(items[i])]
        rep.new_videos += len(good)
        if not good:
            continue
        texts = [
            ((v.get("snippet") or {}).get("title") or "", (v.get("snippet") or {}).get("description") or "")
            for v in good
        ]
        scored = await asyncio.to_thread(
            score_videos, embedder, sections, texts, ch.subjects, min_score=rules.min_score, per_video=MAX_PER_VIDEO
        )
        chosen = pick([(v["id"], m) for v, m in zip(good, scored, strict=True)], room)
        by_id = {v["id"]: v for v in good}
        taxo = {s.id: s.taxonomy_ids for s in sections}
        async with service_tx(db) as conn:
            for vid in dict.fromkeys(v for v, _ in chosen):
                await _insert_media(conn, by_id[vid], ch, at)
            for vid, m in chosen:
                cur = await conn.execute(
                    "insert into public.video_candidates (media_id, section_id, taxonomy_ids, score, matched_terms)"
                    " values (%s, %s, %s, %s, %s) on conflict do nothing",
                    (media_id(vid), m.section_id, list(taxo.get(m.section_id, ())), m.score, list(m.terms)),
                )
                rep.candidates_added += cur.rowcount


async def _insert_media(conn: psycopg.AsyncConnection[Any], item: dict[str, Any], ch: Channel, at: dt.datetime) -> None:
    sn, cd = item.get("snippet") or {}, item.get("contentDetails") or {}
    title = str(sn.get("title") or "")[:300] or None
    await conn.execute(
        "insert into public.media_assets (id, kind, source_id, title, youtube_id, channel_id, duration_s, embeddable,"
        " metadata_fetched_at, attribution, review_status)"
        " values (%s, 'video_youtube', 'youtube', %s, %s, %s, %s, true, %s, %s, 'unreviewed')"
        " on conflict (id) do nothing",
        (
            media_id(item["id"]),
            title,
            item["id"],
            sn.get("channelId") or ch.channel_id,
            iso_duration_s(cd.get("duration")),
            at,
            ATTRIBUTION.format(title=title, channel=sn.get("channelTitle") or ch.name) if title else None,
        ),
    )
