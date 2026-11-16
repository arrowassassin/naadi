"""Hybrid retrieval over the local index: vector + full text, fused, reranked, then hydrated under RLS.

Two stages keep visibility honest:

1. Retrieval reads `public.embeddings` (service-side, clients cannot select it) restricted to shared rows
   and the caller's own (`owner_user_id is null or = caller`).
2. Every candidate is then re-read through `user_tx` (role `authenticated`, the caller's claims), so
   Postgres RLS has the last word: a row the caller may not see (unpublished, someone else's chapter) is
   dropped, and question rows come back through the column grants that exclude every answer column. Titles,
   links and metadata come from stage 2, never from the index.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row

from ..auth_db import user_tx
from ..db import Database
from ..logs import get_logger
from . import ranking
from .engine import Engine, vector_literal
from .text import html_to_text

log = get_logger("naadi.embeddings.query")

Scope = Literal["all", "chapters", "questions", "notes", "handwriting", "uploads", "pathway"]
SCOPE_TYPES: dict[Scope, tuple[str, ...]] = {
    "all": ("section", "upload", "kb_doc", "question", "note", "ink", "pathway", "url"),
    "chapters": ("section",),
    "questions": ("question",),
    "notes": ("note",),
    "handwriting": ("ink",),
    "uploads": ("upload", "kb_doc", "url"),
    "pathway": ("pathway",),
}
GroupName = Literal["chapters", "notes", "handwriting", "questions", "uploads", "pathway"]
GROUP_OF = {
    "section": "chapters",
    "note": "notes",
    "ink": "handwriting",
    "question": "questions",
    "upload": "uploads",
    "kb_doc": "uploads",
    "url": "uploads",
    "pathway": "pathway",
}
HNSW_EF_SEARCH = 120
DB_DEADLINE_S = 5.0
MAX_QUERY_CHARS = 300

_VECTOR_SQL = (
    "select e.id, e.ref_type, e.ref_id, e.chunk_index, e.content,"
    " 1 - (e.embedding <=> %(v)s::extensions.halfvec) as cos"
    " from public.embeddings e"
    " where e.embedding is not null and e.ref_type = any(%(types)s)"
    "   and (e.owner_user_id is null or e.owner_user_id = %(uid)s)"
    " order by e.embedding <=> %(v)s::extensions.halfvec limit %(k)s"
)
_TEXT_SQL = (
    "select e.id, e.ref_type, e.ref_id, e.chunk_index, e.content,"
    " ts_rank_cd(e.tsv, q.query, 32) as rank"
    " from public.embeddings e, to_tsquery('english', %(tsq)s) as q(query)"
    " where e.tsv @@ q.query and e.ref_type = any(%(types)s)"
    "   and (e.owner_user_id is null or e.owner_user_id = %(uid)s)"
    " order by rank desc limit %(k)s"
)


@dataclass
class Candidate:
    id: int
    ref_type: str
    ref_id: str
    chunk_index: int
    content: str
    cosine: float | None = None
    fused: float = 0.0
    rerank: float | None = None
    lexical: bool = False


@dataclass
class Hit:
    ref_type: str
    ref_id: str
    group: GroupName
    title: str
    meta: str
    snippet: list[ranking.Segment]
    target: dict[str, str | None]
    score: float
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class LocalResult:
    hits: list[Hit]
    suggest_web: bool
    candidates: int
    timings_ms: dict[str, float]


async def _fetch(conn: psycopg.AsyncConnection[Any], sql: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    cur = await conn.execute(sql, params)
    return list(await cur.fetchall())


async def retrieve(
    db: Database, engine: Engine, user_id: uuid.UUID, q: str, types: Sequence[str]
) -> tuple[list[Candidate], dict[str, float]]:
    """Vector and full-text candidates for `q`, fused with reciprocal-rank fusion, one per ref."""
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    vec = await asyncio.to_thread(engine.embed_query, q)
    timings["embed"] = (time.perf_counter() - t0) * 1000
    terms = ranking.query_terms(q)
    tsq = ranking.tsquery(terms)
    params = {"v": vector_literal(vec), "types": list(types), "uid": user_id, "k": ranking.CANDIDATES}
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    t1 = time.perf_counter()
    async with db.pool.connection(timeout=DB_DEADLINE_S) as conn, asyncio.timeout(DB_DEADLINE_S), conn.transaction():
        await conn.execute(f"set local hnsw.ef_search = {HNSW_EF_SEARCH}")
        await conn.execute("set local hnsw.iterative_scan = relaxed_order")
        await conn.execute("set local statement_timeout = 4000")
        vrows = await _fetch(conn, _VECTOR_SQL, params)
        trows = await _fetch(conn, _TEXT_SQL, {**params, "tsq": tsq}) if tsq else []
    timings["retrieve"] = (time.perf_counter() - t1) * 1000

    by_id: dict[int, Candidate] = {}
    for r in vrows:
        by_id[r[0]] = Candidate(r[0], r[1], r[2], r[3], r[4], cosine=float(r[5]))
    for r in trows:
        c = by_id.setdefault(r[0], Candidate(r[0], r[1], r[2], r[3], r[4]))
        c.lexical = True
    fused = ranking.rrf([[r[0] for r in vrows], [r[0] for r in trows]])
    for cid, score in fused.items():
        by_id[int(cid)].fused = score
    # One candidate per ref: the best chunk.
    best: dict[tuple[str, str], Candidate] = {}
    for c in sorted(by_id.values(), key=lambda c: -c.fused):
        best.setdefault((c.ref_type, c.ref_id), c)
    return sorted(best.values(), key=lambda c: -c.fused), timings


def rerank_candidates(engine: Engine, q: str, cands: list[Candidate], terms: Sequence[str]) -> None:
    """Re-order the top fused candidates with the cross-encoder, reading each one's best window."""
    top = cands[: ranking.RERANK_TOP_N]
    if not top:
        return
    docs = [ranking.best_window(c.content, terms, ranking.RERANK_WORDS) or c.content for c in top]
    for c, s in zip(top, engine.rerank(q, docs), strict=True):
        c.rerank = s


def order(cands: list[Candidate]) -> list[Candidate]:
    """Reranked head first (by cross-encoder score), then the rest in fused order."""
    head = sorted((c for c in cands if c.rerank is not None), key=lambda c: -(c.rerank or 0.0))
    tail = [c for c in cands if c.rerank is None]
    return head + tail


def relevant(cands: list[Candidate]) -> list[Candidate]:
    """Drop what the cross-encoder scores below the floor. The tail (never reranked) survives only on a full-text
    match or a close vector, and only when the reranked head held something relevant at all."""
    ordered = order(cands)
    head = [c for c in ordered if c.rerank is not None and c.rerank >= ranking.RERANK_FLOOR]
    if not head:
        return []
    tail = [c for c in ordered if c.rerank is None and (c.lexical or (c.cosine or 0.0) >= ranking.TAIL_COSINE)]
    return head + tail


def select(cands: list[Candidate], scope: Scope, limit: int) -> list[Candidate]:
    """Searching everything keeps at most PER_GROUP per result group so every group can appear."""
    if scope != "all":
        return cands[:limit]
    per: dict[str, int] = defaultdict(int)
    out: list[Candidate] = []
    for c in cands:
        g = GROUP_OF[c.ref_type]
        if per[g] >= ranking.PER_GROUP:
            continue
        per[g] += 1
        out.append(c)
        if len(out) >= limit:
            break
    return out


def _first_line(text: str, n: int = 140) -> str:
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line if len(line) <= n else line[: n - 1].rstrip() + "…"


async def hydrate(db: Database, user_id: uuid.UUID, cands: Sequence[Candidate], terms: Sequence[str]) -> list[Hit]:
    """Titles, links and metadata, read as the caller. Anything RLS hides is dropped."""
    by_type: dict[str, list[Candidate]] = defaultdict(list)
    for c in cands:
        by_type[c.ref_type].append(c)
    meta: dict[tuple[str, str], dict[str, Any]] = {}
    async with user_tx(db, user_id, deadline_s=DB_DEADLINE_S) as conn:
        conn.row_factory = dict_row
        for rt in ("section", "upload", "kb_doc"):
            ids = [c.ref_id for c in by_type.get(rt, [])]
            if not ids:
                continue
            cur = await conn.execute(
                "select s.id, s.heading, s.chapter_id, c.title as chapter_title, c.kind from public.chapter_sections s"
                " join public.chapters c on c.id = s.chapter_id where s.id = any(%s)",
                (ids,),
            )
            for r in await cur.fetchall():
                meta[(rt, r["id"])] = r
        qids = [c.ref_id for c in by_type.get("question", [])]
        if qids:
            # Column list = the client-readable columns (no answer, explanation or objective: they are not granted).
            cur = await conn.execute(
                "select q.id, q.stem_html, q.chapter_id, q.exam, q.difficulty, c.title as chapter_title"
                " from public.questions q left join public.chapters c on c.id = q.chapter_id where q.id = any(%s)",
                (qids,),
            )
            for r in await cur.fetchall():
                meta[("question", r["id"])] = r
        nids = [c.ref_id for c in by_type.get("note", [])]
        if nids:
            cur = await conn.execute(
                "select id::text as id, anchor_type, anchor_id, highlight_color from public.notes"
                " where id::text = any(%s)",
                (nids,),
            )
            for r in await cur.fetchall():
                meta[("note", r["id"])] = r
        iids = [c.ref_id for c in by_type.get("ink", [])]
        if iids:
            cur = await conn.execute(
                "select surface_type, surface_id, (surface_type || ':' || surface_id) as id from public.ink_layers"
                " where (surface_type || ':' || surface_id) = any(%s)",
                (iids,),
            )
            for r in await cur.fetchall():
                meta[("ink", r["id"])] = r
        # Notes and handwriting anchored to a section link to it: resolve the chapter (under RLS, so a section
        # the caller cannot read yields no link target).
        anchored = {
            str(m["anchor_id"] if k[0] == "note" else m["surface_id"])
            for k, m in meta.items()
            if (k[0] == "note" and m["anchor_type"] == "section" and m["anchor_id"])
            or (k[0] == "ink" and m["surface_type"] == "section")
        }
        chapter_of: dict[str, str] = {}
        if anchored:
            cur = await conn.execute(
                "select id, chapter_id from public.chapter_sections where id = any(%s)", (sorted(anchored),)
            )
            chapter_of = {r["id"]: r["chapter_id"] for r in await cur.fetchall()}
        for k, m in meta.items():
            if k[0] == "note" and m["anchor_type"] == "section":
                m["chapter_id"] = chapter_of.get(str(m["anchor_id"]))
            elif k[0] == "ink" and m["surface_type"] == "section":
                m["chapter_id"] = chapter_of.get(str(m["surface_id"]))
        pids = [c.ref_id for c in by_type.get("pathway", [])]
        if pids:
            tasks = [p.removeprefix("task:") for p in pids if p.startswith("task:")]
            guides = [p.removeprefix("guide:") for p in pids if p.startswith("guide:")]
            if tasks:
                cur = await conn.execute(
                    "select 'task:' || t.id as id, t.id as task_id, t.title, t.stage_key, st.title as stage_title"
                    " from public.pathway_tasks t join public.pathway_stages st on st.key = t.stage_key"
                    " where t.id = any(%s)",
                    (tasks,),
                )
                for r in await cur.fetchall():
                    meta[("pathway", r["id"])] = r
            if guides:
                cur = await conn.execute(
                    "select 'guide:' || key as id, key as guide_key, title, stage_key from public.pathway_guides"
                    " where key = any(%s)",
                    (guides,),
                )
                for r in await cur.fetchall():
                    meta[("pathway", r["id"])] = r

    hits: list[Hit] = []
    for c in cands:
        info = meta.get((c.ref_type, c.ref_id))
        if info is None:
            continue  # not visible to this caller (RLS), or gone since indexing
        group: GroupName = GROUP_OF[c.ref_type]  # type: ignore[assignment]
        body = ranking.drop_heading(c.content, info.get("heading") or info.get("title"))
        snippet = ranking.highlight(ranking.best_window(body, terms), terms)
        score = c.rerank if c.rerank is not None else c.fused
        if c.ref_type in ("section", "upload", "kb_doc"):
            hit = Hit(
                c.ref_type,
                c.ref_id,
                group,
                info["heading"],
                info["chapter_title"],
                snippet,
                {"type": "section", "chapter_id": info["chapter_id"], "section_id": info["id"]},
                score,
            )
        elif c.ref_type == "question":
            stem = html_to_text(info["stem_html"])
            ctx = info["chapter_title"] or str(info["exam"])
            hit = Hit(
                c.ref_type,
                c.ref_id,
                group,
                _first_line(stem, 160),
                f"Question · {ctx}",
                snippet,
                {"type": "question", "question_id": info["id"], "chapter_id": info["chapter_id"]},
                score,
            )
        elif c.ref_type == "note":
            where = {"section": "a chapter", "question": "a question", "video": "a video", "pdf": "a PDF"}.get(
                info["anchor_type"] or "", "your notebook"
            )
            hit = Hit(
                c.ref_type,
                c.ref_id,
                group,
                _first_line(c.content),
                f"Your note on {where}",
                snippet,
                {
                    "type": "note",
                    "note_id": info["id"],
                    "anchor_type": info["anchor_type"],
                    "anchor_id": info["anchor_id"],
                    "chapter_id": info.get("chapter_id"),
                    "section_id": info["anchor_id"] if info.get("chapter_id") else None,
                },
                score,
            )
        elif c.ref_type == "ink":
            hit = Hit(
                c.ref_type,
                c.ref_id,
                group,
                _first_line(c.content),
                "Found in your handwriting",
                snippet,
                {
                    "type": "ink",
                    "surface_type": info["surface_type"],
                    "surface_id": info["surface_id"],
                    "chapter_id": info.get("chapter_id"),
                    "section_id": info["surface_id"] if info.get("chapter_id") else None,
                },
                score,
            )
        else:  # pathway
            is_task = "task_id" in info
            hit = Hit(
                c.ref_type,
                c.ref_id,
                group,
                info["title"],
                info["stage_title"] if is_task else "Pathway guide",
                snippet,
                {
                    "type": "pathway",
                    "stage_key": info["stage_key"],
                    "task_id": info.get("task_id"),
                    "guide_key": info.get("guide_key"),
                },
                score,
            )
        hits.append(hit)
    return hits


async def search_local(
    db: Database, engine: Engine, user_id: uuid.UUID, q: str, *, scope: Scope = "all", limit: int = 20
) -> LocalResult:
    q = re.sub(r"\s+", " ", q).strip()[:MAX_QUERY_CHARS]
    terms = ranking.query_terms(q)
    cands, timings = await retrieve(db, engine, user_id, q, SCOPE_TYPES[scope])
    t = time.perf_counter()
    await asyncio.to_thread(rerank_candidates, engine, q, cands, terms)
    timings["rerank"] = (time.perf_counter() - t) * 1000
    kept = relevant(cands)
    chosen = select(kept, scope, limit)
    t = time.perf_counter()
    hits = await hydrate(db, user_id, chosen, terms)
    timings["hydrate"] = (time.perf_counter() - t) * 1000
    best = max((c.rerank for c in kept if c.rerank is not None), default=None)
    suggest_web = not hits or best is None or best < ranking.WEAK_RERANK
    return LocalResult(hits, suggest_web, len(cands), {k: round(v, 1) for k, v in timings.items()})
