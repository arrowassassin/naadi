"""Export jobs: queue, build, upload, sign, expire.

A job is one row of `export_jobs` (written only here, with the service role; the student reads it through RLS).
The ZIP is built on the server from the student's own rows and files (`builder.py`), uploaded to the
`user-uploads` bucket under `{user_id}/exports/{id}.zip` (further parts `-2`, `-3`...), and offered through signed
URLs for 24 hours; after that the objects are deleted and the job is `expired`. One active job per student (a
partial unique index): a second request while one runs is 409.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg import errors as pgerrors
from psycopg import sql

from ..authoring.store import service_conn
from ..db import Database
from ..logs import get_logger
from .builder import AREAS, Built, FileEntry, build_export
from .storage import EXPORT_FOLDER, SIGNED_URL_SECONDS, ExportStorage

log = get_logger("naadi.export")

# Never exported: the export's own bookkeeping.
EXCLUDED_TABLES = frozenset({"export_jobs"})
# Vault metadata (titles, dates) is as private as the files: it follows the "Include Vault documents" tick.
VAULT_TABLES = frozenset({"vault_documents"})
STUCK_AFTER = timedelta(minutes=30)
KEEP_EXPIRED = timedelta(days=7)
# A student's correctness on an exam-mode question is withheld until the block is finished (the same rule the
# attempts API applies), so an export cannot be used to peek at graded answers during a live exam.
COLUMN_OVERRIDES: dict[str, dict[str, str]] = {
    "attempts": {
        "correct": "case when t.mode = 'tutor' or exists (select 1 from public.blocks b where b.id = t.block_id"
        " and b.finished_at is not null) then t.correct end"
    }
}


class ExportError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass
class Buckets:
    user_uploads: str
    generated_media: str
    vault: str


class DbSource:
    """The student's rows and files, as `builder.Source`."""

    def __init__(self, db: Database, storage: ExportStorage, user_id: uuid.UUID, buckets: Buckets) -> None:
        self.db = db
        self.storage = storage
        self.user_id = user_id
        self.buckets = buckets
        self._tables: list[str] | None = None

    async def load_tables(self) -> list[str]:
        async with service_conn(self.db) as conn:
            cur = await conn.execute(
                "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace"
                " join pg_attribute a on a.attrelid = c.oid and a.attname = 'user_id' and not a.attisdropped"
                " where n.nspname = 'public' and c.relkind = 'r'"
                "   and has_any_column_privilege('authenticated', c.oid, 'select') order by 1"
            )
            self._tables = [r["relname"] for r in await cur.fetchall() if r["relname"] not in EXCLUDED_TABLES]
        return self._tables

    def tables(self, include_vault: bool) -> Sequence[str]:
        assert self._tables is not None, "load_tables() first"
        return [t for t in self._tables if include_vault or t not in VAULT_TABLES]

    async def rows(self, table: str) -> AsyncIterator[dict[str, Any]]:
        async with service_conn(self.db) as conn:
            cur = await conn.execute(
                "select a.attname from pg_attribute a where a.attrelid = %s::regclass and a.attnum > 0"
                " and not a.attisdropped and has_column_privilege('authenticated', a.attrelid, a.attnum, 'select')"
                " order by a.attnum",
                (f"public.{table}",),
            )
            cols = [r["attname"] for r in await cur.fetchall()]
            over = COLUMN_OVERRIDES.get(table, {})
            select_list = sql.SQL(", ").join(
                sql.SQL("{} as {}").format(sql.SQL(over[c]), sql.Identifier(c))
                if c in over
                else sql.SQL("t.{}").format(sql.Identifier(c))
                for c in cols
            )
            order = sql.SQL("t.{}").format(sql.Identifier("created_at" if "created_at" in cols else cols[0]))
            query = sql.SQL(
                "select to_jsonb(x) as j from"
                " (select {cols} from public.{table} t where t.user_id = %s order by {order}) x"
            ).format(cols=select_list, table=sql.Identifier(table), order=order)
            async with conn.cursor(name=f"export_{uuid.uuid4().hex[:8]}") as cur2:
                await cur2.execute(query, (self.user_id,))
                while True:
                    batch = await cur2.fetchmany(500)
                    if not batch:
                        break
                    for r in batch:
                        yield dict(r["j"])

    async def files(self, include_vault: bool) -> Sequence[FileEntry]:
        areas = [("uploads", self.buckets.user_uploads), ("generated-media", self.buckets.generated_media)]
        if include_vault:
            areas.append(("vault", self.buckets.vault))
        prefix = str(self.user_id)
        out: list[FileEntry] = []
        for area, bucket in areas:
            for obj in await self.storage.list_tree(bucket, prefix, EXPORT_FOLDER if area == "uploads" else None):
                out.append(FileEntry(area, bucket, obj["path"], obj["path"].removeprefix(prefix + "/"), obj["bytes"]))
        assert {e.area for e in out} <= set(AREAS)
        return out

    async def read(self, entry: FileEntry) -> bytes:
        return await self.storage.download(entry.bucket, entry.path)


class ExportService:
    def __init__(self, db: Database, storage: ExportStorage, buckets: Buckets) -> None:
        self.db = db
        self.storage = storage
        self.buckets = buckets
        self._tasks: set[asyncio.Task[Any]] = set()

    # ── queue ──
    async def start(self, user_id: uuid.UUID, include_vault: bool) -> dict[str, Any]:
        if not self.storage.configured:
            raise ExportError(503, "export_unavailable")
        try:
            async with service_conn(self.db) as conn:
                cur = await conn.execute(
                    "insert into public.export_jobs (user_id, include_vault) values (%s, %s)"
                    " returning id, user_id, status, include_vault, storage_path, parts, bytes, expires_at, error,"
                    " created_at",
                    (user_id, include_vault),
                )
                row = await cur.fetchone()
        except pgerrors.UniqueViolation as exc:
            raise ExportError(409, "export_active") from exc
        assert row is not None
        task = asyncio.create_task(self.run(row["id"], user_id, include_vault), name="export-all")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return dict(row)

    async def _set(self, job_id: uuid.UUID, **cols: Any) -> None:
        sets = sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(k)) for k in cols)
        async with service_conn(self.db) as conn:
            await conn.execute(
                sql.SQL("update public.export_jobs set {} where id = %s").format(sets), (*cols.values(), job_id)
            )

    async def run(self, job_id: uuid.UUID, user_id: uuid.UUID, include_vault: bool) -> None:
        built: Built | None = None
        try:
            await self._set(job_id, status="running")
            source = DbSource(self.db, self.storage, user_id, self.buckets)
            await source.load_tables()
            built = await build_export(source, user_id=str(user_id), include_vault=include_vault, job_id=str(job_id))
            base = f"{user_id}/{EXPORT_FOLDER}"
            for i, part in enumerate(built.parts, start=1):
                name = f"{job_id}.zip" if i == 1 else f"{job_id}-{i}.zip"
                await self.storage.upload(self.buckets.user_uploads, f"{base}/{name}", part)
            await self._set(
                job_id,
                status="ready",
                storage_path=f"{base}/{job_id}.zip",
                parts=len(built.parts),
                bytes=built.bytes,
                expires_at=datetime.now(UTC) + timedelta(seconds=SIGNED_URL_SECONDS),
                error=None,
            )
            log.info("export.ready", job=str(job_id), parts=len(built.parts), bytes=built.bytes)
        except Exception as exc:  # recorded on the job as a stable code; the job must always finish
            code = "export_failed"
            log.exception("export.failed", error=type(exc).__name__)
            with contextlib.suppress(Exception):
                await self._set(job_id, status="failed", error=code)
        finally:
            if built is not None and built.tmpdir is not None:
                shutil.rmtree(built.tmpdir, ignore_errors=True)

    # ── read side ──
    def object_paths(self, user_id: uuid.UUID, job_id: uuid.UUID, parts: int) -> list[str]:
        base = f"{user_id}/{EXPORT_FOLDER}"
        return [f"{base}/{job_id}.zip", *(f"{base}/{job_id}-{i}.zip" for i in range(2, parts + 1))]

    async def downloads(self, job: dict[str, Any]) -> list[dict[str, Any]]:
        """Signed URLs for a ready job, valid for the rest of its 24 hours (never past `expires_at`)."""
        if job["status"] != "ready" or not self.storage.configured or job["expires_at"] is None:
            return []
        left = int((job["expires_at"] - datetime.now(UTC)).total_seconds())
        if left <= 0:
            return []
        out: list[dict[str, Any]] = []
        for i, path in enumerate(self.object_paths(job["user_id"], job["id"], int(job["parts"])), start=1):
            url = await self.storage.sign(self.buckets.user_uploads, path, min(left, SIGNED_URL_SECONDS))
            out.append({"part": i, "url": url})
        return out

    async def delete(self, user_id: uuid.UUID, job_id: uuid.UUID) -> bool:
        async with service_conn(self.db) as conn:
            cur = await conn.execute(
                "delete from public.export_jobs where id = %s and user_id = %s and status <> 'running' returning parts",
                (job_id, user_id),
            )
            row = await cur.fetchone()
        if row is None:
            return False
        with contextlib.suppress(Exception):
            await self.storage.remove(self.buckets.user_uploads, self.object_paths(user_id, job_id, int(row["parts"])))
        return True

    # ── housekeeping ──
    async def gc(self) -> dict[str, int]:
        """Delete the objects of expired jobs, fail jobs stuck by a restart, drop old expired rows."""
        stats = {"expired": 0, "stuck": 0, "dropped": 0}
        now = datetime.now(UTC)
        async with service_conn(self.db) as conn:
            cur = await conn.execute(
                "update public.export_jobs set status = 'expired' where status = 'ready' and expires_at < %s"
                " returning id, user_id, parts",
                (now,),
            )
            expired = list(await cur.fetchall())
            cur = await conn.execute(
                "update public.export_jobs set status = 'failed', error = 'interrupted'"
                " where status in ('queued', 'running') and updated_at < %s returning id",
                (now - STUCK_AFTER,),
            )
            stats["stuck"] = len(await cur.fetchall())
            cur = await conn.execute(
                "delete from public.export_jobs where status in ('expired', 'failed') and updated_at < %s returning id",
                (now - KEEP_EXPIRED,),
            )
            stats["dropped"] = len(await cur.fetchall())
        for j in expired:
            with contextlib.suppress(Exception):
                await self.storage.remove(
                    self.buckets.user_uploads, self.object_paths(j["user_id"], j["id"], j["parts"])
                )
            stats["expired"] += 1
        return stats

    async def drain(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
