"""Database access for ingest (the pipeline's privileged connection; every statement filters on the owner).

Clients read their own rows through RLS (migration 0028); every write here is the pipeline's. No statement logs or
returns extracted text except `load_extract`, which the pipeline alone calls.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb

from ..db import Database, DatabaseNotConfigured
from .extract.model import ExtractedDoc

Row = dict[str, Any]
STATUS_PROGRESS = {
    "received": 5,
    "extracting": 20,
    "needs_confirmation": 70,
    "placing": 85,
    "ready": 100,
    "failed": 100,
    "vaulted": 100,
}
ITEM_COLUMNS = (
    "i.id, i.user_id, i.upload_id, i.kb_source_id, i.detected_kind, i.sensitivity, i.proposed_placement,"
    " i.confirmed_placement, i.status, i.error, i.created_at, i.updated_at, i.input_kind, i.sha256, i.bytes,"
    " i.pages, i.progress, i.run_id, i.thread_id, i.batch_id, i.duplicate_of, i.placements, i.stage_detail,"
    " u.original_name, u.storage_path, u.kind as upload_kind, u.mime, u.processed_chapter_id,"
    " k.url as kb_url, k.title as kb_title"
)
ITEM_FROM = (
    "public.ingest_items i left join public.uploads u on u.id = i.upload_id"
    " left join public.kb_sources k on k.id = i.kb_source_id"
)


@asynccontextmanager
async def service_tx(db: Database, *, timeout_ms: int = 30_000) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    if not db.configured:
        raise DatabaseNotConfigured("DATABASE_URL is not set")
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    async with db.pool.connection(timeout=10.0) as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]
        try:
            async with conn.transaction():
                await conn.execute(f"set local statement_timeout = {int(timeout_ms)}")
                yield conn
        finally:
            conn.row_factory = tuple_row


async def one(
    conn: psycopg.AsyncConnection[Any], sql: str, params: Sequence[Any] | Mapping[str, Any] = ()
) -> Row | None:
    cur = await conn.execute(sql, params)
    row = await cur.fetchone()
    return dict(row) if row is not None else None


async def rows(
    conn: psycopg.AsyncConnection[Any], sql: str, params: Sequence[Any] | Mapping[str, Any] = ()
) -> list[Row]:
    cur = await conn.execute(sql, params)
    return [dict(r) for r in await cur.fetchall()]


async def get_item(conn: psycopg.AsyncConnection[Any], item_id: uuid.UUID, user_id: uuid.UUID) -> Row | None:
    return await one(
        conn,
        f"select {ITEM_COLUMNS} from {ITEM_FROM} where i.id = %s and i.user_id = %s",  # noqa: S608 - constants only
        (item_id, user_id),
    )


async def set_status(
    conn: psycopg.AsyncConnection[Any],
    item_id: uuid.UUID,
    user_id: uuid.UUID,
    status: str,
    *,
    error: str | None = None,
    progress: int | None = None,
    detail: Mapping[str, Any] | None = None,
    **cols: Any,
) -> None:
    sets = ["status = %(status)s", "error = %(error)s", "progress = %(progress)s"]
    params: dict[str, Any] = {
        "status": status,
        "error": error,
        "progress": STATUS_PROGRESS[status] if progress is None else max(0, min(100, progress)),
        "id": item_id,
        "uid": user_id,
    }
    if detail is not None:
        sets.append("stage_detail = stage_detail || %(detail)s")
        params["detail"] = Jsonb(dict(detail))
    for k, v in cols.items():
        if k not in {
            "input_kind",
            "sha256",
            "bytes",
            "pages",
            "run_id",
            "thread_id",
            "duplicate_of",
            "detected_kind",
            "sensitivity",
            "proposed_placement",
            "confirmed_placement",
        }:
            raise ValueError(k)
        sets.append(f"{k} = %({k})s")
        params[k] = Jsonb(v) if isinstance(v, dict) else v
    await conn.execute(
        f"update public.ingest_items set {', '.join(sets)} where id = %(id)s and user_id = %(uid)s",  # noqa: S608
        params,
    )


async def set_progress(
    conn: psycopg.AsyncConnection[Any], item_id: uuid.UUID, user_id: uuid.UUID, progress: int
) -> None:
    await conn.execute(
        "update public.ingest_items set progress = greatest(progress, %s) where id = %s and user_id = %s",
        (max(0, min(99, progress)), item_id, user_id),
    )


async def set_upload(conn: psycopg.AsyncConnection[Any], upload_id: uuid.UUID, user_id: uuid.UUID, **cols: Any) -> None:
    allowed = {"status", "kind", "sha256", "mime", "bytes", "storage_path", "processed_chapter_id"}
    if not cols or set(cols) - allowed:
        raise ValueError(sorted(set(cols) - allowed))
    sets = ", ".join(f"{k} = %({k})s" for k in cols)
    await conn.execute(
        f"update public.uploads set {sets} where id = %(id)s and user_id = %(uid)s",  # noqa: S608 - keys allow-listed
        {**cols, "id": upload_id, "uid": user_id},
    )


async def save_extract(
    conn: psycopg.AsyncConnection[Any], item_id: uuid.UUID, user_id: uuid.UUID, doc: ExtractedDoc
) -> None:
    await conn.execute(
        "insert into public.ingest_extracts (item_id, user_id, data) values (%s, %s, %s)"
        " on conflict (item_id) do update set data = excluded.data, created_at = now()",
        (item_id, user_id, doc.dumps()),
    )


async def load_extract(
    conn: psycopg.AsyncConnection[Any], item_id: uuid.UUID, user_id: uuid.UUID
) -> ExtractedDoc | None:
    row = await one(
        conn, "select data from public.ingest_extracts where item_id = %s and user_id = %s", (item_id, user_id)
    )
    return ExtractedDoc.loads(bytes(row["data"])) if row else None


async def prune_extracts(conn: psycopg.AsyncConnection[Any], user_id: uuid.UUID, undo_days: int) -> int:
    """Extracts of items that are done (ready, failed) and past the Undo window."""
    cur = await conn.execute(
        "delete from public.ingest_extracts e using public.ingest_items i"
        " where e.item_id = i.id and e.user_id = %s and i.status in ('ready','failed')"
        " and i.updated_at < now() - make_interval(days => %s)",
        (user_id, undo_days),
    )
    return cur.rowcount


async def exact_duplicate(
    conn: psycopg.AsyncConnection[Any], user_id: uuid.UUID, sha256: str, item_id: uuid.UUID
) -> uuid.UUID | None:
    row = await one(
        conn,
        "select id from public.ingest_items where user_id = %s and sha256 = %s and id <> %s and duplicate_of is null"
        " and status in ('ready','needs_confirmation','placing','extracting','received') order by created_at limit 1",
        (user_id, sha256, item_id),
    )
    return row["id"] if row else None


async def items_today(conn: psycopg.AsyncConnection[Any], user_id: uuid.UUID) -> int:
    row = await one(
        conn,
        "select count(*)::int as n from public.ingest_items where user_id = %s"
        " and created_at >= (date_trunc('day', now() at time zone 'utc') at time zone 'utc') and duplicate_of is null",
        (user_id,),
    )
    return int(row["n"]) if row else 0


async def vision_calls_today(conn: psycopg.AsyncConnection[Any], user_id: uuid.UUID) -> int:
    row = await one(
        conn,
        "select count(*)::int as n from public.llm_usage where user_id = %s and role = 'vision'"
        " and run_id like 'ingest:%%'"
        " and created_at >= (date_trunc('day', now() at time zone 'utc') at time zone 'utc')",
        (user_id,),
    )
    return int(row["n"]) if row else 0


async def storage_used(conn: psycopg.AsyncConnection[Any], user_id: uuid.UUID, buckets: Sequence[str]) -> int:
    """Bytes the student stores in Storage (every object under their own folder of the given buckets)."""
    row = await one(
        conn,
        "select coalesce(sum(coalesce((metadata->>'size')::bigint, 0)), 0)::bigint as n from storage.objects"
        " where bucket_id = any(%s) and (storage.foldername(name))[1] = %s",
        (list(buckets), str(user_id)),
    )
    return int(row["n"]) if row else 0


async def add_placements(
    conn: psycopg.AsyncConnection[Any], item_id: uuid.UUID, user_id: uuid.UUID, records: Sequence[Mapping[str, str]]
) -> None:
    await conn.execute(
        "update public.ingest_items set placements = %s::jsonb[] where id = %s and user_id = %s",
        ([Jsonb(dict(r)) for r in records], item_id, user_id),
    )


async def taxonomy(conn: psycopg.AsyncConnection[Any], exam: str = "step1") -> dict[str, str]:
    out = await rows(
        conn,
        "select id, label from public.taxonomy_nodes where kind in ('system','discipline') and %s = any(exam)"
        " order by kind desc, sort, id",
        (exam,),
    )
    return {r["id"]: r["label"] for r in out}


__all__ = [
    "ITEM_COLUMNS",
    "ITEM_FROM",
    "STATUS_PROGRESS",
    "add_placements",
    "exact_duplicate",
    "get_item",
    "items_today",
    "load_extract",
    "one",
    "prune_extracts",
    "rows",
    "save_extract",
    "service_tx",
    "set_progress",
    "set_status",
    "set_upload",
    "storage_used",
    "taxonomy",
    "vision_calls_today",
]
