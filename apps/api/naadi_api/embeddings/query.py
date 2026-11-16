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

from ..auth import CurrentUser
from ..auth_db import user_tx
from ..db import Database
from ..logs import get_logger
from . import ranking
from .engine import Engine, vector_literal
from .sources import clean_pathway_text, pathway_task_text, question_text
from .text import html_to_text, markdown_to_text, tiptap_to_text

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
    context: str | None  # chapter, stage or exam name; the client words it
    anchor: str | None  # a note's anchor type
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


async def _load_meta(
    conn: psycopg.AsyncConnection[Any], by_type: dict[str, list[Candidate]]
) -> dict[tuple[str, str], dict[str, Any]]:
    """Everything a hit shows, read as the caller. Each row also carries `current`: the text as it is now (so a
    snippet never shows a stale index row), and shared rows are re-checked against their source's
    `allow_ai_ingest` and for mock exams, so a row indexed before a rule changed is dropped, not shown."""
    meta: dict[tuple[str, str], dict[str, Any]] = {}
    for rt in ("section", "upload", "kb_doc"):
        ids = [c.ref_id for c in by_type.get(rt, [])]
        if not ids:
            continue
        cur = await conn.execute(
            "select s.id, s.heading, s.body, s.body_html, s.chapter_id, c.title as chapter_title, c.kind,"
            " c.owner_user_id, src.allow_ai_ingest from public.chapter_sections s"
            " join public.chapters c on c.id = s.chapter_id"
            " left join public.sources src on src.id = c.source_id where s.id = any(%s)",
            (ids,),
        )
        for r in await cur.fetchall():
            shared = r["owner_user_id"] is None
            if r["kind"] == "mock_exam" or (shared and not r["allow_ai_ingest"]):
                continue
            text = tiptap_to_text(r["body"]) if r["body"] else html_to_text(r["body_html"])
            meta[(rt, r["id"])] = {**r, "current": text}
    qids = [c.ref_id for c in by_type.get("question", [])]
    if qids:
        # Only client-readable question columns: the answer, explanations and objective are not granted to this role.
        cur = await conn.execute(
            "select q.id, q.stem_html, q.options, q.chapter_id, q.exam, c.title as chapter_title, c.kind,"
            " src.allow_ai_ingest from public.questions q"
            " left join public.chapters c on c.id = q.chapter_id"
            " left join public.sources src on src.id = q.source_id where q.id = any(%s)",
            (qids,),
        )
        for r in await cur.fetchall():
            if r["kind"] == "mock_exam" or not r["allow_ai_ingest"]:
                continue
            meta[("question", r["id"])] = {**r, "current": question_text(r["stem_html"], r["options"])}
    nids = [c.ref_id for c in by_type.get("note", [])]
    if nids:
        cur = await conn.execute(
            "select id::text as id, anchor_type, anchor_id, body, ocr_text from public.notes where id::text = any(%s)",
            (nids,),
        )
        for r in await cur.fetchall():
            text = "\n".join(x for x in (tiptap_to_text(r["body"]), (r["ocr_text"] or "").strip()) if x)
            meta[("note", r["id"])] = {**r, "current": text}
    iids = [c.ref_id for c in by_type.get("ink", [])]
    if iids:
        cur = await conn.execute(
            "select surface_type, surface_id, recognized_text, (surface_type || ':' || surface_id) as id"
            " from public.ink_layers where (surface_type || ':' || surface_id) = any(%s)",
            (iids,),
        )
        for r in await cur.fetchall():
            meta[("ink", r["id"])] = {**r, "current": (r["recognized_text"] or "").strip()}
    # Notes and handwriting anchored to a section link to it: resolve the chapter (under RLS, so a section the
    # caller cannot read yields no link target).
    anchored = {
        str(m["anchor_id"] if k[0] == "note" else m["surface_id"])
        for k, m in meta.items()
        if (k[0] == "note" and m["anchor_type"] == "section" and m["anchor_id"])
        or (k[0] == "ink" and m["surface_type"] == "section")
    }
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
    tasks = [p.removeprefix("task:") for p in pids if p.startswith("task:")]
    guides = [p.removeprefix("guide:") for p in pids if p.startswith("guide:")]
    if tasks:
        cur = await conn.execute(
            "select 'task:' || t.id as id, t.id as task_id, t.title, t.detail_md, t.stage_key, st.title as stage_title"
            " from public.pathway_tasks t join public.pathway_stages st on st.key = t.stage_key"
            " where t.id = any(%s)",
            (tasks,),
        )
        for r in await cur.fetchall():
            _, body = pathway_task_text(r["stage_title"], r["title"], r["detail_md"])
            meta[("pathway", r["id"])] = {**r, "current": body}
    if guides:
        cur = await conn.execute(
            "select 'guide:' || g.key as id, g.key as guide_key, g.title, g.body_md, g.stage_key,"
            " src.allow_ai_ingest from public.pathway_guides g left join public.sources src on src.id = g.source_id"
            " where g.key = any(%s)",
            (guides,),
        )
        for r in await cur.fetchall():
            if r["allow_ai_ingest"]:
                meta[("pathway", r["id"])] = {
                    **r,
                    "current": clean_pathway_text(markdown_to_text(r["body_md"]), r["title"]),
                }
    return meta


async def hydrate(db: Database, user: CurrentUser, cands: Sequence[Candidate], terms: Sequence[str]) -> list[Hit]:
    """Titles, links, metadata and snippets, read as the caller from the current content. Anything RLS hides, or
    that is no longer allowed in search, is dropped."""
    by_type: dict[str, list[Candidate]] = defaultdict(list)
    for c in cands:
        by_type[c.ref_type].append(c)
    async with user_tx(db, user, deadline_s=DB_DEADLINE_S) as conn:
        conn.row_factory = dict_row
        meta = await _load_meta(conn, by_type)

    hits: list[Hit] = []
    for c in cands:
        info = meta.get((c.ref_type, c.ref_id))
        if info is None:
            continue  # not visible to this caller (RLS), not allowed any more, or gone since indexing
        group: GroupName = GROUP_OF[c.ref_type]  # type: ignore[assignment]
        current = info["current"]
        snippet = ranking.highlight(ranking.best_window(current, terms), terms)
        score = c.rerank if c.rerank is not None else c.fused
        context: str | None
        anchor: str | None = None
        if c.ref_type in ("section", "upload", "kb_doc"):
            title, context = info["heading"], info["chapter_title"]
            target = {"type": "section", "chapter_id": info["chapter_id"], "section_id": info["id"]}
        elif c.ref_type == "question":
            title, context = _first_line(html_to_text(info["stem_html"]), 160), info["chapter_title"] or info["exam"]
            target = {"type": "question", "question_id": info["id"], "chapter_id": info["chapter_id"]}
        elif c.ref_type == "note":
            title, context, anchor = _first_line(current), None, info["anchor_type"]
            target = {
                "type": "note",
                "note_id": info["id"],
                "anchor_type": info["anchor_type"],
                "anchor_id": info["anchor_id"],
                "chapter_id": info.get("chapter_id"),
                "section_id": info["anchor_id"] if info.get("chapter_id") else None,
            }
        elif c.ref_type == "ink":
            title, context = _first_line(current), None
            target = {
                "type": "ink",
                "surface_type": info["surface_type"],
                "surface_id": info["surface_id"],
                "chapter_id": info.get("chapter_id"),
                "section_id": info["surface_id"] if info.get("chapter_id") else None,
            }
        else:  # pathway
            title = info["title"]
            context = info["stage_title"] if "task_id" in info else None
            target = {
                "type": "pathway",
                "stage_key": info["stage_key"],
                "task_id": info.get("task_id"),
                "guide_key": info.get("guide_key"),
            }
        hits.append(Hit(c.ref_type, c.ref_id, group, title, context, anchor, snippet, target, score))
    return hits


async def search_local(
    db: Database, engine: Engine, user: CurrentUser, q: str, *, scope: Scope = "all", limit: int = 20
) -> LocalResult:
    user_id = user.user_id
    q = re.sub(r"\s+", " ", q).strip()[:MAX_QUERY_CHARS]
    terms = ranking.query_terms(q)
    cands, timings = await retrieve(db, engine, user_id, q, SCOPE_TYPES[scope])
    t = time.perf_counter()
    await asyncio.to_thread(rerank_candidates, engine, q, cands, terms)
    timings["rerank"] = (time.perf_counter() - t) * 1000
    kept = relevant(cands)
    chosen = select(kept, scope, limit)
    t = time.perf_counter()
    hits = await hydrate(db, user, chosen, terms)
    timings["hydrate"] = (time.perf_counter() - t) * 1000
    best = max((c.rerank for c in kept if c.rerank is not None), default=None)
    suggest_web = not hits or best is None or best < ranking.WEAK_RERANK
    return LocalResult(hits, suggest_web, len(cands), {k: round(v, 1) for k, v in timings.items()})
