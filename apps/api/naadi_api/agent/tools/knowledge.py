"""Read tools over the student's own knowledge base: `search_knowledge` and `get_page`.

Questions reach the model only as stem and options, and only after the student's attempt was revealed (tutor
mode or a finished block) do their answer and explanation come along; mock-exam content never. Everything else is
read as the student (RLS), so a hit the student cannot open is never shown either.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from pydantic import BaseModel, Field

from ...assistant.content import doc_text
from ...assistant.questions import question_for_assistant
from ...cards import routes as cards
from ...db import DatabaseNotConfigured
from ...embeddings.engine import ModelsNotCached, get_engine
from ...embeddings.query import Scope, search_local
from ...embeddings.text import html_to_text, tiptap_to_text
from ...logs import get_logger
from ...settings import get_settings
from .executor import ToolRefused
from .registry import ToolContext, ToolResult, page_resolver, register_tool

log = get_logger("naadi.agent.tools.knowledge")

GROUP_LAYER = {
    "chapters": "course",
    "questions": "course",
    "pathway": "course",
    "notes": "notes",
    "handwriting": "notes",
    "uploads": "notes",
}
SECTION_MAX_CHARS = 6000


class SearchKnowledgeArgs(BaseModel):
    query: str = Field(min_length=2, max_length=300, description="What to look for, in a few words")
    scope: Scope = Field(default="all", description="all, chapters, questions, notes, handwriting, uploads or pathway")


def citation_of_hit(i: int, h: Any) -> dict[str, Any]:
    return {
        "id": f"kb:{h.ref_type}:{h.ref_id}",
        "title": h.title[:200],
        "source": h.context or h.group,
        "layer": GROUP_LAYER.get(h.group, "course"),
        "url": None,
        "target": dict(h.target),
        "license": None,
        "n": i,
    }


async def search_knowledge(ctx: ToolContext, args: SearchKnowledgeArgs) -> ToolResult:
    """Search the student's course chapters, own notes, handwriting, uploads and the pathway guide (and questions
    they have already answered). Use this first, before any web search. Returns numbered passages to cite as [n]."""
    try:
        engine = get_engine(get_settings().config_dir)
        res = await search_local(ctx.db, engine, ctx.user, args.query, scope=args.scope, limit=8)
    except ModelsNotCached:
        return ToolResult(content="[error: search_unavailable]", status="error")
    except (DatabaseNotConfigured, psycopg.OperationalError, TimeoutError):
        return ToolResult(content="[error: search_unavailable]", status="error")
    qids = [h.ref_id for h in res.hits if h.ref_type == "question"]
    revealed = await ctx.revealed(qids) if qids else set()
    hits = [h for h in res.hits if h.ref_type != "question" or h.ref_id in revealed][:6]
    best = max((h.score for h in hits), default=0.0)
    ctx.kb_best = best if ctx.kb_best is None else max(ctx.kb_best, best)
    if not hits:
        return ToolResult(content="No passages found in the student's knowledge base.", kb_best=0.0)
    lines: list[str] = []
    citations: list[dict[str, Any]] = []
    for i, h in enumerate(hits, start=1):
        snippet = "".join(seg.text for seg in h.snippet).strip()
        where = f" ({h.context})" if h.context else ""
        lines.append(f"[{i}] {h.title}{where}: {snippet}")
        citations.append(citation_of_hit(i, h))
    return ToolResult(
        content="\n".join(lines),
        citations=citations,
        blocks=[{"type": "sources", "data": {"items": citations}}],
        kb_best=best,
    )


class PageArgs(BaseModel):
    type: str = Field(
        default="current",
        pattern=r"^[a-z][a-z0-9_]{0,31}$",
        description="'current' reads the page the student is on; else section, question, flashcard, note, video, "
        "or another page kind the app offers (e.g. pdf_page with id '<upload id>:<page>')",
    )
    id: str | None = Field(default=None, max_length=128, description="Id of the item (not needed for 'current')")


async def _section(ctx: ToolContext, section_id: str) -> ToolResult:
    async with ctx.user_tx() as conn:
        cur = await conn.execute(
            "select s.id, s.heading, s.body, s.body_html, s.chapter_id, c.title as chapter_title, c.kind"
            " from public.chapter_sections s join public.chapters c on c.id = s.chapter_id where s.id = %s",
            (section_id,),
        )
        row = await cur.fetchone()
    if row is None or row["kind"] == "mock_exam":
        raise ToolRefused("not_found")
    text = tiptap_to_text(row["body"]) if row["body"] else html_to_text(row["body_html"])
    if len(text) > SECTION_MAX_CHARS:
        text = text[: SECTION_MAX_CHARS - 1] + "…"
    cite = {
        "id": f"kb:section:{row['id']}",
        "title": row["heading"][:200],
        "source": row["chapter_title"],
        "layer": "course",
        "url": None,
        "target": {"type": "section", "chapter_id": row["chapter_id"], "section_id": row["id"]},
        "license": None,
    }
    return ToolResult(content=f"Section: {row['heading']} ({row['chapter_title']})\n{text}", citations=[cite])


def _options_text(options: Any) -> str:
    out = []
    for o in options or []:
        if isinstance(o, dict):
            out.append(f"{o.get('label')}. {html_to_text(str(o.get('html') or ''))}")
    return "\n".join(out)


async def _question(ctx: ToolContext, question_id: str) -> ToolResult:
    q = await question_for_assistant(ctx.db, ctx.user_id, question_id)
    if q is None:
        raise ToolRefused("not_found")
    parts = [f"Question {q['id']}:", html_to_text(q["stem_html"]), _options_text(q["options"])]
    if q["revealed"]:
        parts.append(f"The student chose {q.get('chosen') or 'nothing'}. Correct answer: {q.get('answer')}.")
        for label, key in (
            ("Explanation", "explanation_html"),
            ("Why not the others", "why_not_html"),
            ("Educational objective", "educational_objective"),
        ):
            value = q.get(key)
            if value:
                parts.append(f"{label}: {html_to_text(str(value))}")
    else:
        parts.append(
            "The student has NOT answered this question yet. Do not reveal, hint at or rule out the answer;"
            " help them reason about the concepts only."
        )
    return ToolResult(content="\n".join(p for p in parts if p))


async def _flashcard(ctx: ToolContext, card_id: str) -> ToolResult:
    try:
        cid = uuid.UUID(card_id)
    except ValueError as exc:
        raise ToolRefused("not_found") from exc

    async def read(conn: psycopg.AsyncConnection[Any]) -> dict[str, Any] | None:
        return await cards._visible_card(conn, ctx.user_id, cid)

    row = await cards.as_service(ctx.db, read)
    if row is None:
        raise ToolRefused("not_found")
    card = cards._card(row, ctx.user_id)
    if ctx.page.ask_kind == "hint":
        return ToolResult(
            content=f"Flashcard front: {card.front_text}\n(The student asked for a hint: the back is withheld."
            " Give a nudge, not the answer.)"
        )
    return ToolResult(content=f"Flashcard front: {card.front_text}\nBack: {card.back_text}")


async def _note(ctx: ToolContext, note_id: str) -> ToolResult:
    try:
        nid = uuid.UUID(note_id)
    except ValueError as exc:
        raise ToolRefused("not_found") from exc
    async with ctx.user_tx() as conn:
        cur = await conn.execute("select id, body, ocr_text from public.notes where id = %s", (nid,))
        row = await cur.fetchone()
    if row is None:
        raise ToolRefused("not_found")
    text = "\n".join(x for x in (doc_text(row["body"] or {}, 6000), (row["ocr_text"] or "").strip()) if x)
    return ToolResult(content=f"The student's note:\n{text or '(empty)'}")


async def _video(ctx: ToolContext, media_id: str | None) -> ToolResult:
    video = ctx.page.video or {}
    mid = media_id or video.get("media_id")
    if not mid:
        raise ToolRefused("not_found")
    async with ctx.user_tx() as conn:
        cur = await conn.execute(
            "select id, title, caption, duration_s from public.media_assets where id = %s", (str(mid),)
        )
        row = await cur.fetchone()
    if row is None:
        raise ToolRefused("not_found")
    t = video.get("t") if video.get("media_id") == mid else None
    at = f" The student is at {int(t) // 60}:{int(t) % 60:02d}." if isinstance(t, int | float) else ""
    return ToolResult(content=f"Video: {row['title'] or row['id']}. {row['caption'] or ''}{at}".strip())


async def get_page(ctx: ToolContext, args: PageArgs) -> ToolResult:
    """Read the page the student is on (or a section, question, flashcard, note or video by id). An unanswered
    question comes back as stem and options only; its answer is available only after the student answers."""
    kind: str = args.type
    ident = args.id
    if kind == "current":
        page = ctx.page
        builtin = ("section", "question", "flashcard", "note", "video")
        resolvable = page.type is not None and page_resolver(page.type, ctx.flags) is not None
        kind = page.type if page.type is not None and (page.type in builtin or resolvable) else "none"
        ident = page.question_id if kind == "question" else page.id
        if kind == "none" or (not ident and kind != "video"):
            return ToolResult(content="The student is not on a reading, question, card or note page.")
    if kind == "section":
        return await _section(ctx, str(ident))
    if kind == "question":
        return await _question(ctx, str(ident))
    if kind == "flashcard":
        return await _flashcard(ctx, str(ident))
    if kind == "note":
        return await _note(ctx, str(ident))
    if kind == "video":
        return await _video(ctx, ident)
    resolver = page_resolver(kind, ctx.flags)
    if resolver is None:
        raise ToolRefused("unknown_page_kind")
    if not ident:
        raise ToolRefused("page_id_required")
    return await resolver.fn(ctx, str(ident))


register_tool("search_knowledge", search_knowledge, writes=False)
register_tool("get_page", get_page, writes=False)
