"""Videos routes: browse approved videos by step / subject / topic, search, and the student's own watched list.

Everything runs as the caller (RLS): a video that is not approved is invisible, so it is never listed, counted or
accepted as watched. "Watched" is a plain checklist: no points, streaks or rewards.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import JSONResponse, Response

from ..auth import CurrentUser
from ..auth_limits import RateLimiter
from ..db import Database
from ..errors import ErrorResponse, error_response
from ..plan.planner_routes import limited
from ..qbank.routes import as_user
from .models import (
    ID_PATTERN,
    ChannelCount,
    SubjectCounts,
    VideoOut,
    VideosOut,
    VideosOverview,
    VideoTopicOut,
    VideoTopicsOut,
)
from .query import CH, COMMONS, ITEM_COLS, ITEM_FROM, names_for, search_filter

router = APIRouter(prefix="/api/videos", tags=["videos"])
NO_STORE = {"cache-control": "private, no-store"}

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Database unavailable"},
}
_INVALID: dict[int | str, dict[str, Any]] = {422: {"model": ErrorResponse, "description": "Invalid query"}}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse, "description": "Not found"}}

_OVERVIEW = """
with vis as (
  select c.media_id, c.step, c.subject, (w.media_id is not null) as watched
  from public.video_catalog c
  left join public.video_watches w on w.media_id = c.media_id and w.user_id = (select auth.uid())
), v as (
  select step, subject, count(*)::int as videos, (count(*) filter (where watched))::int as watched
  from vis group by step, subject
), t as (
  select t.step, t.subject, min(t.sort) as first_sort, count(*)::int as topics,
         (count(*) filter (where d))::int as direct, (count(*) filter (where not d and ch))::int as chapter
  from (
    select t.step, t.subject, t.sort,
           exists (select 1 from public.video_catalog c where c.media_id = any(t.media_ids)) as d,
           exists (select 1 from public.video_catalog c where c.media_id = any(t.chapter_media_ids)) as ch
    from public.video_topics t
  ) t group by t.step, t.subject
)
select coalesce(v.step, t.step) as step, coalesce(v.subject, t.subject) as subject, coalesce(v.videos, 0) as videos,
       coalesce(t.topics, 0) as topics, coalesce(t.direct, 0) as topics_direct,
       coalesce(t.chapter, 0) as topics_chapter, coalesce(v.watched, 0) as watched
from v full join t on t.step = v.step and t.subject = v.subject
order by 1, t.first_sort nulls last, 2
"""

_CHANNELS = f"""
select {CH} as id, count(*)::int as videos
from public.video_catalog c join public.media_assets m on m.id = c.media_id
where c.provider = 'commons' or m.channel_id is not null
group by 1 order by 2 desc, 1
"""  # noqa: S608 - module constant only

_TOPICS = """
select t.id, t.title, t.subject, t.unit, t.chapter, t.step, t.core,
       array(select c.media_id from public.video_catalog c where c.media_id = any(t.media_ids)
             order by c.sort, c.media_id) as media_ids,
       array(select c.media_id from public.video_catalog c where c.media_id = any(t.chapter_media_ids)
             order by c.sort, c.media_id) as chapter_media_ids
from public.video_topics t
where (%(step)s::text is null or t.step = %(step)s) and (%(subject)s::text is null or t.subject = %(subject)s)
order by t.step, t.sort, t.id
"""

_MARK = """
insert into public.video_watches (user_id, media_id) values ((select auth.uid()), %(id)s)
on conflict (user_id, media_id) do nothing
"""
_UNMARK = "delete from public.video_watches where user_id = (select auth.uid()) and media_id = %(id)s"


def default_limiters() -> dict[str, RateLimiter]:
    return {"videos_read": RateLimiter(240, 60.0), "videos_write": RateLimiter(120, 60.0)}


def _names(request: Request) -> dict[str, str]:
    return names_for(request.app.state.settings.config_dir)


def _db(request: Request) -> Database:
    d: Database = request.app.state.db
    return d


async def _rows(conn: psycopg.AsyncConnection[Any], query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    cur = await conn.execute(query, params)
    return [dict(r) for r in await cur.fetchall()]


def _video(r: dict[str, Any], names: dict[str, str]) -> VideoOut:
    commons = r["provider"] == COMMONS
    cid = r["channel_id"]
    return VideoOut(
        **{k: r[k] for k in ("media_id", "provider", "page_url", "licence", "title", "duration_s", "style")},
        **{k: r[k] for k in ("subject", "step", "core", "attribution", "watched", "topics", "channel_id")},
        youtube_id=None if commons else r["youtube_id"],
        file_url=r["file_url"] if commons else None,
        channel_name=names.get(cid or (COMMONS if commons else "")),
    )


@router.get(
    "",
    operation_id="listVideos",
    summary="Browse and search the approved videos (filters combine; ranked by topic, title, chapter, channel match)",
    response_model=VideosOut,
    responses={**_ERRORS, **_INVALID},
)
async def list_videos(
    request: Request,
    user: Annotated[CurrentUser, limited("videos_read")],
    step: Annotated[Literal["step1", "step2ck", "all"], Query()] = "all",
    subject: Annotated[str | None, Query(max_length=80)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    channel: Annotated[str | None, Query(max_length=80, description="A channel id, or 'commons'")] = None,
    style: Annotated[Literal["sketch", "whiteboard", "animation", "lecture"] | None, Query()] = None,
    unwatched: bool = False,
    core: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 60,
    cursor: Annotated[str | None, Query(pattern=r"^\d{1,7}$", description="Offset from a previous `next`")] = None,
) -> JSONResponse:
    names = _names(request)
    where, rank, params = search_filter(q or "", names)
    offset = int(cursor or 0)
    params |= {"step": None if step == "all" else step, "subject": subject, "channel": channel, "style": style}
    params |= {"limit": limit, "offset": offset}
    sql = (
        f"select {ITEM_COLS}, {rank} as rank {ITEM_FROM} where ({where})"
        " and (%(step)s::text is null or c.step = %(step)s) and (%(subject)s::text is null or c.subject = %(subject)s)"
        " and (%(style)s::text is null or c.style = %(style)s)"
        f" and (%(channel)s::text is null or {CH} = %(channel)s)"
        + (" and w.media_id is null" if unwatched else "")
        + (" and c.core" if core else "")
        + " order by rank, c.step, c.subject, c.sort, c.media_id limit %(limit)s offset %(offset)s"
    )

    async def run(conn: psycopg.AsyncConnection[Any]) -> list[dict[str, Any]]:
        return await _rows(conn, sql, params)

    rows = await as_user(_db(request), user, run)
    total = rows[0]["total"] if rows else 0
    nxt = str(offset + limit) if offset + limit < total else None
    out = VideosOut(items=[_video(r, names) for r in rows], next=nxt, total=total)
    return JSONResponse(out.model_dump(mode="json"), headers=NO_STORE)


@router.get(
    "/overview",
    operation_id="getVideosOverview",
    summary="Counts per step and subject (videos, topics, topics covered directly or by chapter, watched) and channels",
    response_model=VideosOverview,
    responses=_ERRORS,
)
async def get_overview(request: Request, user: Annotated[CurrentUser, limited("videos_read")]) -> JSONResponse:
    async def run(conn: psycopg.AsyncConnection[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return await _rows(conn, _OVERVIEW, {}), await _rows(conn, _CHANNELS, {})

    subjects, channels = await as_user(_db(request), user, run)
    names = _names(request)
    out = VideosOverview(
        subjects=[SubjectCounts(**s) for s in subjects],
        channels=[ChannelCount(id=c["id"], name=names.get(c["id"], c["id"]), videos=c["videos"]) for c in channels],
    )
    return JSONResponse(out.model_dump(mode="json"), headers=NO_STORE)


@router.get(
    "/topics",
    operation_id="listVideoTopics",
    summary="The topic map of a step/subject with its video ids and coverage (topic, chapter or gap)",
    response_model=VideoTopicsOut,
    responses={**_ERRORS, **_INVALID},
)
async def list_topics(
    request: Request,
    user: Annotated[CurrentUser, limited("videos_read")],
    step: Annotated[Literal["step1", "step2ck"] | None, Query()] = None,
    subject: Annotated[str | None, Query(max_length=80)] = None,
) -> JSONResponse:
    async def run(conn: psycopg.AsyncConnection[Any]) -> list[dict[str, Any]]:
        return await _rows(conn, _TOPICS, {"step": step, "subject": subject})

    rows = await as_user(_db(request), user, run)
    items = [
        VideoTopicOut(**r, coverage="topic" if r["media_ids"] else "chapter" if r["chapter_media_ids"] else "gap")
        for r in rows
    ]
    return JSONResponse(VideoTopicsOut(items=items).model_dump(mode="json"), headers=NO_STORE)


_MediaId = Annotated[str, PathParam(pattern=ID_PATTERN)]


@router.put(
    "/{media_id}/watched",
    operation_id="markVideoWatched",
    summary="Tick a video off as watched (no-op when it already is)",
    status_code=204,
    responses={**_ERRORS, **_NOT_FOUND, **_INVALID},
)
async def mark_watched(
    request: Request, media_id: _MediaId, user: Annotated[CurrentUser, limited("videos_write")]
) -> Response:
    async def run(conn: psycopg.AsyncConnection[Any]) -> bool:
        try:
            async with conn.transaction():
                await conn.execute(_MARK, {"id": media_id})
        except (psycopg.errors.InsufficientPrivilege, psycopg.errors.ForeignKeyViolation):
            return False  # hidden (not approved), not a catalogue video, or unknown: indistinguishable
        return True

    if not await as_user(_db(request), user, run):
        return error_response(404, "This video does not exist or is not available to you.", "video_not_found")
    return Response(status_code=204)


@router.delete(
    "/{media_id}/watched",
    operation_id="unmarkVideoWatched",
    summary="Clear the watched tick (no-op when it is not set)",
    status_code=204,
    responses={**_ERRORS, **_INVALID},
)
async def unmark_watched(
    request: Request, media_id: _MediaId, user: Annotated[CurrentUser, limited("videos_write")]
) -> Response:
    async def run(conn: psycopg.AsyncConnection[Any]) -> None:
        await conn.execute(_UNMARK, {"id": media_id})

    await as_user(_db(request), user, run)
    return Response(status_code=204)


def install_videos_routes(app: Any) -> None:
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
    app.include_router(router)
