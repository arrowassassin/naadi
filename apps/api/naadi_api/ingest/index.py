"""Step 6, Index: local embeddings (fastembed, S07's engine) for matching, near-duplicates and the student's index.

- `match_sections`: the shared sections closest to an item (figure overlays and notebook pages link there);
- `near_duplicate`: >= `near_dup_share` of the item's chunks have a neighbour at cosine >= `near_dup_cosine` among
  the student's own `upload`/`kb_doc` rows, mostly in one private chapter: the item is linked, not added again;
- `reindex`: S07's reconcile for exactly what a placement created (private sections, notebook pages), owner set.

Vectors are computed in S07's worker pool; nothing here logs text.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from typing import Any

import psycopg

from ..db import Database
from ..embeddings.chunker import chunk_text
from ..embeddings.engine import Engine, vector_literal
from ..logs import get_logger
from .extract.model import ExtractedDoc
from .placements.content import short_id

log = get_logger("naadi.ingest.index")

MAX_DUP_CHUNKS = 40
MATCH_TEXT_CHARS = 2000


def engine_or_none() -> Engine | None:
    from ..embeddings.jobs import active_index_jobs

    jobs = active_index_jobs()
    return jobs.engine if jobs is not None else None


async def _embed(engine: Engine, texts: list[str]) -> list[list[float]]:
    from ..embeddings.index import _index_pool

    return await asyncio.get_running_loop().run_in_executor(_index_pool(), engine.embed_passages, texts)


def _chunks(doc: ExtractedDoc) -> list[str]:
    out: list[str] = []
    for s in doc.sections or []:
        out.extend(chunk_text(s.text, heading=s.heading))
    if not out:
        out = chunk_text(doc.full_text())
    if len(out) > MAX_DUP_CHUNKS:
        step = len(out) / MAX_DUP_CHUNKS
        out = [out[int(i * step)] for i in range(MAX_DUP_CHUNKS)]
    return out


async def match_sections(
    conn: psycopg.AsyncConnection[Any], engine: Engine, text: str, *, limit: int = 3
) -> list[dict[str, Any]]:
    text = text.strip()[:MATCH_TEXT_CHARS]
    if not text:
        return []
    vec = (await _embed(engine, [text]))[0]
    cur = await conn.execute(
        "with near as (select e.ref_id, 1 - (e.embedding <=> %(v)s::extensions.halfvec) as score"
        " from public.embeddings e where e.ref_type = 'section' and e.owner_user_id is null"
        " order by e.embedding <=> %(v)s::extensions.halfvec limit 30)"
        " select s.id as section_id, c.id as chapter_id, c.title as chapter_title, s.heading, max(n.score) as score"
        " from near n join public.chapter_sections s on s.id = n.ref_id join public.chapters c on c.id = s.chapter_id"
        " where c.owner_user_id is null and c.status = 'published' and c.kind = 'chapter'"
        " group by 1, 2, 3, 4 order by max(n.score) desc limit %(limit)s",
        {"v": vector_literal(vec), "limit": limit},
    )
    return [dict(r) for r in await cur.fetchall() if float(r["score"]) >= 0.35]


async def near_duplicate(
    conn: psycopg.AsyncConnection[Any],
    engine: Engine,
    doc: ExtractedDoc,
    user_id: uuid.UUID,
    *,
    cosine: float,
    share: float,
) -> str | None:
    """The private chapter id this item duplicates, or None."""
    chunks = _chunks(doc)
    if len(chunks) < 2:
        return None
    vectors = await _embed(engine, chunks)
    chapters: Counter[str] = Counter()
    hits = 0
    for vec in vectors:
        cur = await conn.execute(
            "select e.ref_id, 1 - (e.embedding <=> %(v)s::extensions.halfvec) as sim from public.embeddings e"
            " where e.owner_user_id = %(u)s and e.ref_type in ('upload','kb_doc')"
            " order by e.embedding <=> %(v)s::extensions.halfvec limit 1",
            {"v": vector_literal(vec), "u": user_id},
        )
        row = await cur.fetchone()
        if row is not None and float(row["sim"]) >= cosine:
            hits += 1
            chapters[str(row["ref_id"]).rsplit("-s", 1)[0]] += 1
    if hits / len(vectors) < share or not chapters:
        return None
    chapter, n = chapters.most_common(1)[0]
    return chapter if n / len(vectors) >= share * 0.75 else None


async def item_for_chapter(conn: psycopg.AsyncConnection[Any], user_id: uuid.UUID, chapter_id: str) -> uuid.UUID | None:
    """The ingest item that created a private chapter (`u-<first 20 hex of the item id>`)."""
    if not chapter_id.startswith("u-"):
        return None
    prefix = chapter_id[2:22]
    cur = await conn.execute(
        "select id from public.ingest_items where user_id = %s and replace(id::text, '-', '') like %s"
        " and cardinality(placements) > 0 limit 1",
        (user_id, prefix + "%"),
    )
    row = await cur.fetchone()
    return row["id"] if row else None


async def reindex(db: Database, engine: Engine | None, user_id: uuid.UUID, records: list[dict[str, str]]) -> None:
    """Index (or, after Undo, drop) exactly what a placement wrote."""
    if engine is None:
        return
    from ..embeddings.index import reconcile

    chapters = [r["id"] for r in records if r["table"] == "chapters"]
    notes = [r["id"] for r in records if r["table"] == "notes"]
    if chapters:
        async with db.pool.connection() as conn:  # type: ignore[union-attr]
            cur = await conn.execute("select id from public.chapter_sections where chapter_id = any(%s)", (chapters,))
            ids = [r[0] for r in await cur.fetchall()]
        # After Undo the sections are gone; their ids still follow the `u-<item>-s<n>` scheme.
        prefixes = {c for c in chapters}
        if not ids:
            async with db.pool.connection() as conn:  # type: ignore[union-attr]
                cur = await conn.execute(
                    "select ref_id from public.embeddings where owner_user_id = %s and ref_type in ('upload','kb_doc')"
                    " and split_part(ref_id, '-s', 1) = any(%s)",
                    (user_id, list(prefixes)),
                )
                ids = [r[0] for r in await cur.fetchall()]
        if ids:
            await reconcile(db, engine, kinds=("sections",), owner=user_id, ref_ids=ids)
    if notes:
        await reconcile(db, engine, kinds=("notes",), owner=user_id, ref_ids=notes)


__all__ = ["engine_or_none", "item_for_chapter", "match_sections", "near_duplicate", "reindex", "short_id"]
