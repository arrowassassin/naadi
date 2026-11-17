"""The built-in placements (S15): private chapter, knowledge-base entry, figure overlay, notebook page, flashcards and
private questions.

Rules every handler here follows: rows are owned by the student (`owner_user_id` / `user_id`), reference the item's
source row (`user-provided`), are never `published` to anyone else (RLS: owned rows are visible to their owner only)
and are recorded one by one so Undo deletes exactly them. Previews hold counts, titles and ids, never an answer.
"""

from __future__ import annotations

import html
import uuid
from typing import Any

from psycopg.types.json import Jsonb

from ..extract.model import ExtractedDoc, Section
from . import content as c
from .registry import Candidate, IngestView, PlacementContext, PlacementRecord

UPLOADS_BUCKET = "user-uploads"
DEFAULT_EXAM = "step1"
MIN_CHAPTER_WORDS = 80
MAX_CARDS = 5000
MAX_QUESTIONS = 2000
_OWNED_TABLES = {
    "chapters": "owner_user_id",
    "media_assets": "owner_user_id",
    "flashcards": "owner_user_id",
    "questions": "owner_user_id",
    "kb_sources": "user_id",
    "notes": "user_id",
    "notebooks": "user_id",
}


async def _delete_owned(ctx: PlacementContext, records: list[PlacementRecord], order: tuple[str, ...]) -> None:
    """Delete recorded rows the student owns, children first (section_media links before media)."""
    by_table: dict[str, list[str]] = {}
    for r in records:
        by_table.setdefault(r.table, []).append(r.id)
    for table in order:
        ids = by_table.pop(table, [])
        if not ids:
            continue
        if table == "section_media":
            for pair in ids:
                section, _, media = pair.partition("|")
                await ctx.conn.execute(
                    "delete from public.section_media sm using public.media_assets m"
                    " where sm.section_id = %s and sm.media_id = %s and m.id = sm.media_id and m.owner_user_id = %s",
                    (section, media, ctx.user_id),
                )
            continue
        owner_col = _OWNED_TABLES.get(table)
        if owner_col is None:
            raise ValueError(f"undo of {table!r} is not supported here")
        key = "id::text" if table in ("flashcards", "kb_sources", "notes", "notebooks") else "id"
        await ctx.conn.execute(
            f"delete from public.{table} where {key} = any(%s) and {owner_col} = %s",  # noqa: S608 - constants only
            (ids, ctx.user_id),
        )


def qa_share(doc: ExtractedDoc) -> float:
    """How much of the document's text its Q&A pairs account for (1.0 = a pure deck)."""
    total = len(doc.full_text()) or 1
    return min(1.0, sum(len(q.front) + len(q.back) for q in doc.qa_pairs) / total)


def mcq_share(doc: ExtractedDoc) -> float:
    total = len(doc.full_text()) or 1
    return min(1.0, sum(len(m.stem) + sum(len(o) for o in m.options) + len(m.explanation) for m in doc.mcqs) / total)


def _pages_of(doc: ExtractedDoc) -> int:
    return doc.page_count or len(doc.pages)


def _title(view: IngestView) -> str:
    t = (view.understanding.title or view.doc.title or "").strip()
    if not t:
        stem = view.original_name.rsplit(".", 1)[0] if "." in view.original_name else view.original_name
        t = stem.replace("_", " ").strip()
    return (t or "—")[:200]


def _section_blocks(sec: Section, figure_ids: list[str], pdf_media: str | None) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if pdf_media and sec.page_start:
        blocks.append({"type": "pdf", "attrs": {"mediaId": pdf_media, "page": sec.page_start}})
    blocks.extend(c.blocks_from_text(sec.text))
    blocks.extend({"type": "figure", "attrs": {"mediaId": m}} for m in figure_ids)
    return blocks


async def _insert_media(
    ctx: PlacementContext,
    mid: str,
    *,
    kind: str,
    url: str,
    title: str | None,
    caption: str | None,
    width: int | None,
    height: int | None,
    taxonomy: list[str],
) -> PlacementRecord:
    await ctx.conn.execute(
        "insert into public.media_assets (id, kind, source_id, title, caption, url, attribution, taxonomy_ids,"
        " review_status, width, height, owner_user_id)"
        " values (%s, %s, %s, %s, %s, %s, null, %s, 'approved', %s, %s, %s)"
        " on conflict (id) do nothing",
        (mid, kind, ctx.source_id, title, caption, url, taxonomy, width, height, ctx.user_id),
    )
    return PlacementRecord("media_assets", mid, "media")


async def write_chapter(ctx: PlacementContext, kind: str, title: str, *, with_pdf: bool) -> list[PlacementRecord]:
    """A private chapter (`user_upload` / `user_kb`) with its sections, owned figures and the original PDF link."""
    view = ctx.view
    doc = view.doc
    cid = c.chapter_id(ctx.item_id)
    taxonomy = list(view.understanding.taxonomy_ids)
    records: list[PlacementRecord] = []
    await ctx.conn.execute(
        "insert into public.chapters (id, exam, kind, title, meta, sort, status, owner_user_id, source_id)"
        " values (%s, %s, %s, %s, %s, null, 'published', %s, %s)",
        (cid, DEFAULT_EXAM, kind, title, None, ctx.user_id, ctx.source_id),
    )
    records.append(PlacementRecord("chapters", cid, kind))
    pdf_media: str | None = None
    if with_pdf and view.original_path and doc.kind == "pdf":
        pdf_media = c.media_id(ctx.item_id, "pdf")
        records.append(
            await _insert_media(
                ctx,
                pdf_media,
                kind="pdf",
                url=view.original_path,
                title=title,
                caption=None,
                width=None,
                height=None,
                taxonomy=taxonomy,
            )
        )
    sections = doc.sections or [Section(heading=title, text=doc.full_text())]
    # Figures go into the section that holds their page (or the first section).
    fig_ids: dict[int, list[str]] = {}
    for fig in doc.figures:
        if not fig.storage_path:
            continue
        mid = c.media_id(ctx.item_id, fig.key)
        records.append(
            await _insert_media(
                ctx,
                mid,
                kind="image",
                url=fig.storage_path,
                title=None,
                caption=fig.caption or None,
                width=fig.width,
                height=fig.height,
                taxonomy=taxonomy,
            )
        )
        target = 0
        if fig.page:
            for i, s in enumerate(sections):
                if s.page_start and s.page_start <= fig.page <= (s.page_end or s.page_start):
                    target = i
                    break
        fig_ids.setdefault(target, []).append(mid)
    rows = []
    for i, sec in enumerate(sections, start=1):
        body = c.doc(_section_blocks(sec, fig_ids.get(i - 1, []), pdf_media))
        rows.append(
            (
                c.section_id(ctx.item_id, i),
                cid,
                i,
                (sec.heading or title)[:300],
                Jsonb(body),
                c.reading_minutes(sec.text),
                taxonomy,
            )
        )
    async with ctx.conn.cursor() as cur:
        await cur.executemany(
            "insert into public.chapter_sections (id, chapter_id, sort, heading, body, reading_minutes, taxonomy_ids)"
            " values (%s, %s, %s, %s, %s, %s, %s)",
            rows,
        )
    return records


class PrivateChapter:
    kind = "private_chapter"
    inputs: frozenset[str] = frozenset()

    def propose(self, view: IngestView) -> list[Candidate]:
        doc = view.doc
        words = doc.word_count
        if words < MIN_CHAPTER_WORDS or view.input_kind in ("video", "anki"):
            return []
        score = {
            "reading": 0.95,
            "lecture_notes": 0.85,
            "article": 0.6,
            "handwritten_notes": 0.45,
            "other": 0.5,
            "figure": 0.3,
            "flashcard_deck": 0.3,
            "question_set": 0.3,
            "schedule": 0.3,
            "fee_receipt": 0.2,
        }.get(view.understanding.kind, 0.4)
        # A document that is mostly cards or questions reads badly as a chapter, whatever the classifier said.
        if max(qa_share(doc), mcq_share(doc)) >= 0.6:
            score = min(score, 0.3)
        preview = {
            "title": _title(view),
            "sections": max(1, len(doc.sections)),
            "figures": sum(1 for f in doc.figures if f.storage_path) if doc.kind != "image" else 0,
            "pages": _pages_of(doc),
            "words": words,
            "taxonomy_ids": list(view.understanding.taxonomy_ids),
        }
        return [Candidate(self.kind, score, preview, {"title": preview["title"]})]

    async def apply(self, ctx: PlacementContext, cand: Candidate) -> list[PlacementRecord]:
        kind = "user_kb" if ctx.view.input_kind in ("link", "paste") else "user_upload"
        return await write_chapter(ctx, kind, str(cand.params.get("title") or _title(ctx.view))[:200], with_pdf=True)

    async def undo(self, ctx: PlacementContext, records: list[PlacementRecord]) -> None:
        await _delete_owned(ctx, records, ("chapters", "media_assets"))


class KbEntry:
    """Knowledge-base entry: a `kb_sources` row plus a private `user_kb` chapter (so it is readable and searchable
    for its owner only). Until S16/S17 register, schedules and receipts are offered here too."""

    kind = "kb_entry"
    inputs: frozenset[str] = frozenset()

    def propose(self, view: IngestView) -> list[Candidate]:
        doc = view.doc
        words = doc.word_count
        if view.input_kind == "link" and doc.url and words < 10:
            # A video link (oEmbed only): a reference with its title.
            score = 0.9
        elif words < 20:
            return []
        else:
            score = {"article": 0.95, "schedule": 0.75, "fee_receipt": 0.75, "other": 0.55}.get(
                view.understanding.kind, 0.4 if view.input_kind not in ("link", "paste") else 0.7
            )
        preview = {
            "title": _title(view),
            "words": words,
            "url": doc.url,
            "taxonomy_ids": list(view.understanding.taxonomy_ids),
        }
        return [Candidate(self.kind, score, preview, {"title": preview["title"]})]

    async def apply(self, ctx: PlacementContext, cand: Candidate) -> list[PlacementRecord]:
        view = ctx.view
        title = str(cand.params.get("title") or _title(view))[:200]
        kb_kind = "url" if view.input_kind == "link" else ("paste" if view.input_kind == "paste" else "file")
        kb_id = uuid.uuid5(ctx.item_id, "kb")
        cur = await ctx.conn.execute("select upload_id from public.ingest_items where id = %s", (ctx.item_id,))
        row = await cur.fetchone()
        up = row["upload_id"] if row else None
        await ctx.conn.execute(
            "insert into public.kb_sources (id, user_id, kind, url, title, upload_id, status, ingest_item_id)"
            " values (%s, %s, %s, %s, %s, %s, 'ready', %s)",
            (kb_id, ctx.user_id, kb_kind, view.doc.url, title, up, ctx.item_id),
        )
        records = [PlacementRecord("kb_sources", str(kb_id), self.kind)]
        if view.doc.word_count >= 10:
            records += await write_chapter(ctx, "user_kb", title, with_pdf=True)
        return records

    async def undo(self, ctx: PlacementContext, records: list[PlacementRecord]) -> None:
        await _delete_owned(ctx, records, ("chapters", "media_assets", "kb_sources"))


class FigureOverlay:
    """A diagram, chart or table photo attached to the matching section as the student's own figure."""

    kind = "figure_overlay"
    inputs: frozenset[str] = frozenset()

    def propose(self, view: IngestView) -> list[Candidate]:
        doc = view.doc
        if view.input_kind != "image" or not doc.image_key or not view.matches:
            return []
        fig = doc.figures[0] if doc.figures else None
        if fig is None or not fig.storage_path:
            return []
        base = 0.9 if view.understanding.kind == "figure" else 0.45
        out = []
        for i, m in enumerate(view.matches[:3]):
            preview = {
                "section_id": m["section_id"],
                "chapter_id": m["chapter_id"],
                "chapter_title": m["chapter_title"],
                "heading": m["heading"],
                "caption": fig.caption[:200],
            }
            out.append(Candidate(self.kind, max(0.05, base - 0.1 * i), preview, {"section_id": m["section_id"]}))
        return out

    async def apply(self, ctx: PlacementContext, cand: Candidate) -> list[PlacementRecord]:
        view = ctx.view
        fig = view.doc.figures[0]
        section = str(cand.params["section_id"])
        cur = await ctx.conn.execute(
            "select 1 from public.chapter_sections s join public.chapters ch on ch.id = s.chapter_id"
            " where s.id = %s and ((ch.owner_user_id is null and ch.status = 'published') or ch.owner_user_id = %s)",
            (section, ctx.user_id),
        )
        if await cur.fetchone() is None:
            raise LookupError("section not visible")
        mid = c.media_id(ctx.item_id, "figure")
        rec = await _insert_media(
            ctx,
            mid,
            kind="image",
            url=fig.storage_path or "",
            title=_title(view),
            caption=fig.caption or None,
            width=fig.width,
            height=fig.height,
            taxonomy=list(view.understanding.taxonomy_ids),
        )
        await ctx.conn.execute(
            "insert into public.section_media (section_id, media_id, sort) values (%s, %s, 1000)"
            " on conflict do nothing",
            (section, mid),
        )
        return [rec, PlacementRecord("section_media", f"{section}|{mid}", self.kind)]

    async def undo(self, ctx: PlacementContext, records: list[PlacementRecord]) -> None:
        await _delete_owned(ctx, records, ("section_media", "media_assets"))


class NotebookPage:
    """A photo of handwritten or lecture notes as a notebook page: the image plus its recognised text."""

    kind = "notebook_page"
    inputs: frozenset[str] = frozenset()

    def propose(self, view: IngestView) -> list[Candidate]:
        doc = view.doc
        if view.input_kind != "image" or not doc.figures or not doc.figures[0].storage_path:
            return []
        score = {"handwritten_notes": 0.92, "lecture_notes": 0.7, "figure": 0.35}.get(view.understanding.kind, 0.45)
        match = view.matches[0] if view.matches else None
        preview = {
            "title": _title(view),
            "words": doc.word_count,
            "section_id": match["section_id"] if match else None,
            "heading": match["heading"] if match else None,
        }
        return [Candidate(self.kind, score, preview, {"title": preview["title"], "section_id": preview["section_id"]})]

    async def apply(self, ctx: PlacementContext, cand: Candidate) -> list[PlacementRecord]:
        view = ctx.view
        title = str(cand.params.get("title") or _title(view))[:200]
        records: list[PlacementRecord] = []
        cur = await ctx.conn.execute(
            "select id from public.notebooks where user_id = %s order by updated_at desc limit 1", (ctx.user_id,)
        )
        row = await cur.fetchone()
        if row is not None:
            notebook = row["id"]
        else:
            notebook = uuid.uuid5(ctx.item_id, "notebook")
            await ctx.conn.execute(
                "insert into public.notebooks (id, user_id, title) values (%s, %s, %s)", (notebook, ctx.user_id, title)
            )
            records.append(PlacementRecord("notebooks", str(notebook), self.kind))
        cur = await ctx.conn.execute(
            "select coalesce(max(sort), -1) + 1 as next from public.notes where notebook_id = %s and kind = 'page'",
            (notebook,),
        )
        nrow = await cur.fetchone()
        sort = int(nrow["next"] or 0) if nrow else 0
        fig = view.doc.figures[0]
        path = (fig.storage_path or "").split("/", 1)[1] if fig.storage_path else None
        text = view.doc.full_text()
        blocks: list[dict[str, Any]] = []
        if path:
            blocks.append({"type": "image", "attrs": {"path": path, "alt": None}})
        blocks.extend(c.blocks_from_text(text))
        body = c.doc(blocks)
        section = cand.params.get("section_id")
        if section:
            cur = await ctx.conn.execute(
                "select 1 from public.chapter_sections s join public.chapters ch on ch.id = s.chapter_id"
                " where s.id = %s and ((ch.owner_user_id is null and ch.status = 'published')"
                " or ch.owner_user_id = %s)",
                (section, ctx.user_id),
            )
            if await cur.fetchone() is None:
                section = None
        note_id = uuid.uuid5(ctx.item_id, "page")
        await ctx.conn.execute(
            "insert into public.notes (id, user_id, notebook_id, kind, title, sort, body, body_text, anchor_type,"
            " anchor_id, canvas) values (%s, %s, %s, 'page', %s, %s, %s, %s, %s, %s, 'paper')",
            (
                note_id,
                ctx.user_id,
                notebook,
                title,
                min(sort, 100000),
                Jsonb(body),
                text[:200_000],
                "section" if section else "free",
                section,
            ),
        )
        records.insert(0, PlacementRecord("notes", str(note_id), self.kind))
        return records

    async def undo(self, ctx: PlacementContext, records: list[PlacementRecord]) -> None:
        await _delete_owned(ctx, records, ("notes", "notebooks"))


def _face(text: str) -> tuple[str, dict[str, Any]]:
    return html.escape(text).replace("\n", "<br>"), c.doc(c.blocks_from_text(text))


class Flashcards:
    kind = "flashcards"
    inputs: frozenset[str] = frozenset()

    def propose(self, view: IngestView) -> list[Candidate]:
        pairs = view.doc.qa_pairs
        if len(pairs) < 2:
            return []
        named = view.understanding.kind == "flashcard_deck" or view.input_kind == "anki"
        # A classifier's explicit kind beats a share-based guess (0.97 > 0.93); S20's anki_deck goes above both.
        score = 0.97 if named else (0.93 if qa_share(view.doc) >= 0.6 else 0.7)
        preview = {"deck": _title(view)[:120], "count": min(len(pairs), MAX_CARDS)}
        return [Candidate(self.kind, score, preview, {"deck": preview["deck"]})]

    async def apply(self, ctx: PlacementContext, cand: Candidate) -> list[PlacementRecord]:
        deck = str(cand.params.get("deck") or _title(ctx.view))[:120]
        taxonomy = list(ctx.view.understanding.taxonomy_ids)
        rows = []
        records = []
        for i, qa in enumerate(ctx.view.doc.qa_pairs[:MAX_CARDS]):
            cid = uuid.uuid5(ctx.item_id, f"card-{i}")
            fh, fd = _face(qa.front)
            bh, bd = _face(qa.back)
            rows.append((cid, ctx.user_id, deck, fh, bh, Jsonb(fd), Jsonb(bd), taxonomy))
            records.append(PlacementRecord("flashcards", str(cid), self.kind))
        async with ctx.conn.cursor() as cur:
            await cur.executemany(
                "insert into public.flashcards (id, owner_user_id, deck, front_html, back_html, front_doc, back_doc,"
                " taxonomy_ids, origin) values (%s, %s, %s, %s, %s, %s, %s, %s, 'user')",
                rows,
            )
        return records

    async def undo(self, ctx: PlacementContext, records: list[PlacementRecord]) -> None:
        await _delete_owned(ctx, records, ("flashcards",))


class PrivateQuestions:
    """The student's own MCQs as private questions: visible only to them; the answer and explanation stay hidden
    behind the same attempt gate as shared questions (column grants + the Qbank API)."""

    kind = "private_questions"
    inputs: frozenset[str] = frozenset()

    def propose(self, view: IngestView) -> list[Candidate]:
        mcqs = [m for m in view.doc.mcqs if m.answer]
        if len(mcqs) < 2:
            return []
        named = view.understanding.kind == "question_set"
        score = 0.97 if named else (0.93 if mcq_share(view.doc) >= 0.5 else 0.65)
        preview = {"title": _title(view), "count": min(len(mcqs), MAX_QUESTIONS)}
        return [Candidate(self.kind, score, preview, {})]

    async def apply(self, ctx: PlacementContext, cand: Candidate) -> list[PlacementRecord]:
        taxonomy = list(ctx.view.understanding.taxonomy_ids)
        rows = []
        records = []
        n = 0
        for m in ctx.view.doc.mcqs:
            if not m.answer or ord(m.answer) - 65 >= len(m.options):
                continue
            n += 1
            if n > MAX_QUESTIONS:
                break
            qid = c.question_id(ctx.item_id, n)
            options = [{"label": chr(65 + i), "html": html.escape(o)} for i, o in enumerate(m.options)]
            rows.append(
                (
                    qid,
                    DEFAULT_EXAM,
                    f"<p>{html.escape(m.stem)}</p>",
                    Jsonb(options),
                    m.answer,
                    c.html_from_text(m.explanation) or None,
                    taxonomy,
                    ctx.source_id,
                    ctx.user_id,
                )
            )
            records.append(PlacementRecord("questions", qid, self.kind))
        async with ctx.conn.cursor() as cur:
            await cur.executemany(
                "insert into public.questions (id, exam, stem_html, options, answer, explanation_html, taxonomy_ids,"
                " source_id, owner_user_id, status, tags) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'published',"
                " '{own}')",
                rows,
            )
        return records

    async def undo(self, ctx: PlacementContext, records: list[PlacementRecord]) -> None:
        # Attempts on the student's own questions go with them (they reference the question).
        ids = [r.id for r in records if r.table == "questions"]
        if ids:
            await ctx.conn.execute(
                "delete from public.attempts where user_id = %s and question_id = any(%s)", (ctx.user_id, ids)
            )
        await _delete_owned(ctx, records, ("questions",))


BUILTINS = (PrivateChapter(), KbEntry(), FigureOverlay(), NotebookPage(), Flashcards(), PrivateQuestions())

__all__ = ["BUILTINS", "FigureOverlay", "Flashcards", "KbEntry", "NotebookPage", "PrivateChapter", "PrivateQuestions"]
