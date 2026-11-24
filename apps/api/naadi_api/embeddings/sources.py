"""What goes into the retrieval index, per kind, as plain-text documents.

Rules (segment 07 brief, CLAUDE.md):
- only content whose `sources` row has `allow_ai_ingest` (shared content); a student's own private chapter
  is indexed for that student alone unless its source says otherwise;
- questions: stem and options only. Answer, explanations, educational objective and review data are never
  selected here, so no code path in this module can put them in an index row or a snippet;
- shared rows must be published (the same rule RLS applies); drafts and retired rows are removed;
- mock exams are never indexed (sections or questions): their answer keys are not content;
- notes and handwriting belong to their owner (`owner_user_id`), nothing else sees them;
- videos: only Naadi's own catalogue metadata (title, topics, chapter, subject, step, channel name), never anything
  from the video itself; the YouTube/Commons sources do not allow AI ingest of their content, and none is read here.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row

from ..settings import get_settings
from ..videos.query import COMMONS, names_for
from .chunker import chunk_text
from .text import html_to_text, markdown_to_text, tiptap_to_text

Kind = Literal["sections", "questions", "pathway", "notes", "ink", "videos"]
KINDS: tuple[Kind, ...] = ("sections", "questions", "pathway", "notes", "ink", "videos")
# `embeddings.ref_type` values each kind owns (a private chapter's sections are `upload` / `kb_doc`).
REF_TYPES: dict[Kind, tuple[str, ...]] = {
    "sections": ("section", "upload", "kb_doc"),
    "questions": ("question",),
    "pathway": ("pathway",),
    "notes": ("note",),
    "ink": ("ink",),
    "videos": ("video",),
}
PRIVATE_SECTION_TYPE = {"user_upload": "upload", "user_kb": "kb_doc"}


@dataclass(frozen=True)
class Doc:
    owner: uuid.UUID | None
    ref_type: str
    ref_id: str
    chunks: tuple[str, ...]

    @property
    def key(self) -> tuple[uuid.UUID | None, str, str]:
        return (self.owner, self.ref_type, self.ref_id)


def question_text(stem_html: str | None, options: Any) -> str:
    """Stem and lettered options. This is the only question text the index ever sees."""
    lines = [html_to_text(stem_html)]
    if isinstance(options, Sequence) and not isinstance(options, str | bytes):
        for opt in options:
            if isinstance(opt, Mapping):
                label = str(opt.get("label") or "").strip()
                body = html_to_text(str(opt.get("html") or ""))
                if body:
                    lines.append(f"{label}. {body}" if label else body)
    return "\n".join(line for line in lines if line)


def _ref_filter(column: str, ref_ids: Sequence[str] | None, params: dict[str, Any], name: str = "ids") -> str:
    if ref_ids is None:
        return ""
    params[name] = list(ref_ids)
    return f" and {column} = any(%({name})s)"


async def _rows(conn: psycopg.AsyncConnection[Any], sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return list(await cur.fetchall())


async def section_docs(
    conn: psycopg.AsyncConnection[Any], *, owner: uuid.UUID | None = None, ref_ids: Sequence[str] | None = None
) -> AsyncIterator[Doc]:
    params: dict[str, Any] = {}
    captions = {
        r["id"]: r["caption"] or r["title"]
        for r in await _rows(
            conn,
            "select m.id, m.caption, m.title from public.media_assets m join public.sources s on s.id = m.source_id"
            " where m.review_status = 'approved' and s.allow_ai_ingest and coalesce(m.caption, m.title) is not null",
            {},
        )
    }
    where = _ref_filter("s.id", ref_ids, params)
    if owner is not None:
        where += " and c.owner_user_id = %(owner)s"
        params["owner"] = owner
    rows = await _rows(
        conn,
        "select s.id, s.heading, s.body, s.body_html, c.owner_user_id, c.kind"  # noqa: S608 - `where` holds constants only
        " from public.chapter_sections s"
        " join public.chapters c on c.id = s.chapter_id"
        " left join public.sources src on src.id = c.source_id"
        " where c.kind <> 'mock_exam'"
        "   and ((c.owner_user_id is null and c.status = 'published' and coalesce(src.allow_ai_ingest, false))"
        "     or (c.owner_user_id is not null and coalesce(src.allow_ai_ingest, true)))"
        f"{where} order by s.id",
        params,
    )
    for r in rows:
        text = tiptap_to_text(r["body"], captions) if r["body"] else html_to_text(r["body_html"])
        chunks = chunk_text(text, heading=r["heading"])
        if chunks:
            yield Doc(r["owner_user_id"], PRIVATE_SECTION_TYPE.get(r["kind"], "section"), r["id"], tuple(chunks))


async def question_docs(
    conn: psycopg.AsyncConnection[Any], *, owner: uuid.UUID | None = None, ref_ids: Sequence[str] | None = None
) -> AsyncIterator[Doc]:
    if owner is not None:
        return  # questions are shared content
    params: dict[str, Any] = {}
    where = _ref_filter("q.id", ref_ids, params)
    # Deliberately the only question columns this module reads: id, stem_html, options.
    rows = await _rows(
        conn,
        "select q.id, q.stem_html, q.options from public.questions q"  # noqa: S608 - `where` holds constants only
        " left join public.chapters c on c.id = q.chapter_id"
        " left join public.sources src on src.id = q.source_id"
        # A student's own MCQs (owner_user_id, S15) are private: never in the shared index.
        " where q.status = 'published' and q.owner_user_id is null and coalesce(src.allow_ai_ingest, false)"
        "   and (q.chapter_id is null"
        "        or (c.owner_user_id is null and c.status = 'published' and c.kind <> 'mock_exam'))"
        f"{where} order by q.id",
        params,
    )
    for r in rows:
        text = question_text(r["stem_html"], r["options"])
        if text:
            yield Doc(None, "question", r["id"], (text,))


_ID_TOKEN = re.compile(r"\b[a-z][a-z0-9]*-\d{2}\b")
# `(erassubmit-60d)`, `(residencystart+150d)`, `(start)`: a parenthesised due-rule token, no spaces inside
_RULE_TOKEN = re.compile(r"\s*\((?:[a-z]+(?:[+-]\d+d)?)\)")
_EMPTY_CELLS = {"", "-", "\u2014", "official"}


def clean_pathway_text(text: str, title: str | None = None) -> str:
    """Pathway markdown as plain text without internal ids (`ca-01`), due-rule tokens (`(erassubmit-60d)`), a
    repeated title line, and empty or placeholder table cells (`-`, an em dash, `official`)."""
    lines: list[str] = []
    for raw in text.split("\n"):
        line = _RULE_TOKEN.sub("", _ID_TOKEN.sub("", raw))
        if " ; " in f" {line} " or ";" in line:
            cells = [c.strip() for c in line.split(";")]
            line = " ; ".join(c for c in cells if c.lower() not in _EMPTY_CELLS)
        line = line.strip(" ;").strip()
        if line and line != (title or "").strip():
            lines.append(line)
    return "\n".join(lines)


def pathway_task_text(stage_title: str | None, title: str | None, detail_md: str | None) -> tuple[str, str]:
    """(heading, body) of a pathway task: the heading carries stage and title once; the body is cleaned."""
    heading = " - ".join(x for x in (stage_title, title) if x)
    return heading, clean_pathway_text(markdown_to_text(detail_md), title)


async def pathway_docs(
    conn: psycopg.AsyncConnection[Any], *, owner: uuid.UUID | None = None, ref_ids: Sequence[str] | None = None
) -> AsyncIterator[Doc]:
    """Pathway tasks (`task:<id>`) and long-form guides (`guide:<key>`); both are Naadi-authored shared text."""
    if owner is not None:
        return
    wanted = None if ref_ids is None else set(ref_ids)
    tasks = await _rows(
        conn,
        "select t.id, t.title, t.detail_md, st.title as stage_title from public.pathway_tasks t"
        " join public.pathway_stages st on st.key = t.stage_key order by t.id",
        {},
    )
    for r in tasks:
        ref = f"task:{r['id']}"
        if wanted is not None and ref not in wanted:
            continue
        heading, body = pathway_task_text(r["stage_title"], r["title"], r["detail_md"])
        chunks = chunk_text(body, heading=heading)
        if chunks:
            yield Doc(None, "pathway", ref, tuple(chunks))
    guides = await _rows(
        conn,
        "select g.key, g.title, g.body_md from public.pathway_guides g"
        " join public.sources s on s.id = g.source_id where s.allow_ai_ingest order by g.key",
        {},
    )
    for r in guides:
        ref = f"guide:{r['key']}"
        if wanted is not None and ref not in wanted:
            continue
        chunks = chunk_text(clean_pathway_text(markdown_to_text(r["body_md"]), r["title"]), heading=r["title"])
        if chunks:
            yield Doc(None, "pathway", ref, tuple(chunks))


def note_text(r: Mapping[str, Any]) -> str:
    """A note's indexed text: its title, `body_text` (computed by the notes API from TipTap JSON or a whiteboard's
    text elements; a row written before segment 13 falls back to its TipTap body) and any OCR text. A highlight's
    quote is not indexed: it is course text the section index already holds."""
    body = r.get("body_text")
    if body is None and r.get("canvas", "paper") == "paper":
        body = tiptap_to_text(r.get("body"))
    parts = ((r.get("title") or "").strip(), (body or "").strip(), (r.get("ocr_text") or "").strip())
    return "\n".join(x for x in parts if x)


async def note_docs(
    conn: psycopg.AsyncConnection[Any], *, owner: uuid.UUID | None = None, ref_ids: Sequence[str] | None = None
) -> AsyncIterator[Doc]:
    params: dict[str, Any] = {}
    where = _ref_filter("n.id::text", ref_ids, params)
    if owner is not None:
        where += " and n.user_id = %(owner)s"
        params["owner"] = owner
    rows = await _rows(
        conn,
        "select n.id, n.user_id, n.title, n.canvas, n.body, n.body_text, n.ocr_text"  # noqa: S608
        f" from public.notes n where true{where} order by n.id",
        params,
    )
    for r in rows:
        chunks = chunk_text(note_text(r))
        if chunks:
            yield Doc(r["user_id"], "note", str(r["id"]), tuple(chunks))


async def ink_docs(
    conn: psycopg.AsyncConnection[Any], *, owner: uuid.UUID | None = None, ref_ids: Sequence[str] | None = None
) -> AsyncIterator[Doc]:
    """Recognised handwriting (`ink_layers.recognized_text`, filled by the `ink_ocr` graph); ref id is
    `<surface_type>:<surface_id>`."""
    params: dict[str, Any] = {}
    where = _ref_filter("(l.surface_type || ':' || l.surface_id)", ref_ids, params)
    if owner is not None:
        where += " and l.user_id = %(owner)s"
        params["owner"] = owner
    rows = await _rows(
        conn,
        "select l.user_id, l.surface_type, l.surface_id, l.recognized_text from public.ink_layers l"  # noqa: S608
        f" where coalesce(btrim(l.recognized_text), '') <> ''{where} order by l.surface_type, l.surface_id",
        params,
    )
    for r in rows:
        chunks = chunk_text(r["recognized_text"])
        if chunks:
            yield Doc(r["user_id"], "ink", f"{r['surface_type']}:{r['surface_id']}", tuple(chunks))


STEP_LABEL = {"step1": "Step 1", "step2ck": "Step 2 CK"}

# A catalogue video as the index and hydration read it: approved and embeddable (RLS decides at query time).
VIDEO_SQL = """
select c.media_id, c.subject, c.step, c.provider, m.title, m.channel_id,
       array(select t.title from public.video_topics t where t.id = any(c.topic_ids) order by t.sort) as topics,
       array(select distinct t.chapter from public.video_topics t
             where t.id = any(c.topic_ids) and t.chapter is not null order by t.chapter) as chapters
from public.video_catalog c join public.media_assets m on m.id = c.media_id
where m.review_status = 'approved' and m.embeddable is not false and coalesce(m.youtube_id, m.url) is not null
"""


def video_text(r: Mapping[str, Any], names: Mapping[str, str]) -> str:
    """What Naadi holds about a video: its title (the Naadi label, the refreshed YouTube title, or a Commons file
    title), topics, chapters, subject, step and channel name."""
    channel = names.get(COMMONS if r["provider"] == COMMONS else r["channel_id"] or "", "")
    parts = [
        (r["title"] or "").strip(),
        "Topics: " + "; ".join(r["topics"]) if r["topics"] else "",
        "Chapter: " + "; ".join(r["chapters"]) if r["chapters"] else "",
        " · ".join(x for x in (r["subject"], STEP_LABEL.get(r["step"], r["step"]), channel) if x),
    ]
    return "\n".join(p for p in parts if p)


async def video_docs(
    conn: psycopg.AsyncConnection[Any], *, owner: uuid.UUID | None = None, ref_ids: Sequence[str] | None = None
) -> AsyncIterator[Doc]:
    """The Videos catalogue (shared); ref id is the media id."""
    if owner is not None:
        return
    params: dict[str, Any] = {}
    where = _ref_filter("c.media_id", ref_ids, params)
    names = names_for(get_settings().config_dir)
    for r in await _rows(conn, VIDEO_SQL + where + " order by c.media_id", params):
        text = video_text(r, names)
        if text:
            yield Doc(None, "video", r["media_id"], (text,))


LOADERS = {
    "sections": section_docs,
    "questions": question_docs,
    "pathway": pathway_docs,
    "notes": note_docs,
    "ink": ink_docs,
    "videos": video_docs,
}
