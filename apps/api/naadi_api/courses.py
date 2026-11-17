"""Courses map, chapter reader, reading positions and "Report an issue" (segment 06).

Every query runs as the signed-in user inside `user_tx` (role `authenticated` + their claims), so Postgres
RLS decides what exists: published shared chapters, the caller's own private chapters, approved media,
the client-readable question columns (never answers or explanations) and the caller's own positions.
The service role is used only to sign private Storage URLs (`chapters_media.py`).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException

from .auth import CurrentUser, get_account_user
from .auth_db import user_tx
from .auth_limits import RateLimiter
from .chapters_content import ContentScan, scan_body
from .chapters_media import MediaUrlResolver
from .db import Database, DatabaseNotConfigured
from .errors import ErrorResponse
from .logs import get_logger

log = get_logger("naadi.courses")

router = APIRouter(prefix="/api", tags=["courses"])

# A chapter is listed under a system when at least this share of its questions (and at least
# SYSTEM_MIN_QUESTIONS of them) are tagged with that system, or when one of its sections is tagged with it.
# Single questions tagged elsewhere (a cardiology item in a biochemistry chapter) do not move the chapter.
SYSTEM_MIN_SHARE = 0.10
SYSTEM_MIN_QUESTIONS = 3
WORDS_PER_MINUTE = 200
MAX_POSITION = 100_000.0
# Clock skew we accept from a device before clamping its timestamp to the server clock.
FUTURE_SKEW = dt.timedelta(seconds=5)

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}$"
EXAM_PATTERN = r"^[a-z0-9]{2,16}$"
TAXONOMY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,127}$"

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing, expired or invalid access token"},
    429: {"model": ErrorResponse, "description": "Rate limited"},
    503: {"model": ErrorResponse, "description": "Database unavailable"},
}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse, "description": "Not found or not visible"}}

Row = dict[str, Any]

# ── Models ──


class Coverage(BaseModel):
    """What exists today for a chapter or a system (counts of items visible to the caller)."""

    sections: int
    reading_minutes: int
    figures: int
    videos: int
    questions: int
    cards: int


class ChapterProgress(BaseModel):
    sections_read: int
    sections_total: int
    status: Literal["not_started", "in_progress", "done"]
    last_section_id: str | None
    last_read_at: dt.datetime | None


class CourseChapter(BaseModel):
    id: str
    exam: str
    kind: Literal["chapter", "user_upload", "user_kb"]
    title: str
    meta: str | None
    number: int | None = Field(description="Position among the exam's shared chapters (1-based); null for own files")
    systems: list[str] = Field(description="Taxonomy system ids this chapter is listed under")
    format: Literal["read", "read_watch"] = Field(description="read_watch when at least one video is attached")
    coverage: Coverage
    progress: ChapterProgress


class CourseSystem(BaseModel):
    id: str
    label: str
    chapter_ids: list[str]
    coverage: Coverage
    sections_read: int
    sections_total: int


class ExamOption(BaseModel):
    id: str
    label: str
    chapter_count: int


class NextUp(BaseModel):
    """The one next action on the course map (the lime CTA)."""

    reason: Literal["continue", "start", "next"]
    chapter_id: str
    chapter_title: str
    section_id: str
    section_heading: str


class CoursesResponse(BaseModel):
    exam: str | None = Field(description="The exam shown (the query, else the profile's target, else the first)")
    exams: list[ExamOption]
    system: str | None = Field(description="The system filter applied, if any")
    systems: list[CourseSystem] = Field(description="Every system of the exam with at least one chapter")
    chapters: list[CourseChapter] = Field(description="Chapters of the exam (and the caller's own), filtered")
    next_up: NextUp | None


class SystemRef(BaseModel):
    id: str
    label: str


class SectionOut(BaseModel):
    id: str
    sort: int
    heading: str
    reading_minutes: int
    taxonomy_ids: list[str]
    body: dict[str, Any] | None = Field(description="TipTap JSON (schema: @naadi/shared/content); null if empty")
    attached_media: list[str] = Field(
        default_factory=list,
        description="The caller's own figures attached to this section (ids in `media`; shown after the body)",
    )


class MediaSource(BaseModel):
    id: str
    name: str
    license: str
    url: str | None


class MediaOut(BaseModel):
    id: str
    kind: Literal["svg", "image", "video_own", "video_youtube", "audio", "pdf", "link"]
    url: str | None = Field(description="Browser URL (public or signed); null when it cannot be served")
    title: str | None
    caption: str | None
    attribution: str | None
    width: int | None
    height: int | None
    youtube_id: str | None
    duration_s: int | None
    embeddable: bool | None
    source: MediaSource | None


class QuestionOption(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    html: str


class EmbeddedQuestion(BaseModel):
    """A question without its answer, explanation or statistics (answering arrives with segment 08)."""

    id: str
    stem_html: str
    options: list[QuestionOption]


class ChapterLink(BaseModel):
    id: str
    title: str


class ChapterDetail(BaseModel):
    id: str
    exam: str
    kind: Literal["chapter", "user_upload", "user_kb"]
    title: str
    meta: str | None
    number: int | None
    chapter_count: int = Field(description="Shared chapters in this exam")
    systems: list[SystemRef]
    coverage: Coverage
    sections: list[SectionOut]
    media: dict[str, MediaOut] = Field(description="Every visible media asset the sections reference, by id")
    questions: dict[str, EmbeddedQuestion] = Field(description="Questions embedded in the sections, by id")
    next_chapter: ChapterLink | None


class ReadingPositionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str = Field(pattern=ID_PATTERN)
    section_id: str = Field(pattern=ID_PATTERN)
    offset: float = Field(
        ge=0,
        le=MAX_POSITION,
        description="Index of the top-most visible top-level block plus the fraction of it scrolled past",
    )
    completed: bool = Field(default=False, description="The reader reached the end of the section (never unset)")
    client_updated_at: dt.datetime | None = Field(
        default=None, description="When the device recorded it (orders offline writes); clamped to the server clock"
    )

    @field_validator("offset")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("offset must be a finite number")
        return round(v, 4)

    @field_validator("client_updated_at")
    @classmethod
    def _aware(cls, v: dt.datetime | None) -> dt.datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("client_updated_at needs a time zone")
        return v


class ReadingPosition(BaseModel):
    chapter_id: str
    section_id: str
    offset: float
    completed: bool
    client_updated_at: dt.datetime
    updated_at: dt.datetime


class ReadingPositionWrite(BaseModel):
    position: ReadingPosition = Field(description="The stored position after the write")
    applied: bool = Field(description="false when a newer position from another device was kept")


class ChapterPositions(BaseModel):
    chapter_id: str
    last: ReadingPosition | None = Field(description="Most recent position in this chapter, from any device")
    sections: list[ReadingPosition]


ReportRefType = Literal["section", "chapter", "media", "question"]


class ReportIssueIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_type: ReportRefType
    ref_id: str = Field(pattern=ID_PATTERN)
    body: str = Field(min_length=1, max_length=4000)

    @field_validator("body")
    @classmethod
    def _text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Describe the issue in a few words.")
        return v


class ReportIssueOut(BaseModel):
    id: uuid.UUID
    status: Literal["open", "triaged", "resolved", "dismissed"]
    created_at: dt.datetime


# ── Plumbing ──


def _db(request: Request) -> Database:
    d: Database = request.app.state.db
    return d


def _resolver(request: Request) -> MediaUrlResolver:
    r: MediaUrlResolver = request.app.state.media_resolver
    return r


def limited(bucket: str) -> Any:
    async def dep(request: Request, user: Annotated[CurrentUser, Depends(get_account_user)]) -> CurrentUser:
        limiters: dict[str, RateLimiter] = request.app.state.rate_limiters
        limiters[bucket].hit(str(user.user_id))
        return user

    return Depends(dep)


UNAVAILABLE = "The library cannot be reached right now. Try again in a moment."


async def as_user[T](db: Database, user: CurrentUser, fn: Callable[[psycopg.AsyncConnection[Any]], Awaitable[T]]) -> T:
    try:
        async with user_tx(db, user, deadline_s=8.0) as conn:
            return await fn(conn)
    except DatabaseNotConfigured as exc:
        raise HTTPException(503, UNAVAILABLE) from exc
    except (psycopg.OperationalError, TimeoutError) as exc:
        log.warning("courses_db_unavailable", error=type(exc).__name__)
        raise HTTPException(503, UNAVAILABLE) from exc


async def _rows(
    conn: psycopg.AsyncConnection[Any], query: Any, params: Sequence[Any] | dict[str, Any] = ()
) -> list[Row]:
    cur = await conn.execute(query, params)
    return [dict(r) for r in await cur.fetchall()]


# Section reading time: the stored value, else words of the HTML fallback / 200 (at least 1 minute).
_MINUTES_SQL = (
    "coalesce(s.reading_minutes, greatest(1, ceil(coalesce(array_length(regexp_split_to_array(btrim("
    "regexp_replace(coalesce(s.body_html, ''), '<[^>]*>', ' ', 'g')), '\\s+'), 1), 0) / "
    f"{WORDS_PER_MINUTE}.0)))::int"
)


def _coverage(
    sections: int = 0, minutes: int = 0, figures: int = 0, videos: int = 0, q: int = 0, c: int = 0
) -> Coverage:
    return Coverage(sections=sections, reading_minutes=minutes, figures=figures, videos=videos, questions=q, cards=c)


def chapter_systems(question_total: int, per_system: dict[str, int], section_systems: set[str]) -> list[str]:
    """The systems a chapter is listed under (see SYSTEM_MIN_SHARE)."""
    need = max(SYSTEM_MIN_QUESTIONS, math.ceil(SYSTEM_MIN_SHARE * question_total))
    picked = {sid for sid, n in per_system.items() if n >= need} | section_systems
    # Most questions first, then alphabetical, so the order is stable.
    return sorted(picked, key=lambda sid: (-per_system.get(sid, 0), sid))


def _position(r: Row) -> ReadingPosition:
    return ReadingPosition(
        chapter_id=r["chapter_id"],
        section_id=r["section_id"],
        offset=float(r["scroll_position"]),
        completed=r["completed_at"] is not None,
        client_updated_at=r["client_updated_at"],
        updated_at=r["updated_at"],
    )


def next_section(order: list[str], completed: set[str], last: str | None) -> str | None:
    """Where to resume a chapter: the last section if unfinished, else the next unfinished one after it,
    else the first unfinished one. None when every section is complete."""
    if last in order and last not in completed:
        return last
    start = order.index(last) + 1 if last in order else 0
    for sid in order[start:] + order[:start]:
        if sid not in completed:
            return sid
    return None


# ── Queries ──

_Q_EXAMS = """
select c.exam as id, coalesce(t.label, c.exam) as label, coalesce(t.sort, 999) as sort,
       count(*) filter (where c.kind = 'chapter') as chapter_count
from public.chapters c
left join public.taxonomy_nodes t on t.id = c.exam and t.kind = 'exam'
where c.owner_user_id is null and c.status = 'published' and c.kind = 'chapter'
group by 1, 2, 3
order by 3, 1
"""

_Q_CHAPTERS = """
select id, exam, kind, title, meta, sort, owner_user_id is not null as own
from public.chapters
where status <> 'retired' and kind in ('chapter', 'user_upload', 'user_kb')
  and (exam = %(exam)s or owner_user_id = (select auth.uid()))
order by (owner_user_id is not null), sort nulls last, id
"""

_Q_SECTIONS = f"""
select s.chapter_id, s.id, s.sort, s.heading, {_MINUTES_SQL} as minutes, s.taxonomy_ids
from public.chapter_sections s
where s.chapter_id = any(%(ids)s)
order by s.chapter_id, s.sort, s.id
"""  # noqa: S608 - module constants only

_Q_MEDIA_COUNTS = """
select s.chapter_id, m.kind, count(*)::int as n
from public.section_media sm
join public.chapter_sections s on s.id = sm.section_id
join public.media_assets m on m.id = sm.media_id
where s.chapter_id = any(%(ids)s)
group by 1, 2
"""

_Q_QUESTION_COUNTS = """
select chapter_id, count(*)::int as n from public.questions where chapter_id = any(%(ids)s) group by 1
"""

_Q_QUESTION_SYSTEMS = """
select q.chapter_id, t.id as system_id, count(*)::int as n
from public.questions q
cross join lateral unnest(q.taxonomy_ids) as tid(id)
join public.taxonomy_nodes t on t.id = tid.id and t.kind = 'system'
where q.chapter_id = any(%(ids)s)
group by 1, 2
"""

_Q_CARD_COUNTS = """
select coalesce(s.chapter_id, q.chapter_id) as chapter_id, count(*)::int as n
from public.flashcards f
left join public.chapter_sections s on s.id = f.section_id
left join public.questions q on q.id = f.question_id
where coalesce(s.chapter_id, q.chapter_id) = any(%(ids)s)
group by 1
"""

_Q_SYSTEM_NODES = """
select id, label from public.taxonomy_nodes where kind = 'system' and id = any(%(ids)s) order by sort, label
"""

_Q_POSITIONS = """
select chapter_id, section_id, scroll_position, completed_at, client_updated_at, updated_at
from public.reading_positions
where chapter_id = any(%(ids)s)
order by client_updated_at desc
"""


class _ChapterStats:
    """Per-chapter aggregates for a set of chapter ids, all read under the caller's RLS."""

    def __init__(self) -> None:
        self.sections: dict[str, list[Row]] = defaultdict(list)
        self.media: dict[str, dict[str, int]] = defaultdict(dict)
        self.questions: dict[str, int] = {}
        self.question_systems: dict[str, dict[str, int]] = defaultdict(dict)
        self.cards: dict[str, int] = {}
        self.positions: dict[str, list[Row]] = defaultdict(list)

    @classmethod
    async def load(cls, conn: psycopg.AsyncConnection[Any], ids: list[str], *, positions: bool = True) -> _ChapterStats:
        st = cls()
        p = {"ids": ids}
        for r in await _rows(conn, _Q_SECTIONS, p):
            st.sections[r["chapter_id"]].append(r)
        for r in await _rows(conn, _Q_MEDIA_COUNTS, p):
            st.media[r["chapter_id"]][r["kind"]] = r["n"]
        for r in await _rows(conn, _Q_QUESTION_COUNTS, p):
            st.questions[r["chapter_id"]] = r["n"]
        for r in await _rows(conn, _Q_QUESTION_SYSTEMS, p):
            st.question_systems[r["chapter_id"]][r["system_id"]] = r["n"]
        for r in await _rows(conn, _Q_CARD_COUNTS, p):
            st.cards[r["chapter_id"]] = r["n"]
        if positions:
            for r in await _rows(conn, _Q_POSITIONS, p):
                st.positions[r["chapter_id"]].append(r)
        return st

    def coverage(self, cid: str) -> Coverage:
        secs = self.sections.get(cid, [])
        media = self.media.get(cid, {})
        return _coverage(
            sections=len(secs),
            minutes=sum(int(s["minutes"]) for s in secs),
            figures=media.get("svg", 0) + media.get("image", 0),
            videos=media.get("video_youtube", 0) + media.get("video_own", 0),
            q=self.questions.get(cid, 0),
            c=self.cards.get(cid, 0),
        )

    def systems(self, cid: str, system_ids: set[str]) -> list[str]:
        section_systems = {t for s in self.sections.get(cid, []) for t in s["taxonomy_ids"] if t in system_ids}
        per = {k: v for k, v in self.question_systems.get(cid, {}).items() if k in system_ids}
        return chapter_systems(self.questions.get(cid, 0), per, section_systems)

    def all_system_ids(self) -> set[str]:
        ids = {sid for per in self.question_systems.values() for sid in per}
        return ids | {t for secs in self.sections.values() for s in secs for t in s["taxonomy_ids"]}

    def progress(self, cid: str) -> ChapterProgress:
        order = [s["id"] for s in self.sections.get(cid, [])]
        rows = [r for r in self.positions.get(cid, []) if r["section_id"] in order]
        done = {r["section_id"] for r in rows if r["completed_at"] is not None}
        last = rows[0] if rows else None
        total = len(order)
        status: Literal["not_started", "in_progress", "done"] = (
            "done" if total and len(done) == total else "in_progress" if rows else "not_started"
        )
        return ChapterProgress(
            sections_read=len(done),
            sections_total=total,
            status=status,
            last_section_id=last["section_id"] if last else None,
            last_read_at=last["client_updated_at"] if last else None,
        )

    def resume(self, cid: str) -> str | None:
        order = [s["id"] for s in self.sections.get(cid, [])]
        rows = [r for r in self.positions.get(cid, []) if r["section_id"] in order]
        done = {r["section_id"] for r in rows if r["completed_at"] is not None}
        return next_section(order, done, rows[0]["section_id"] if rows else None)

    def heading(self, cid: str, sid: str) -> str:
        return next((s["heading"] for s in self.sections.get(cid, []) if s["id"] == sid), "")


def _next_up(chapters: list[Row], st: _ChapterStats) -> NextUp | None:
    readable = [c for c in chapters if c["kind"] != "mock_exam" and st.sections.get(c["id"])]
    if not readable:
        return None
    by_id = {c["id"]: c for c in readable}
    latest: Row | None = None
    for c in readable:
        rows = st.positions.get(c["id"], [])
        if rows and (latest is None or rows[0]["client_updated_at"] > latest["client_updated_at"]):
            latest = rows[0]
    if latest is None:
        first = readable[0]
        sid = st.sections[first["id"]][0]["id"]
        return NextUp(
            reason="start",
            chapter_id=first["id"],
            chapter_title=first["title"],
            section_id=sid,
            section_heading=st.heading(first["id"], sid),
        )
    cid = latest["chapter_id"]
    sid = st.resume(cid)
    if sid is not None:
        return NextUp(
            reason="continue",
            chapter_id=cid,
            chapter_title=by_id[cid]["title"],
            section_id=sid,
            section_heading=st.heading(cid, sid),
        )
    # That chapter is finished: the first unfinished chapter after it (wrapping around).
    ids = [c["id"] for c in readable]
    start = ids.index(cid) + 1
    for other in ids[start:] + ids[:start]:
        nsid = st.resume(other)
        if nsid is not None:
            return NextUp(
                reason="next",
                chapter_id=other,
                chapter_title=by_id[other]["title"],
                section_id=nsid,
                section_heading=st.heading(other, nsid),
            )
    return None


def _number_map(chapters: list[Row]) -> dict[str, int]:
    shared = [c for c in chapters if c["kind"] == "chapter" and not c["own"]]
    return {c["id"]: i + 1 for i, c in enumerate(shared)}


# ── Routes: courses ──


@router.get(
    "/courses",
    operation_id="getCourses",
    summary="Course map: chapters by system with coverage and the caller's progress, plus the next action",
    response_model=CoursesResponse,
    responses=_ERRORS,
)
async def get_courses(
    request: Request,
    user: Annotated[CurrentUser, limited("courses_read")],
    exam: Annotated[str | None, Query(pattern=EXAM_PATTERN, description="Exam id, e.g. step1, step2ck")] = None,
    system: Annotated[str | None, Query(pattern=TAXONOMY_PATTERN, description="Taxonomy system id")] = None,
) -> CoursesResponse:
    async def run(conn: psycopg.AsyncConnection[Any]) -> CoursesResponse:
        exams = [
            ExamOption(id=r["id"], label=r["label"], chapter_count=r["chapter_count"])
            for r in await _rows(conn, _Q_EXAMS)
        ]
        chosen = exam
        if chosen is None:
            prof = await _rows(conn, "select exam_target from public.profiles where user_id = (select auth.uid())")
            target = prof[0]["exam_target"] if prof else None
            ids = [e.id for e in exams]
            chosen = target if target in ids else (ids[0] if ids else None)
        if chosen is None:
            return CoursesResponse(exam=None, exams=exams, system=system, systems=[], chapters=[], next_up=None)
        rows = await _rows(conn, _Q_CHAPTERS, {"exam": chosen})
        ids = [r["id"] for r in rows]
        st = await _ChapterStats.load(conn, ids)
        nodes = await _rows(conn, _Q_SYSTEM_NODES, {"ids": sorted(st.all_system_ids())})
        system_ids = {n["id"] for n in nodes}
        numbers = _number_map(rows)

        readable = [r for r in rows if r["kind"] != "mock_exam"]
        chapters: list[CourseChapter] = []
        for r in readable:
            cov = st.coverage(r["id"])
            chapters.append(
                CourseChapter(
                    id=r["id"],
                    exam=r["exam"],
                    kind=r["kind"],
                    title=r["title"],
                    meta=r["meta"],
                    number=numbers.get(r["id"]),
                    systems=st.systems(r["id"], system_ids),
                    format="read_watch" if cov.videos else "read",
                    coverage=cov,
                    progress=st.progress(r["id"]),
                )
            )
        systems: list[CourseSystem] = []
        for n in nodes:
            members = [c for c in chapters if n["id"] in c.systems]
            if not members:
                continue
            systems.append(
                CourseSystem(
                    id=n["id"],
                    label=n["label"],
                    chapter_ids=[c.id for c in members],
                    coverage=_coverage(
                        sections=sum(c.coverage.sections for c in members),
                        minutes=sum(c.coverage.reading_minutes for c in members),
                        figures=sum(c.coverage.figures for c in members),
                        videos=sum(c.coverage.videos for c in members),
                        q=sum(c.coverage.questions for c in members),
                        c=sum(c.coverage.cards for c in members),
                    ),
                    sections_read=sum(c.progress.sections_read for c in members),
                    sections_total=sum(c.progress.sections_total for c in members),
                )
            )
        listed = [c for c in chapters if system is None or system in c.systems]
        return CoursesResponse(
            exam=chosen,
            exams=exams,
            system=system,
            systems=systems,
            chapters=listed,
            next_up=_next_up(readable, st),
        )

    return await as_user(_db(request), user, run)


# ── Routes: chapter ──

# Mock exams are Practice content (their instruction section is a blueprint for timed blocks, and mock
# questions are answered there); pathway pages have their own screen. Neither is listed or readable here.
_Q_CHAPTER = """
select id, exam, kind, title, meta, sort, owner_user_id is not null as own
from public.chapters
where id = %(id)s and status <> 'retired' and kind in ('chapter', 'user_upload', 'user_kb')
"""

_Q_SHARED_ORDER = """
select id, title from public.chapters
where exam = %(exam)s and kind = 'chapter' and owner_user_id is null and status = 'published'
order by sort nulls last, id
"""

_Q_SECTIONS_FULL = f"""
select s.id, s.sort, s.heading, {_MINUTES_SQL} as minutes, s.taxonomy_ids, s.body
from public.chapter_sections s where s.chapter_id = %(id)s order by s.sort, s.id
"""  # noqa: S608 - module constants only

_Q_SECTION_MEDIA = """
select sm.section_id, sm.media_id, m.owner_user_id from public.section_media sm
join public.chapter_sections s on s.id = sm.section_id
left join public.media_assets m on m.id = sm.media_id
where s.chapter_id = %(id)s order by s.sort, sm.sort nulls last, sm.media_id
"""


def owned_attachments(rows: list[Row], user_id: Any, visible: set[str]) -> dict[str, list[str]]:
    """Media the caller owns (their own figures, segment 15) attached to each section, in attachment order.

    Shared figures attached by `section_media` are not listed (the content places those itself); a row whose media
    the caller cannot see (RLS) or that is gone is skipped.
    """
    out: dict[str, list[str]] = defaultdict(list)
    uid = str(user_id)
    for r in rows:
        if r["owner_user_id"] is None or str(r["owner_user_id"]) != uid or r["media_id"] not in visible:
            continue
        if r["media_id"] not in out[r["section_id"]]:
            out[r["section_id"]].append(r["media_id"])
    return dict(out)


_Q_MEDIA = """
select m.id, m.kind, m.url, m.title, m.caption, m.attribution, m.width, m.height, m.youtube_id, m.duration_s,
       m.embeddable, src.id as source_id, src.name as source_name, src.license as source_license,
       src.url as source_url
from public.media_assets m left join public.sources src on src.id = m.source_id
where m.id = any(%(ids)s)
"""

_Q_EMBED_QUESTIONS = "select id, stem_html, options from public.questions where id = any(%(ids)s)"


def _etag(payload: bytes) -> str:
    return '"' + hashlib.sha256(payload).hexdigest()[:32] + '"'


def _source(r: Row) -> MediaSource | None:
    if not r["source_id"]:
        return None
    return MediaSource(id=r["source_id"], name=r["source_name"], license=r["source_license"], url=r["source_url"])


def _options(raw: Any) -> list[QuestionOption]:
    out: list[QuestionOption] = []
    for o in raw if isinstance(raw, list) else []:
        if isinstance(o, dict) and isinstance(o.get("label"), str) and isinstance(o.get("html"), str):
            out.append(QuestionOption(label=o["label"], html=o["html"]))
    return out


@router.get(
    "/chapters/{chapter_id}",
    operation_id="getChapter",
    summary="A chapter with its ordered sections (TipTap JSON), resolved media and embedded questions",
    response_model=ChapterDetail,
    responses={**_ERRORS, **_NOT_FOUND, 304: {"description": "Not modified (If-None-Match)"}},
)
async def get_chapter(
    request: Request,
    user: Annotated[CurrentUser, limited("courses_read")],
    chapter_id: Annotated[str, Path(pattern=ID_PATTERN)],
) -> Response:
    async def run(conn: psycopg.AsyncConnection[Any]) -> tuple[Row, list[Row], list[Row], list[Row], list[Row], Any]:
        found = await _rows(conn, _Q_CHAPTER, {"id": chapter_id})
        if not found:
            raise HTTPException(404, "This chapter does not exist or is not available to you.")
        ch = found[0]
        sections = await _rows(conn, _Q_SECTIONS_FULL, {"id": chapter_id})
        scan = ContentScan()
        for s in sections:
            scan_body(s["body"], scan)
        attached = await _rows(conn, _Q_SECTION_MEDIA, {"id": chapter_id})
        media_ids = list(dict.fromkeys([*scan.media_ids, *(r["media_id"] for r in attached)]))
        media = await _rows(conn, _Q_MEDIA, {"ids": media_ids}) if media_ids else []
        questions = await _rows(conn, _Q_EMBED_QUESTIONS, {"ids": scan.question_ids}) if scan.question_ids else []
        order = await _rows(conn, _Q_SHARED_ORDER, {"exam": ch["exam"]})
        st = await _ChapterStats.load(conn, [chapter_id], positions=False)
        nodes = await _rows(conn, _Q_SYSTEM_NODES, {"ids": sorted(st.all_system_ids())})
        return ch, sections, media, questions, order, (scan, st, nodes, attached)

    ch, sections, media_rows, question_rows, order, (scan, st, nodes, attached) = await as_user(_db(request), user, run)

    if scan.unknown_nodes or scan.unknown_marks or scan.truncated:
        log.warning(
            "content_unknown_nodes",
            chapter_id=chapter_id,
            node_types=sorted(scan.unknown_nodes),
            mark_types=sorted(scan.unknown_marks),
            truncated=scan.truncated,
        )
    visible = {r["id"] for r in media_rows}
    own_figures = owned_attachments(attached, user.user_id, visible)
    missing = [m for m in scan.media_ids if m not in visible]
    if missing:
        log.warning("content_media_missing", chapter_id=chapter_id, media_ids=missing[:20], count=len(missing))
    empty = [s["id"] for s in sections if s["body"] is None]
    if empty:
        log.warning("content_section_empty", chapter_id=chapter_id, section_ids=empty[:20], count=len(empty))

    urls = await _resolver(request).resolve(((r["id"], r["url"]) for r in media_rows), user.user_id)
    media = {
        r["id"]: MediaOut(
            id=r["id"],
            kind=r["kind"],
            url=urls.get(r["id"]),
            title=r["title"],
            caption=r["caption"],
            attribution=r["attribution"],
            width=r["width"],
            height=r["height"],
            youtube_id=r["youtube_id"],
            duration_s=r["duration_s"],
            embeddable=r["embeddable"],
            source=_source(r),
        )
        for r in media_rows
    }
    ids_in_order = [o["id"] for o in order]
    number = ids_in_order.index(chapter_id) + 1 if chapter_id in ids_in_order else None
    nxt = order[number] if number is not None and number < len(order) else None
    system_ids = {n["id"] for n in nodes}
    labels = {n["id"]: n["label"] for n in nodes}
    detail = ChapterDetail(
        id=ch["id"],
        exam=ch["exam"],
        kind=ch["kind"],
        title=ch["title"],
        meta=ch["meta"],
        number=number,
        chapter_count=len(order),
        systems=[SystemRef(id=s, label=labels[s]) for s in st.systems(chapter_id, system_ids)],
        coverage=st.coverage(chapter_id),
        sections=[
            SectionOut(
                id=s["id"],
                sort=s["sort"],
                heading=s["heading"],
                reading_minutes=int(s["minutes"]),
                taxonomy_ids=list(s["taxonomy_ids"]),
                body=s["body"] if isinstance(s["body"], dict) else None,
                attached_media=own_figures.get(s["id"], []),
            )
            for s in sections
        ],
        media=media,
        questions={
            q["id"]: EmbeddedQuestion(id=q["id"], stem_html=q["stem_html"], options=_options(q["options"]))
            for q in question_rows
        },
        next_chapter=ChapterLink(id=nxt["id"], title=nxt["title"]) if nxt else None,
    )
    payload = json.dumps(detail.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False).encode()
    etag = _etag(payload)
    # Signed URLs expire, so a private chapter is revalidated every time; the ETag saves the body transfer.
    headers = {"etag": etag, "cache-control": "private, no-cache", "vary": "authorization"}
    inm = request.headers.get("if-none-match")
    if inm and etag in [t.strip() for t in inm.split(",")]:
        return Response(status_code=304, headers=headers)
    return Response(payload, media_type="application/json", headers=headers)


# ── Routes: reading position ──

_UPSERT_POSITION = """
insert into public.reading_positions
  (user_id, chapter_id, section_id, scroll_position, completed_at, client_updated_at)
values ((select auth.uid()), %(chapter)s, %(section)s, %(pos)s,
        case when %(completed)s then now() end, %(cts)s)
on conflict (user_id, section_id) do update set
  scroll_position = excluded.scroll_position,
  completed_at = coalesce(reading_positions.completed_at, excluded.completed_at),
  client_updated_at = excluded.client_updated_at
where reading_positions.client_updated_at <= excluded.client_updated_at
  and reading_positions.chapter_id = excluded.chapter_id
returning chapter_id, section_id, scroll_position, completed_at, client_updated_at, updated_at
"""

_MARK_COMPLETED = """
update public.reading_positions set completed_at = coalesce(completed_at, now())
where user_id = (select auth.uid()) and section_id = %(section)s and chapter_id = %(chapter)s
returning chapter_id, section_id, scroll_position, completed_at, client_updated_at, updated_at
"""

_SELECT_POSITION = """
select chapter_id, section_id, scroll_position, completed_at, client_updated_at, updated_at
from public.reading_positions where user_id = (select auth.uid()) and section_id = %(section)s
"""


@router.put(
    "/reading-position",
    operation_id="putReadingPosition",
    summary="Save where the caller is in a section (last-writer-wins by the device's timestamp)",
    response_model=ReadingPositionWrite,
    responses={**_ERRORS, **_NOT_FOUND, 422: {"model": ErrorResponse, "description": "Invalid fields"}},
)
async def put_reading_position(
    request: Request, body: ReadingPositionIn, user: Annotated[CurrentUser, limited("position_write")]
) -> ReadingPositionWrite:
    now = dt.datetime.now(dt.UTC)
    cts = body.client_updated_at or now
    if cts > now + FUTURE_SKEW:
        cts = now

    async def run(conn: psycopg.AsyncConnection[Any]) -> ReadingPositionWrite:
        params = {
            "chapter": body.chapter_id,
            "section": body.section_id,
            "pos": body.offset,
            "completed": body.completed,
            "cts": cts,
        }
        if not await _rows(conn, _Q_CHAPTER, {"id": body.chapter_id}):
            raise HTTPException(404, "This section does not exist or is not available to you.")
        try:
            async with conn.transaction():
                rows = await _rows(conn, _UPSERT_POSITION, params)
        except (psycopg.errors.InsufficientPrivilege, psycopg.errors.ForeignKeyViolation) as exc:
            # The RLS check: the section is hidden, private to someone else, or not in that chapter.
            raise HTTPException(404, "This section does not exist or is not available to you.") from exc
        if rows:
            return ReadingPositionWrite(position=_position(rows[0]), applied=True)
        # A newer write from another device won (or the section is stored under another chapter).
        kept = await _rows(conn, _MARK_COMPLETED if body.completed else _SELECT_POSITION, params)
        if not kept or kept[0]["chapter_id"] != body.chapter_id:
            raise HTTPException(404, "This section does not exist or is not available to you.")
        return ReadingPositionWrite(position=_position(kept[0]), applied=False)

    return await as_user(_db(request), user, run)


@router.get(
    "/reading-position",
    operation_id="getReadingPosition",
    summary="The caller's positions in one chapter (all sections) and the most recent one, from any device",
    response_model=ChapterPositions,
    responses={**_ERRORS, 422: {"model": ErrorResponse, "description": "Invalid query"}},
)
async def get_reading_position(
    request: Request,
    user: Annotated[CurrentUser, limited("courses_read")],
    chapter_id: Annotated[str, Query(pattern=ID_PATTERN)],
) -> JSONResponse:
    async def run(conn: psycopg.AsyncConnection[Any]) -> ChapterPositions:
        rows = await _rows(conn, _Q_POSITIONS, {"ids": [chapter_id]})
        items = [_position(r) for r in rows]
        return ChapterPositions(chapter_id=chapter_id, last=items[0] if items else None, sections=items)

    out = await as_user(_db(request), user, run)
    return JSONResponse(out.model_dump(mode="json"), headers={"cache-control": "private, no-store"})


# ── Routes: report an issue ──

_REF_EXISTS: dict[str, str] = {
    "section": "select 1 from public.chapter_sections where id = %(id)s",
    "chapter": "select 1 from public.chapters where id = %(id)s",
    "media": "select 1 from public.media_assets where id = %(id)s",
    "question": "select 1 from public.questions where id = %(id)s",
}


@router.post(
    "/report-issue",
    operation_id="reportIssue",
    summary="Report a problem with a section, chapter, figure or question (stored for review)",
    response_model=ReportIssueOut,
    status_code=201,
    responses={**_ERRORS, **_NOT_FOUND, 422: {"model": ErrorResponse, "description": "Invalid fields"}},
)
async def report_issue(
    request: Request, body: ReportIssueIn, user: Annotated[CurrentUser, limited("report_write")]
) -> ReportIssueOut:
    async def run(conn: psycopg.AsyncConnection[Any]) -> ReportIssueOut:
        if not await _rows(conn, _REF_EXISTS[body.ref_type], {"id": body.ref_id}):
            raise HTTPException(404, "What you are reporting does not exist or is not available to you.")
        rows = await _rows(
            conn,
            "insert into public.report_issues (user_id, ref_type, ref_id, body)"
            " values ((select auth.uid()), %(t)s, %(id)s, %(body)s) returning id, status, created_at",
            {"t": body.ref_type, "id": body.ref_id, "body": body.body},
        )
        return ReportIssueOut(**rows[0])

    out = await as_user(_db(request), user, run)
    # Never the body: it is free text the student wrote.
    log.info("issue_reported", ref_type=body.ref_type, ref_id=body.ref_id, chars=len(body.body))
    return out


def default_course_limiters() -> dict[str, RateLimiter]:
    return {
        "courses_read": RateLimiter(240, 60.0),
        # The reader saves at most every few seconds while scrolling, plus once per section change.
        "position_write": RateLimiter(120, 60.0),
        "report_write": RateLimiter(10, 60.0),
    }


def install_courses_routes(app: Any, resolver: MediaUrlResolver | None = None) -> None:
    limiters: dict[str, RateLimiter] = getattr(app.state, "rate_limiters", {})
    for k, v in default_course_limiters().items():
        limiters.setdefault(k, v)
    app.state.rate_limiters = limiters
    app.state.media_resolver = resolver or MediaUrlResolver(app.state.settings)
    app.include_router(router)


__all__ = ["install_courses_routes", "router"]
