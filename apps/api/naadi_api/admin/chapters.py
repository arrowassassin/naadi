"""Admin chapters, sections and section revisions (segment 19).

- Chapters: create (status `draft`), edit metadata, reorder, status workflow draft → in_review → published →
  retired (publishing is refused while a section has no live body or a referenced asset fails its gates).
- Sections: create, edit heading/taxonomy, reorder.
- Revisions: the editor autosaves one draft per section (`PUT …/draft`, optimistic concurrency on `updated_at`);
  publish turns the draft into a published revision, whose body the database copies into
  `chapter_sections.body` (migration 0024 trigger), so the live body is always the latest published revision.
  Diff compares two revisions (or one with the live body) block by block; rollback publishes a new revision
  copying an old one. Every body is checked against the content node schema (unknown nodes refused) and every
  figure / video / pdf it references must be approved and pass the licence gates for a page.
- Publishing a section re-embeds it (S07's `schedule_reindex`, after commit).

Only shared chapters (`owner_user_id is null`) are reachable here; students' private chapters never are.
"""

from __future__ import annotations

import datetime as dt
import difflib
import math
import re
import secrets
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from ..auth import CurrentUser
from ..embeddings.jobs import schedule_reindex
from ..logs import get_logger
from .common import (
    ADMIN_ERRORS,
    CONFLICT,
    INVALID,
    NOT_FOUND,
    ConsoleAdmin,
    Row,
    audit,
    conflict,
    not_found,
    one,
    rows,
    run_tx,
)
from .content_schema import check_doc, refs_of, text_of, word_count
from .licence import GateRefused, check_media_row

log = get_logger("naadi.admin.chapters")

router = APIRouter(prefix="/api/admin", tags=["admin"])

CHAPTER_ID = r"^[a-z0-9][a-z0-9-]{1,63}$"
SECTION_ID = r"^[a-z0-9][a-z0-9_-]{1,127}$"
SOURCE_ID = r"^[a-z0-9][a-z0-9:._-]{0,127}$"
TAXONOMY_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
WORDS_PER_MINUTE = 200
MAX_BODY_NODES_BYTES = 2 * 1024 * 1024

ChapterStatus = Literal["draft", "in_review", "published", "retired"]
EXAMS = ("step1", "step2ck", "step3")
Exam = Literal["step1", "step2ck", "step3"]
# The workflow: forward one step, back to draft from review or retirement, published straight to retired.
TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"in_review"}),
    "in_review": frozenset({"draft", "published"}),
    "published": frozenset({"retired", "in_review"}),
    "retired": frozenset({"draft"}),
}
ADMIN_KINDS = ("chapter", "mock_exam", "pathway")


# ── models ──


class AdminChapter(BaseModel):
    id: str
    exam: str
    kind: str
    title: str
    meta: str | None
    sort: int | None
    status: ChapterStatus
    source_id: str | None
    section_count: int
    created_at: dt.datetime
    updated_at: dt.datetime


class AdminChapterList(BaseModel):
    items: list[AdminChapter]


class AdminSection(BaseModel):
    id: str
    chapter_id: str
    sort: int
    heading: str
    taxonomy_ids: list[str]
    reading_minutes: int | None
    has_body: bool = Field(description="The section has a live (published) body")
    has_draft: bool
    draft_updated_at: dt.datetime | None
    live_revision_id: int | None = Field(description="Latest published non-seed revision (null: the seed body)")
    updated_at: dt.datetime
    admin_edited_at: dt.datetime | None


class AdminChapterDetail(BaseModel):
    chapter: AdminChapter
    sections: list[AdminSection]


def _taxonomy(v: list[str]) -> list[str]:
    out: list[str] = []
    for t in v:
        if not TAXONOMY_ID.match(t):
            raise ValueError("taxonomy ids are lowercase dotted ids")
        if t not in out:
            out.append(t)
    return out


class ChapterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, pattern=CHAPTER_ID, description="Leave out to generate one")
    exam: Exam
    kind: Literal["chapter", "mock_exam", "pathway"] = "chapter"
    title: str = Field(min_length=1, max_length=200)
    meta: str | None = Field(default=None, max_length=300)
    source_id: str = Field(pattern=SOURCE_ID)

    @field_validator("title", "meta")
    @classmethod
    def _trim(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class ChapterPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=200)
    meta: str | None = Field(default=None, max_length=300)
    clear_meta: bool = False
    source_id: str | None = Field(default=None, pattern=SOURCE_ID)
    expected_updated_at: dt.datetime


class ChapterStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: ChapterStatus
    note: str | None = Field(default=None, max_length=1000)


class ChapterReorder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exam: Exam
    ids: list[Annotated[str, Field(pattern=CHAPTER_ID)]] = Field(min_length=1, max_length=500)


class SectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str = Field(min_length=1, max_length=200)
    taxonomy_ids: list[str] = Field(default_factory=list, max_length=50)

    _tax = field_validator("taxonomy_ids")(classmethod(lambda cls, v: _taxonomy(v)))


class SectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    heading: str | None = Field(default=None, min_length=1, max_length=200)
    taxonomy_ids: list[str] | None = Field(default=None, max_length=50)

    @field_validator("taxonomy_ids")
    @classmethod
    def _tax(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _taxonomy(v)


class SectionReorder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[Annotated[str, Field(pattern=SECTION_ID)]] = Field(min_length=1, max_length=500)


RevisionStatus = Literal["draft", "published"]
RevisionOrigin = Literal["admin", "assistant", "seed"]


class RevisionSummary(BaseModel):
    id: int
    section_id: str
    status: RevisionStatus
    origin: RevisionOrigin
    note: str | None
    author_user_id: uuid.UUID | None
    parent_revision_id: int | None
    restored_from_id: int | None
    created_at: dt.datetime
    updated_at: dt.datetime
    published_at: dt.datetime | None
    word_count: int
    is_live: bool = Field(description="This revision's body is the section's live body")


class Revision(RevisionSummary):
    body: dict[str, JsonValue] | None


class RevisionList(BaseModel):
    items: list[RevisionSummary]


class LiveBody(BaseModel):
    body: dict[str, JsonValue] | None
    revision_id: int | None


class EditorMedia(BaseModel):
    id: str
    kind: str
    title: str | None
    review_status: str
    source_id: str


class EditorQuestion(BaseModel):
    id: str
    status: str
    exam: str


class SectionEditor(BaseModel):
    chapter: AdminChapter
    section: AdminSection
    live: LiveBody
    draft: Revision | None
    media: list[EditorMedia]
    questions: list[EditorQuestion]


class DraftSave(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: dict[str, JsonValue]
    parent_revision_id: int | None = Field(description="live_revision_id the draft was started from")
    expected_updated_at: dt.datetime | None = Field(description="The draft's updated_at; null for a new draft")
    note: str | None = Field(default=None, max_length=500)


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision_id: int
    expected_updated_at: dt.datetime
    note: str | None = Field(default=None, max_length=500)


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=500)


class DiffBlock(BaseModel):
    op: Literal["equal", "insert", "delete"]
    kind: str = Field(description="Top-level node type")
    text: str = Field(description="Plain text of the block, or the media/question id for an embed")


class RevisionDiff(BaseModel):
    from_id: int | None = Field(description="null: the live body")
    to_id: int
    blocks: list[DiffBlock]


# ── SQL ──

_CHAPTER_SELECT = """
select c.id, c.exam, c.kind, c.title, c.meta, c.sort, c.status, c.source_id, c.created_at, c.updated_at,
       (select count(*) from public.chapter_sections s where s.chapter_id = c.id)::int as section_count
  from public.chapters c
 where c.owner_user_id is null
"""

_SECTION_SELECT = """
select s.id, s.chapter_id, s.sort, s.heading, s.taxonomy_ids, s.reading_minutes, s.updated_at, s.admin_edited_at,
       s.body is not null as has_body,
       d.id as draft_id, d.updated_at as draft_updated_at,
       (select r.id from public.chapter_section_revisions r
         where r.section_id = s.id and r.status = 'published' and r.origin <> 'seed'
         order by r.published_at desc, r.id desc limit 1) as live_revision_id
  from public.chapter_sections s
  left join public.chapter_section_revisions d on d.section_id = s.id and d.status = 'draft'
"""

_REVISION_COLS = (
    "r.id, r.section_id, r.status, r.origin, r.note, r.author_user_id, r.parent_revision_id, r.restored_from_id,"
    " r.created_at, r.updated_at, r.published_at, r.body"
)


def _chapter(r: Row) -> AdminChapter:
    return AdminChapter(**{k: r[k] for k in AdminChapter.model_fields})


def _section(r: Row) -> AdminSection:
    return AdminSection(
        id=r["id"],
        chapter_id=r["chapter_id"],
        sort=r["sort"],
        heading=r["heading"],
        taxonomy_ids=list(r["taxonomy_ids"] or []),
        reading_minutes=r["reading_minutes"],
        has_body=r["has_body"],
        has_draft=r["draft_id"] is not None,
        draft_updated_at=r["draft_updated_at"],
        live_revision_id=r["live_revision_id"],
        updated_at=r["updated_at"],
        admin_edited_at=r["admin_edited_at"],
    )


def _revision(r: Row, live_id: int | None, *, with_body: bool) -> Revision | RevisionSummary:
    fields: dict[str, Any] = {
        k: r[k]
        for k in (
            "id",
            "section_id",
            "status",
            "origin",
            "note",
            "author_user_id",
            "parent_revision_id",
            "restored_from_id",
            "created_at",
            "updated_at",
            "published_at",
        )
    }
    fields["word_count"] = word_count(r["body"])
    fields["is_live"] = live_id is not None and r["id"] == live_id
    if with_body:
        return Revision(**fields, body=r["body"] if isinstance(r["body"], dict) else None)
    return RevisionSummary(**fields)


async def get_chapter_row(conn: AsyncConnection[Any], chapter_id: str, *, lock: bool = False) -> Row:
    r = await one(conn, _CHAPTER_SELECT + " and c.id = %s" + (" for update of c" if lock else ""), (chapter_id,))
    if r is None:
        raise not_found("chapter")
    return r


async def get_section_row(conn: AsyncConnection[Any], section_id: str, *, lock: bool = False) -> Row:
    if lock:
        await conn.execute("select 1 from public.chapter_sections where id = %s for update", (section_id,))
    r = await one(
        conn,
        _SECTION_SELECT + " join public.chapters c on c.id = s.chapter_id where s.id = %s and c.owner_user_id is null",
        (section_id,),
    )
    if r is None:
        raise not_found("section")
    return r


async def _sections(conn: AsyncConnection[Any], chapter_id: str) -> list[AdminSection]:
    return [
        _section(r)
        for r in await rows(conn, _SECTION_SELECT + " where s.chapter_id = %s order by s.sort, s.id", (chapter_id,))
    ]


class InvalidBody(Exception):
    def __init__(self, problems: list[Any]) -> None:
        super().__init__("invalid body")
        self.problems = problems


def validate_body(body: Any) -> None:
    problems = check_doc(body)
    if problems:
        raise InvalidBody(problems)


async def gate_body(conn: AsyncConnection[Any], body: Any) -> None:
    """Every referenced media asset is approved and licensed for a page; every embedded question is published."""
    media, questions = refs_of(body)
    for _kind, mid in media:
        await check_media_row(conn, mid, use="page", require_approved=True)
    for qid in questions:
        q = await one(conn, "select status from public.questions where id = %s", (qid,))
        if q is None or q["status"] != "published":
            raise GateRefused("question_missing", qid)


def reading_minutes(body: Any) -> int:
    return max(1, math.ceil(word_count(body) / WORDS_PER_MINUTE))


async def _sync_section_media(conn: AsyncConnection[Any], section_id: str, body: Any) -> None:
    """Keep `section_media` in step with the published body for media that only appear through body nodes
    (figures and PDFs); videos attached through approvals (S20) without a node are left alone."""
    media, _ = refs_of(body)
    ids = [mid for _k, mid in media]
    for n, mid in enumerate(ids):
        await conn.execute(
            "insert into public.section_media (section_id, media_id, sort) values (%s, %s, %s)"
            " on conflict (section_id, media_id) do update set sort = excluded.sort",
            (section_id, mid, n),
        )
    await conn.execute(
        "delete from public.section_media sm using public.media_assets m"
        " where sm.section_id = %s and m.id = sm.media_id and m.kind in ('svg','image','pdf')"
        " and not (sm.media_id = any(%s))",
        (section_id, ids),
    )


def _reindex(request: Request, section_ids: list[str]) -> None:
    if section_ids:
        schedule_reindex(request.app, ("sections",), ref_ids=section_ids)


# ── chapters ──


@router.get(
    "/chapters",
    operation_id="listAdminChapters",
    summary="Admin only: shared chapters of every status",
    response_model=AdminChapterList,
    responses=ADMIN_ERRORS,
)
async def list_chapters(
    request: Request,
    admin: ConsoleAdmin,
    exam: Annotated[Exam | None, Query()] = None,
    status: Annotated[ChapterStatus | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> AdminChapterList:
    async def run(conn: AsyncConnection[Any]) -> AdminChapterList:
        sql = _CHAPTER_SELECT + " and c.kind = any(%(kinds)s)"
        params: dict[str, Any] = {"kinds": list(ADMIN_KINDS)}
        if exam:
            sql += " and c.exam = %(exam)s"
            params["exam"] = exam
        if status:
            sql += " and c.status = %(status)s"
            params["status"] = status
        if q and q.strip():
            sql += " and (c.title ilike %(q)s or c.id ilike %(q)s)"
            params["q"] = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        sql += " order by c.exam, c.sort nulls last, c.id"
        return AdminChapterList(items=[_chapter(r) for r in await rows(conn, sql, params)])

    return await run_tx(request, run)


def _new_chapter_id(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    return f"{slug or 'chapter'}-{secrets.token_hex(3)}"


@router.post(
    "/chapters",
    operation_id="createAdminChapter",
    summary="Admin only: create a shared chapter (status draft, invisible to students)",
    response_model=AdminChapter,
    status_code=201,
    responses={**ADMIN_ERRORS, **CONFLICT, **INVALID},
)
async def create_chapter(request: Request, admin: ConsoleAdmin, body: ChapterCreate) -> AdminChapter:
    async def run(conn: AsyncConnection[Any]) -> AdminChapter:
        if await one(conn, "select 1 from public.sources where id = %s", (body.source_id,)) is None:
            raise GateRefused("no_source", body.source_id)
        cid = body.id or _new_chapter_id(body.title)
        if await one(conn, "select 1 from public.chapters where id = %s", (cid,)):
            raise conflict("id_taken", "A chapter with this id exists.")
        nxt = await one(
            conn,
            "select coalesce(max(sort), 0) + 1 as n from public.chapters where exam = %s and owner_user_id is null",
            (body.exam,),
        )
        await conn.execute(
            "insert into public.chapters (id, exam, kind, title, meta, sort, status, source_id)"
            " values (%s, %s, %s, %s, %s, %s, 'draft', %s)",
            (cid, body.exam, body.kind, body.title, body.meta, min(int(nxt["n"]) if nxt else 1, 32767), body.source_id),
        )
        row = await get_chapter_row(conn, cid)
        await audit(conn, admin, "chapter.create", "chapter", cid, after=row)
        return _chapter(row)

    return await run_tx(request, run)


@router.get(
    "/chapters/{chapter_id}",
    operation_id="getAdminChapter",
    summary="Admin only: one chapter with its sections",
    response_model=AdminChapterDetail,
    responses={**ADMIN_ERRORS, **NOT_FOUND},
)
async def get_chapter(
    request: Request, admin: ConsoleAdmin, chapter_id: Annotated[str, Path(pattern=CHAPTER_ID)]
) -> AdminChapterDetail:
    async def run(conn: AsyncConnection[Any]) -> AdminChapterDetail:
        return AdminChapterDetail(
            chapter=_chapter(await get_chapter_row(conn, chapter_id)), sections=await _sections(conn, chapter_id)
        )

    return await run_tx(request, run)


@router.patch(
    "/chapters/{chapter_id}",
    operation_id="patchAdminChapter",
    summary="Admin only: edit a chapter's title, meta line or source",
    response_model=AdminChapter,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **CONFLICT, **INVALID},
)
async def patch_chapter(
    request: Request, admin: ConsoleAdmin, chapter_id: Annotated[str, Path(pattern=CHAPTER_ID)], body: ChapterPatch
) -> AdminChapter:
    async def run(conn: AsyncConnection[Any]) -> AdminChapter:
        before = await get_chapter_row(conn, chapter_id, lock=True)
        if before["updated_at"] != body.expected_updated_at:
            raise conflict("stale_write", "This chapter was changed since you opened it.")
        if body.source_id and await one(conn, "select 1 from public.sources where id = %s", (body.source_id,)) is None:
            raise GateRefused("no_source", body.source_id)
        title = body.title.strip() if body.title else before["title"]
        if not title:
            raise HTTPException(422, "The title must not be blank.")
        meta = None if body.clear_meta else ((body.meta.strip() or None) if body.meta is not None else before["meta"])
        await conn.execute(
            "update public.chapters set title = %s, meta = %s, source_id = %s where id = %s",
            (title, meta, body.source_id or before["source_id"], chapter_id),
        )
        after = await get_chapter_row(conn, chapter_id)
        await audit(conn, admin, "chapter.edit", "chapter", chapter_id, before=before, after=after)
        return _chapter(after)

    return await run_tx(request, run)


async def gate_chapter_publish(conn: AsyncConnection[Any], chapter_id: str) -> None:
    secs = await rows(
        conn, "select id, body from public.chapter_sections where chapter_id = %s order by sort", (chapter_id,)
    )
    if not secs:
        raise GateRefused("no_sections", chapter_id)
    for s in secs:
        if s["body"] is None:
            raise GateRefused("section_empty", s["id"])
        await gate_body(conn, s["body"])
    ch = await one(conn, "select source_id from public.chapters where id = %s", (chapter_id,))
    if (
        ch is None
        or ch["source_id"] is None
        or await one(conn, "select 1 from public.sources where id = %s", (ch["source_id"],)) is None
    ):
        raise GateRefused("no_source", chapter_id)


@router.post(
    "/chapters/{chapter_id}/status",
    operation_id="setAdminChapterStatus",
    summary="Admin only: move a chapter through draft → in_review → published → retired",
    response_model=AdminChapter,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **CONFLICT},
)
async def set_chapter_status(
    request: Request,
    admin: ConsoleAdmin,
    chapter_id: Annotated[str, Path(pattern=CHAPTER_ID)],
    body: ChapterStatusChange,
) -> AdminChapter:
    section_ids: list[str] = []

    async def run(conn: AsyncConnection[Any]) -> AdminChapter:
        before = await get_chapter_row(conn, chapter_id, lock=True)
        if body.status == before["status"]:
            return _chapter(before)
        if body.status not in TRANSITIONS[before["status"]]:
            raise conflict("bad_transition", "This status change is not allowed.")
        if body.status == "published":
            await gate_chapter_publish(conn, chapter_id)
        await conn.execute("update public.chapters set status = %s where id = %s", (body.status, chapter_id))
        after = await get_chapter_row(conn, chapter_id)
        await audit(
            conn,
            admin,
            f"chapter.status_{body.status}",
            "chapter",
            chapter_id,
            before=before,
            after=after,
            note=body.note,
        )
        if "published" in (body.status, before["status"]):
            section_ids.extend(
                r["id"]
                for r in await rows(conn, "select id from public.chapter_sections where chapter_id = %s", (chapter_id,))
            )
        return _chapter(after)

    out = await run_tx(request, run)
    _reindex(request, section_ids)  # published → visible to search; retired → its chunks are dropped
    return out


@router.post(
    "/chapters/reorder",
    operation_id="reorderAdminChapters",
    summary="Admin only: set the order of an exam's chapters",
    response_model=AdminChapterList,
    responses={**ADMIN_ERRORS, **CONFLICT, **INVALID},
)
async def reorder_chapters(request: Request, admin: ConsoleAdmin, body: ChapterReorder) -> AdminChapterList:
    async def run(conn: AsyncConnection[Any]) -> AdminChapterList:
        current = await rows(
            conn,
            "select id from public.chapters where exam = %s and owner_user_id is null and kind = any(%s) for update",
            (body.exam, list(ADMIN_KINDS)),
        )
        if sorted(r["id"] for r in current) != sorted(body.ids) or len(set(body.ids)) != len(body.ids):
            raise conflict("stale_order", "The chapter list changed. Reload and try again.")
        for n, cid in enumerate(body.ids, start=1):
            await conn.execute(
                "update public.chapters set sort = %s where id = %s and sort is distinct from %s", (n, cid, n)
            )
        await audit(conn, admin, "chapter.reorder", "exam", body.exam, after={"ids": body.ids})
        sql = _CHAPTER_SELECT + " and c.exam = %s and c.kind = any(%s) order by c.sort nulls last, c.id"
        return AdminChapterList(items=[_chapter(r) for r in await rows(conn, sql, (body.exam, list(ADMIN_KINDS)))])

    return await run_tx(request, run)


# ── sections ──


async def _new_section_id(conn: AsyncConnection[Any], chapter_id: str) -> str:
    base = chapter_id[:100]
    taken = {
        r["id"] for r in await rows(conn, "select id from public.chapter_sections where id like %s", (base + "-s%",))
    }
    n = len(taken) + 1
    while f"{base}-s{n:02d}" in taken:
        n += 1
    return f"{base}-s{n:02d}"


@router.post(
    "/chapters/{chapter_id}/sections",
    operation_id="createAdminSection",
    summary="Admin only: add an empty section at the end of a chapter",
    response_model=AdminSection,
    status_code=201,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **INVALID},
)
async def create_section(
    request: Request, admin: ConsoleAdmin, chapter_id: Annotated[str, Path(pattern=CHAPTER_ID)], body: SectionCreate
) -> AdminSection:
    async def run(conn: AsyncConnection[Any]) -> AdminSection:
        await get_chapter_row(conn, chapter_id, lock=True)
        sid = await _new_section_id(conn, chapter_id)
        nxt = await one(
            conn,
            "select coalesce(max(sort), 0) + 1 as n from public.chapter_sections where chapter_id = %s",
            (chapter_id,),
        )
        await conn.execute(
            "insert into public.chapter_sections (id, chapter_id, sort, heading, taxonomy_ids, admin_edited_at)"
            " values (%s, %s, %s, %s, %s, now())",
            (sid, chapter_id, int(nxt["n"]) if nxt else 1, body.heading.strip(), body.taxonomy_ids),
        )
        row = await get_section_row(conn, sid)
        await audit(conn, admin, "section.create", "section", sid, after=row)
        return _section(row)

    return await run_tx(request, run)


@router.patch(
    "/sections/{section_id}",
    operation_id="patchAdminSection",
    summary="Admin only: edit a section's heading or taxonomy tags",
    response_model=AdminSection,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **INVALID},
)
async def patch_section(
    request: Request, admin: ConsoleAdmin, section_id: Annotated[str, Path(pattern=SECTION_ID)], body: SectionPatch
) -> AdminSection:
    async def run(conn: AsyncConnection[Any]) -> AdminSection:
        before = await get_section_row(conn, section_id, lock=True)
        heading = body.heading.strip() if body.heading is not None else before["heading"]
        if not heading:
            raise HTTPException(422, "The heading must not be blank.")
        tax = body.taxonomy_ids if body.taxonomy_ids is not None else list(before["taxonomy_ids"] or [])
        await conn.execute(
            "update public.chapter_sections set heading = %s, taxonomy_ids = %s, admin_edited_at = now() where id = %s",
            (heading, tax, section_id),
        )
        after = await get_section_row(conn, section_id)
        await audit(conn, admin, "section.edit", "section", section_id, before=before, after=after)
        return _section(after)

    out = await run_tx(request, run)
    _reindex(request, [section_id])
    return out


@router.post(
    "/chapters/{chapter_id}/sections/reorder",
    operation_id="reorderAdminSections",
    summary="Admin only: set the order of a chapter's sections",
    response_model=AdminChapterDetail,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **CONFLICT, **INVALID},
)
async def reorder_sections(
    request: Request, admin: ConsoleAdmin, chapter_id: Annotated[str, Path(pattern=CHAPTER_ID)], body: SectionReorder
) -> AdminChapterDetail:
    async def run(conn: AsyncConnection[Any]) -> AdminChapterDetail:
        chapter = await get_chapter_row(conn, chapter_id, lock=True)
        current = await rows(
            conn, "select id from public.chapter_sections where chapter_id = %s for update", (chapter_id,)
        )
        if sorted(r["id"] for r in current) != sorted(body.ids) or len(set(body.ids)) != len(body.ids):
            raise conflict("stale_order", "The section list changed. Reload and try again.")
        # two passes: sort has no unique constraint, but keep every intermediate state sane anyway
        for n, sid in enumerate(body.ids, start=1):
            await conn.execute(
                "update public.chapter_sections set sort = %s where id = %s and sort is distinct from %s", (n, sid, n)
            )
        await audit(conn, admin, "section.reorder", "chapter", chapter_id, after={"ids": body.ids})
        return AdminChapterDetail(chapter=_chapter(chapter), sections=await _sections(conn, chapter_id))

    return await run_tx(request, run)


# ── editor, drafts, publish ──


async def _live_id(conn: AsyncConnection[Any], section_id: str) -> int | None:
    r = await one(
        conn,
        "select id from public.chapter_section_revisions where section_id = %s and status = 'published'"
        " and origin <> 'seed' order by published_at desc, id desc limit 1",
        (section_id,),
    )
    return int(r["id"]) if r else None


async def _draft(conn: AsyncConnection[Any], section_id: str, *, lock: bool = False) -> Row | None:
    return await one(
        conn,
        f"select {_REVISION_COLS} from public.chapter_section_revisions r"  # noqa: S608 - constant columns
        " where r.section_id = %s and r.status = 'draft'" + (" for update" if lock else ""),
        (section_id,),
    )


@router.get(
    "/sections/{section_id}/editor",
    operation_id="getAdminSectionEditor",
    summary="Admin only: everything the chapter editor needs for one section (live body, draft, references)",
    response_model=SectionEditor,
    responses={**ADMIN_ERRORS, **NOT_FOUND},
)
async def get_editor(
    request: Request, admin: ConsoleAdmin, section_id: Annotated[str, Path(pattern=SECTION_ID)]
) -> SectionEditor:
    async def run(conn: AsyncConnection[Any]) -> SectionEditor:
        srow = await get_section_row(conn, section_id)
        chapter = await get_chapter_row(conn, srow["chapter_id"])
        body_row = await one(conn, "select body from public.chapter_sections where id = %s", (section_id,))
        live_body = body_row["body"] if body_row and isinstance(body_row["body"], dict) else None
        live_id = await _live_id(conn, section_id)
        draft = await _draft(conn, section_id)
        media_ids: list[str] = []
        question_ids: list[str] = []
        for b in (live_body, draft["body"] if draft else None):
            m, q = refs_of(b)
            media_ids += [mid for _k, mid in m if mid not in media_ids]
            question_ids += [qid for qid in q if qid not in question_ids]
        media = await rows(
            conn,
            "select id, kind, title, review_status, source_id from public.media_assets where id = any(%s) order by id",
            (media_ids,),
        )
        questions = await rows(
            conn, "select id, status, exam from public.questions where id = any(%s) order by id", (question_ids,)
        )
        return SectionEditor(
            chapter=_chapter(chapter),
            section=_section(srow),
            live=LiveBody(body=live_body, revision_id=live_id),
            draft=_revision(draft, live_id, with_body=True) if draft else None,
            media=[EditorMedia(**m) for m in media],
            questions=[EditorQuestion(**q) for q in questions],
        )

    return await run_tx(request, run)


@router.put(
    "/sections/{section_id}/draft",
    operation_id="saveAdminSectionDraft",
    summary="Admin only: save (autosave) the section's draft; the body must match the content schema",
    response_model=Revision,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **CONFLICT, **INVALID},
)
async def save_draft(
    request: Request, admin: ConsoleAdmin, section_id: Annotated[str, Path(pattern=SECTION_ID)], body: DraftSave
) -> Revision:
    validate_body(body.body)

    async def run(conn: AsyncConnection[Any]) -> Revision:
        await get_section_row(conn, section_id, lock=True)
        draft = await _draft(conn, section_id, lock=True)
        live_id = await _live_id(conn, section_id)
        if (
            body.parent_revision_id is not None
            and await one(
                conn,
                "select 1 from public.chapter_section_revisions"
                " where id = %s and section_id = %s and status = 'published'",
                (body.parent_revision_id, section_id),
            )
            is None
        ):
            raise conflict("bad_parent", "The draft's starting revision is not a published revision of this section.")
        if draft is None:
            if body.expected_updated_at is not None:
                raise conflict("stale_draft", "This draft was published or discarded since you opened it.")
            r = await one(
                conn,
                "insert into public.chapter_section_revisions"  # noqa: S608 - constant column list
                " (section_id, body, body_html, author_user_id, origin, note, status, parent_revision_id)"
                f" values (%s, %s, null, %s, 'admin', %s, 'draft', %s) returning {_REVISION_COLS.replace('r.', '')}",
                (section_id, Jsonb(body.body), admin.user_id, body.note, body.parent_revision_id),
            )
            assert r is not None
            await audit(
                conn,
                admin,
                "section.draft_create",
                "section",
                section_id,
                after={"revision": r["id"], "body": body.body},
            )
        else:
            if body.expected_updated_at is None or draft["updated_at"] != body.expected_updated_at:
                raise conflict("stale_draft", "This draft was saved somewhere else since you opened it.")
            r = await one(
                conn,
                "update public.chapter_section_revisions set body = %s, note = %s, parent_revision_id = %s,"  # noqa: S608 - constant column list
                " author_user_id = %s"
                f" where id = %s returning {_REVISION_COLS.replace('r.', '')}",
                (Jsonb(body.body), body.note, body.parent_revision_id, admin.user_id, draft["id"]),
            )
            assert r is not None
            # Autosave is frequent: one audit row per save would bury everything else; drafts are not live, so
            # only create / publish / discard are audited.
        return _revision(r, live_id, with_body=True)  # type: ignore[return-value]

    return await run_tx(request, run)


@router.delete(
    "/sections/{section_id}/draft",
    operation_id="discardAdminSectionDraft",
    summary="Admin only: discard the section's draft",
    status_code=204,
    responses={**ADMIN_ERRORS, **NOT_FOUND},
)
async def discard_draft(
    request: Request, admin: ConsoleAdmin, section_id: Annotated[str, Path(pattern=SECTION_ID)]
) -> None:
    async def run(conn: AsyncConnection[Any]) -> None:
        await get_section_row(conn, section_id, lock=True)
        draft = await _draft(conn, section_id, lock=True)
        if draft is None:
            raise not_found("draft")
        await conn.execute("delete from public.chapter_section_revisions where id = %s", (draft["id"],))
        await audit(
            conn,
            admin,
            "section.draft_discard",
            "section",
            section_id,
            before={"revision": draft["id"], "body": draft["body"]},
        )

    await run_tx(request, run)


async def publish_body(conn: AsyncConnection[Any], admin: CurrentUser, section_id: str, body: Any) -> None:
    """After a revision became published: reading time, section_media, edited mark (the trigger set the body)."""
    await conn.execute(
        "update public.chapter_sections set reading_minutes = %s, admin_edited_at = now() where id = %s",
        (reading_minutes(body), section_id),
    )
    await _sync_section_media(conn, section_id, body)


@router.post(
    "/sections/{section_id}/publish",
    operation_id="publishAdminSection",
    summary="Admin only: publish the draft; it becomes the live body (licence gates and schema apply)",
    response_model=Revision,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **CONFLICT, **INVALID},
)
async def publish(
    request: Request, admin: ConsoleAdmin, section_id: Annotated[str, Path(pattern=SECTION_ID)], body: PublishRequest
) -> Revision:
    async def run(conn: AsyncConnection[Any]) -> Revision:
        await get_section_row(conn, section_id, lock=True)
        draft = await _draft(conn, section_id, lock=True)
        if draft is None or draft["id"] != body.revision_id or draft["updated_at"] != body.expected_updated_at:
            raise conflict("stale_draft", "This draft changed since you opened it.")
        live_id = await _live_id(conn, section_id)
        if draft["parent_revision_id"] != live_id:
            raise conflict(
                "stale_parent", "Someone published this section since your draft started. Review the changes first."
            )
        validate_body(draft["body"])
        await gate_body(conn, draft["body"])
        before = await one(conn, "select body from public.chapter_sections where id = %s", (section_id,))
        r = await one(
            conn,
            "update public.chapter_section_revisions set status = 'published', published_at = now(), published_by = %s,"  # noqa: S608 - constant column list
            " note = coalesce(%s, note)"
            f" where id = %s returning {_REVISION_COLS.replace('r.', '')}",
            (admin.user_id, body.note, draft["id"]),
        )
        assert r is not None
        await publish_body(conn, admin, section_id, r["body"])
        await audit(
            conn,
            admin,
            "section.publish",
            "section",
            section_id,
            before=before,
            after={"body": r["body"]},
            note=body.note,
        )
        return _revision(r, r["id"], with_body=True)  # type: ignore[return-value]

    out = await run_tx(request, run)
    _reindex(request, [section_id])
    return out


@router.get(
    "/sections/{section_id}/revisions",
    operation_id="listAdminSectionRevisions",
    summary="Admin only: a section's revisions, newest first",
    response_model=RevisionList,
    responses={**ADMIN_ERRORS, **NOT_FOUND},
)
async def list_revisions(
    request: Request,
    admin: ConsoleAdmin,
    section_id: Annotated[str, Path(pattern=SECTION_ID)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> RevisionList:
    async def run(conn: AsyncConnection[Any]) -> RevisionList:
        await get_section_row(conn, section_id)
        live_id = await _live_id(conn, section_id)
        found = await rows(
            conn,
            f"select {_REVISION_COLS} from public.chapter_section_revisions r where r.section_id = %s"  # noqa: S608
            " order by (r.status = 'draft') desc, coalesce(r.published_at, r.created_at) desc, r.id desc limit %s",
            (section_id, limit),
        )
        return RevisionList(items=[_revision(r, live_id, with_body=False) for r in found])

    return await run_tx(request, run)


async def _revision_row(conn: AsyncConnection[Any], revision_id: int) -> Row:
    r = await one(
        conn,
        f"select {_REVISION_COLS} from public.chapter_section_revisions r"  # noqa: S608
        " join public.chapter_sections s on s.id = r.section_id join public.chapters c on c.id = s.chapter_id"
        " where r.id = %s and c.owner_user_id is null",
        (revision_id,),
    )
    if r is None:
        raise not_found("revision")
    return r


@router.get(
    "/revisions/{revision_id}",
    operation_id="getAdminRevision",
    summary="Admin only: one section revision with its body",
    response_model=Revision,
    responses={**ADMIN_ERRORS, **NOT_FOUND},
)
async def get_revision(request: Request, admin: ConsoleAdmin, revision_id: Annotated[int, Path(ge=1)]) -> Revision:
    async def run(conn: AsyncConnection[Any]) -> Revision:
        r = await _revision_row(conn, revision_id)
        return _revision(r, await _live_id(conn, r["section_id"]), with_body=True)  # type: ignore[return-value]

    return await run_tx(request, run)


def _blocks(body: Any) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    content = body.get("content") if isinstance(body, dict) else None
    for b in content if isinstance(content, list) else []:
        if not isinstance(b, dict):
            continue
        kind = str(b.get("type"))
        raw_attrs = b.get("attrs")
        attrs: dict[str, Any] = raw_attrs if isinstance(raw_attrs, dict) else {}
        if kind in ("figure", "video", "pdf"):
            label = str(attrs.get("mediaId", ""))
        elif kind == "questionEmbed":
            label = str(attrs.get("questionId", ""))
        elif kind == "mermaid":
            label = str(attrs.get("code", ""))
        elif kind == "math":
            label = str(attrs.get("latex", ""))
        else:
            label = text_of(b)
            if kind == "callout":
                kind = f"callout:{attrs.get('variant')}"
            elif kind == "heading":
                kind = f"heading:{attrs.get('level')}"
        out.append((kind, label))
    return out


def diff_bodies(old: Any, new: Any) -> list[DiffBlock]:
    """Top-level blocks of `old` → `new`: equal / delete / insert, in reading order (a changed block is a delete
    followed by an insert)."""
    a, b = _blocks(old), _blocks(new)
    out: list[DiffBlock] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            out += [DiffBlock(op="equal", kind=k, text=t) for k, t in a[i1:i2]]
            continue
        out += [DiffBlock(op="delete", kind=k, text=t) for k, t in a[i1:i2]]
        out += [DiffBlock(op="insert", kind=k, text=t) for k, t in b[j1:j2]]
    return out


@router.get(
    "/revisions/{revision_id}/diff",
    operation_id="diffAdminRevision",
    summary="Admin only: block diff of a revision against another revision or the live body",
    response_model=RevisionDiff,
    responses={**ADMIN_ERRORS, **NOT_FOUND},
)
async def diff_revision(
    request: Request,
    admin: ConsoleAdmin,
    revision_id: Annotated[int, Path(ge=1)],
    against: Annotated[str, Query(pattern=r"^(live|[1-9][0-9]{0,17})$")] = "live",
) -> RevisionDiff:
    async def run(conn: AsyncConnection[Any]) -> RevisionDiff:
        to = await _revision_row(conn, revision_id)
        if against == "live":
            live = await one(conn, "select body from public.chapter_sections where id = %s", (to["section_id"],))
            old, from_id = (live["body"] if live else None), None
        else:
            frm = await _revision_row(conn, int(against))
            if frm["section_id"] != to["section_id"]:
                raise HTTPException(422, "Both revisions must belong to the same section.")
            old, from_id = frm["body"], int(frm["id"])
        return RevisionDiff(from_id=from_id, to_id=revision_id, blocks=diff_bodies(old, to["body"]))

    return await run_tx(request, run)


@router.post(
    "/revisions/{revision_id}/rollback",
    operation_id="rollbackAdminRevision",
    summary="Admin only: publish a new revision copying an older one (gates apply)",
    response_model=Revision,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **CONFLICT, **INVALID},
)
async def rollback(
    request: Request, admin: ConsoleAdmin, revision_id: Annotated[int, Path(ge=1)], body: RollbackRequest
) -> Revision:
    section: list[str] = []

    async def run(conn: AsyncConnection[Any]) -> Revision:
        old = await _revision_row(conn, revision_id)
        sid = old["section_id"]
        section.append(sid)
        await get_section_row(conn, sid, lock=True)
        if old["status"] != "published" or not isinstance(old["body"], dict):
            raise conflict("not_restorable", "Only a published revision with a body can be restored.")
        validate_body(old["body"])
        await gate_body(conn, old["body"])
        before = await one(conn, "select body from public.chapter_sections where id = %s", (sid,))
        live_id = await _live_id(conn, sid)
        r = await one(
            conn,
            "insert into public.chapter_section_revisions (section_id, body, body_html, author_user_id, origin, note,"  # noqa: S608 - constant column list
            " status, parent_revision_id, restored_from_id, published_at, published_by)"
            " values (%s, %s, %s, %s, 'admin', %s, 'published', %s, %s, now(), %s)"
            f" returning {_REVISION_COLS.replace('r.', '')}",
            (sid, Jsonb(old["body"]), None, admin.user_id, body.note, live_id, revision_id, admin.user_id),
        )
        assert r is not None
        await publish_body(conn, admin, sid, r["body"])
        await audit(
            conn, admin, "section.rollback", "section", sid, before=before, after={"body": r["body"]}, note=body.note
        )
        return _revision(r, r["id"], with_body=True)  # type: ignore[return-value]

    out = await run_tx(request, run)
    _reindex(request, section)
    return out
