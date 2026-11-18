"""Keep `public.embeddings` equal to what the sources say should be indexed.

`reconcile()` is the one entry point: a full run (admin job, after the seed importer, at start-up) and an
incremental one (`ref_ids` / `owner`: S05's importer, S13's notes, S15's uploads, S19's editor) are the same
call. It is idempotent by content: a chunk whose text is unchanged and already has a vector is skipped, so a
repeat run costs a read, not a model call. Rows for refs that disappeared, were unpublished or are no
longer allowed are deleted. Embedding happens in small batches on a worker thread, each committed on its
own, so a long run never blocks the event loop and a crash resumes where it stopped.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import os
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import psycopg

from ..db import Database
from ..logs import get_logger
from .engine import Engine, vector_literal
from .sources import KINDS, LOADERS, REF_TYPES, Doc, Kind

log = get_logger("naadi.embeddings.index")

BATCH = 16
DB_DEADLINE_S = 30.0

_UPSERT = (
    "insert into public.embeddings (owner_user_id, ref_type, ref_id, chunk_index, content, embedding)"
    " values (%s, %s, %s, %s, %s, %s::extensions.halfvec)"
    " on conflict (owner_user_id, ref_type, ref_id, chunk_index)"
    " do update set content = excluded.content, embedding = excluded.embedding"
)


def _note_id(doc: Doc) -> str | None:
    if doc.ref_type == "note":
        return doc.ref_id
    if doc.ref_type == "ink" and doc.ref_id.startswith("notebook_page:"):
        return doc.ref_id.removeprefix("notebook_page:")
    return None


@dataclass
class KindReport:
    docs: int = 0
    chunks: int = 0
    embedded: int = 0
    skipped: int = 0
    deleted: int = 0


@dataclass
class IndexReport:
    kinds: dict[str, KindReport] = field(default_factory=dict)
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"seconds": round(self.seconds, 2), "kinds": {k: asdict(v) for k, v in self.kinds.items()}}


INDEX_NICE = 10  # niceness added to the indexing thread: request threads keep the CPU


def _lower_priority() -> None:
    """Initializer of the dedicated indexing thread (Linux: niceness is per thread; elsewhere a no-op)."""
    with contextlib.suppress(AttributeError, OSError):
        tid = threading.get_native_id()
        os.setpriority(os.PRIO_PROCESS, tid, min(19, os.getpriority(os.PRIO_PROCESS, tid) + INDEX_NICE))


_POOL: concurrent.futures.ThreadPoolExecutor | None = None


def _index_pool() -> concurrent.futures.ThreadPoolExecutor:
    """One worker thread, started at a lower priority than the asyncio default pool that serves queries and
    reranking, so a long index run slows searches as little as the scheduler allows."""
    global _POOL
    if _POOL is None:
        _POOL = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="embeddings-index", initializer=_lower_priority
        )
    return _POOL


Key = tuple[uuid.UUID | None, str, str, int]


async def _existing(
    conn: psycopg.AsyncConnection[Any], kind: Kind, owner: uuid.UUID | None, ref_ids: Sequence[str] | None
) -> dict[Key, tuple[int, str, bool]]:
    sql = (
        "select id, owner_user_id, ref_type, ref_id, chunk_index, content, embedding is not null"
        " from public.embeddings where ref_type = any(%(types)s)"
    )
    params: dict[str, Any] = {"types": list(REF_TYPES[kind])}
    if owner is not None:
        sql += " and owner_user_id = %(owner)s"
        params["owner"] = owner
    if ref_ids is not None:
        sql += " and ref_id = any(%(ids)s)"
        params["ids"] = list(ref_ids)
    cur = await conn.execute(sql, params)
    return {(r[1], r[2], r[3], r[4]): (r[0], r[5], bool(r[6])) for r in await cur.fetchall()}


async def reconcile(
    db: Database,
    engine: Engine,
    *,
    kinds: Sequence[Kind] = KINDS,
    owner: uuid.UUID | None = None,
    ref_ids: Sequence[str] | None = None,
) -> IndexReport:
    """Bring the index in line with the sources for `kinds` (optionally one owner, or specific ref ids)."""
    if not db.configured:
        raise RuntimeError("DATABASE_URL is not set")
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    started = time.monotonic()
    report = IndexReport()
    for kind in kinds:
        rep = report.kinds.setdefault(kind, KindReport())
        async with db.pool.connection(timeout=DB_DEADLINE_S) as conn:
            docs: list[Doc] = [d async for d in LOADERS[kind](conn, owner=owner, ref_ids=ref_ids)]
            existing = await _existing(conn, kind, owner, ref_ids)
        wanted: set[Key] = set()
        todo: list[tuple[Doc, int, str]] = []
        for doc in docs:
            rep.docs += 1
            for i, chunk in enumerate(doc.chunks):
                key: Key = (doc.owner, doc.ref_type, doc.ref_id, i)
                wanted.add(key)
                rep.chunks += 1
                have = existing.get(key)
                if have is not None and have[1] == chunk and have[2]:
                    rep.skipped += 1
                else:
                    todo.append((doc, i, chunk))
        stale = [v[0] for k, v in existing.items() if k not in wanted]
        for start in range(0, len(todo), BATCH):
            batch = todo[start : start + BATCH]
            vectors = await asyncio.get_running_loop().run_in_executor(
                _index_pool(), engine.embed_passages, [c for _, _, c in batch]
            )
            async with db.pool.connection(timeout=DB_DEADLINE_S) as conn, conn.transaction(), conn.cursor() as cur:
                note_ids = [_note_id(doc) for doc, _, _ in batch]
                existing_notes: set[tuple[uuid.UUID, str]] = set()
                if any(note_ids):
                    # A note may have been deleted while inference ran. Hold a lock against deletion until
                    # these writes commit, so the delete either wins here or removes the newly written rows.
                    await cur.execute(
                        "select user_id, id::text from public.notes where id = any(%s::uuid[])"
                        " order by id for key share",
                        ([note_id for note_id in note_ids if note_id is not None],),
                    )
                    existing_notes.update(await cur.fetchall())
                writes = [
                    (doc.owner, doc.ref_type, doc.ref_id, i, chunk, vector_literal(vec))
                    for (doc, i, chunk), vec, note_id in zip(batch, vectors, note_ids, strict=True)
                    if note_id is None or (doc.owner, note_id) in existing_notes
                ]
                if writes:
                    await cur.executemany(_UPSERT, writes)
            rep.embedded += len(writes)
        if stale:
            async with db.pool.connection(timeout=DB_DEADLINE_S) as conn:
                await conn.execute("delete from public.embeddings where id = any(%s)", (stale,))
            rep.deleted = len(stale)
        log.info("embeddings.reconciled", kind=kind, **asdict(rep))
    report.seconds = time.monotonic() - started
    return report


async def index_counts(db: Database) -> dict[str, int]:
    """Rows per ref_type, for the admin status endpoint and the health of the index."""
    if not db.configured:
        return {}
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    async with db.pool.connection(timeout=5.0) as conn:
        cur = await conn.execute("select ref_type, count(*) from public.embeddings group by 1 order by 1")
        return {str(r[0]): int(r[1]) for r in await cur.fetchall()}
