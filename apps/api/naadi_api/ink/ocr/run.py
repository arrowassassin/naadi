"""One handwriting OCR pass (the `ink_ocr` graph's work): read due layers, recognise, store, index.

Per layer: load its live items (privileged connection, filtered by layer id), split pen strokes from text boxes,
plan tiles (`raster.plan_tiles`), check the student's daily cap (vision calls counted in `llm_usage` by run id
`ink_ocr:<layer>`), render each tile with Pillow off the event loop, make one `vision` call per tile through the
gateway (purpose `job`), validate `{text, lines}`, then write `recognized_text` with a compare-and-set on the layer's
`content_hash` and index it (S07 `ref_type 'ink'`, owner set).

Outcomes: `done`; `deferred` (the job budget's daily cap, the per-call limit, the student's cap, or every provider
failing); `failed` (a reply that is not the JSON asked for: not retried until the ink changes). The provider rpm
limit leaves the layer `pending` and ends the pass (the next sweep, two minutes later, tries again). The first
daily-cap refusal ends the pass too: no other layer could be read today.

Logs carry counts and outcomes only, never recognised text.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import psycopg
from psycopg.rows import dict_row, tuple_row

from ...config_api.schemas import InkOcrRules
from ...db import Database
from ...llm import LLMUnavailable
from ...llm_budget import BudgetExhausted
from ...llm_privacy import PrivacyUnavailable
from ...logs import get_logger
from . import queue, raster
from .recognize import MAX_TEXT, Gateway, UnreadableReply, recognize

log = get_logger("naadi.ink.ocr")

Mode = Literal["sweep", "nightly"]
Outcome = Literal["done", "cleared", "skipped", "deferred", "failed", "rate_limited", "superseded"]
PASS_LOCK_KEY = 0x6E6B_6F63  # one OCR pass at a time (session-level advisory lock)
RUN_PREFIX = "ink_ocr:"
_TODAY = "created_at >= (date_trunc('day', now() at time zone 'utc') at time zone 'utc')"

# Indexes recognised text for one layer: (db, owner, ref id). None = leave it to the next full reconcile.
Indexer = Callable[[Database, uuid.UUID, str], Awaitable[None]]


@dataclass
class PassStats:
    mode: str
    claimed: int = 0
    done: int = 0
    cleared: int = 0
    skipped: int = 0
    deferred: int = 0
    failed: int = 0
    rate_limited: int = 0
    superseded: int = 0
    vision_calls: int = 0
    opted_out_cleared: int = 0
    stopped: str | None = None
    errors: int = 0
    locked: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, False)}

    @property
    def worked(self) -> bool:
        return self.claimed > 0 or self.opted_out_cleared > 0


@dataclass
class LayerResult:
    outcome: Outcome
    calls: int = 0
    stop: str | None = None


@asynccontextmanager
async def _tx(db: Database) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    if db.pool is None:
        await db.open()
    if db.pool is None:
        raise RuntimeError("DATABASE_URL is not set")
    async with db.pool.connection(timeout=10.0) as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]
        try:
            async with conn.transaction():
                await conn.execute("set local statement_timeout = 15000")
                yield conn
        finally:
            conn.row_factory = tuple_row


async def calls_today(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, prefix: str = RUN_PREFIX) -> int:
    cur = await conn.execute(
        f"select count(*)::int as n from public.llm_usage where user_id = %s and run_id like %s and {_TODAY}",  # noqa: S608
        (uid, prefix + "%"),
    )
    row = await cur.fetchone()
    return int(row["n"]) if row else 0


def compose_text(tile_texts: list[str], typed: list[str]) -> str:
    text = "\n".join(t for t in (*tile_texts, *typed) if t.strip())
    return text[:MAX_TEXT]


@dataclass
class OcrPass:
    db: Database
    gateway: Gateway
    rules: InkOcrRules
    indexer: Indexer | None = None
    render: Callable[..., bytes] = field(default=raster.render_tile)

    async def run(self, mode: Mode) -> PassStats:
        stats = PassStats(mode=mode)
        if self.db.pool is None:
            await self.db.open()
        if self.db.pool is None:
            raise RuntimeError("DATABASE_URL is not set")
        async with self.db.pool.connection(timeout=10.0) as lock_conn:
            cur = await lock_conn.execute("select pg_try_advisory_lock(%s)", (PASS_LOCK_KEY,))
            got = await cur.fetchone()
            if not got or not got[0]:
                stats.locked = True
                return stats
            try:
                await self._run_locked(mode, stats)
            finally:
                await lock_conn.execute("select pg_advisory_unlock(%s)", (PASS_LOCK_KEY,))
        return stats

    async def _run_locked(self, mode: Mode, stats: PassStats) -> None:
        if mode == "nightly":
            async with _tx(self.db) as conn:
                stats.opted_out_cleared = len(await queue.clear_opted_out(conn))
        if not self.rules.enabled:
            stats.stopped = "disabled"
            return
        statuses = ("pending",) if mode == "sweep" else ("pending", "deferred")
        sql, params = queue.claim_sql(statuses)
        idle = self.rules.idle_seconds if mode == "sweep" else 0
        async with _tx(self.db) as conn:
            cur = await conn.execute(sql, {**params, "idle": idle, "limit": self.rules.max_layers_per_run})
            due = [dict(r) for r in await cur.fetchall()]
        stats.claimed = len(due)
        for row in due:
            try:
                res = await self.layer(row["id"])
            except Exception as exc:  # noqa: BLE001 - one bad layer must not end the pass
                stats.errors += 1
                log.warning("ink_ocr.layer_error", error=type(exc).__name__)
                continue
            setattr(stats, res.outcome, getattr(stats, res.outcome) + 1)
            stats.vision_calls += res.calls
            if res.stop:
                stats.stopped = res.stop
                break

    async def _status(self, layer: dict[str, Any], status: str, content_hash: str | None) -> None:
        async with _tx(self.db) as conn:
            await conn.execute(
                queue.SET_STATUS,
                {"status": status, "hash": content_hash, "layer": layer["id"], "uid": layer["user_id"]},
            )

    async def _write(self, layer: dict[str, Any], text: str | None, content_hash: str | None) -> str | None:
        async with _tx(self.db) as conn:
            cur = await conn.execute(
                queue.WRITE_RESULT,
                {"text": text, "hash": content_hash, "status": "done", "layer": layer["id"], "uid": layer["user_id"]},
            )
            row = await cur.fetchone()
            if row is None:
                return None
            ref = f"{row['surface_type']}:{row['surface_id']}"
            if not text:
                await conn.execute(
                    "delete from public.embeddings where owner_user_id = %s and ref_type = 'ink' and ref_id = %s",
                    (layer["user_id"], ref),
                )
        if text and self.indexer is not None:
            try:
                await self.indexer(self.db, layer["user_id"], ref)
            except Exception as exc:  # noqa: BLE001 - the text is stored; the next full reconcile indexes it
                log.warning("ink_ocr.index_failed", error=type(exc).__name__)
        return str(row["ocr_status"])

    async def layer(self, layer_id: str) -> LayerResult:
        async with _tx(self.db) as conn:
            cur = await conn.execute(queue.LOAD_LAYER, (layer_id,))
            got = await cur.fetchone()
            if got is None:
                return LayerResult("skipped")
            layer = dict(got)
            if layer["opted_out"]:
                await queue.clear_user(conn, layer["user_id"])
                return LayerResult("cleared")
            cur = await conn.execute(queue.LOAD_ITEMS, (layer_id,))
            items = [r["item"] for r in await cur.fetchall()]
            used = await calls_today(conn, layer["user_id"])
        content_hash = layer["content_hash"]
        if len(items) < self.rules.min_items:
            # Erased below the threshold: stale recognised text must not stay searchable.
            await self._write(layer, None, content_hash)
            return LayerResult("cleared" if layer["has_text"] else "skipped")
        strokes, texts = raster.split_items(items)
        typed = raster.text_box_lines(texts)
        plan = raster.plan_tiles(raster.strokes_boxes(strokes), max_tiles=self.rules.max_tiles)
        tiles = plan.tiles if plan is not None else ()
        if tiles and used + len(tiles) > self.rules.per_user_daily:
            await self._status(layer, "deferred", content_hash)
            log.info("ink_ocr.layer", outcome="deferred", reason="per_user_daily", tiles=len(tiles))
            return LayerResult("deferred")
        texts_out: list[str] = []
        calls = 0
        for tile in tiles:
            assert plan is not None
            png = await asyncio.to_thread(self.render, strokes, plan, tile)
            calls += 1
            try:
                result = await recognize(
                    self.gateway, png, user_id=layer["user_id"], run_id=RUN_PREFIX + layer_id, purpose="job"
                )
            except BudgetExhausted as exc:
                if exc.reason == "rate_limited":
                    log.info("ink_ocr.layer", outcome="rate_limited", tiles=len(tiles))
                    return LayerResult("rate_limited", calls, stop="rate_limited")
                await self._status(layer, "deferred", content_hash)
                log.info("ink_ocr.layer", outcome="deferred", reason=exc.reason, tiles=len(tiles))
                return LayerResult("deferred", calls, stop="daily_cap" if exc.reason == "daily_cap" else None)
            except (LLMUnavailable, PrivacyUnavailable) as exc:
                await self._status(layer, "deferred", content_hash)
                log.info("ink_ocr.layer", outcome="deferred", reason=type(exc).__name__, tiles=len(tiles))
                return LayerResult("deferred", calls)
            except UnreadableReply:
                await self._status(layer, "failed", content_hash)
                log.info("ink_ocr.layer", outcome="failed", tiles=len(tiles))
                return LayerResult("failed", calls)
            texts_out.append(result.text)
        text = compose_text(texts_out, typed)
        status = await self._write(layer, text or None, content_hash)
        outcome: Outcome = "done" if status == "done" else ("superseded" if status else "cleared")
        log.info(
            "ink_ocr.layer",
            outcome=outcome,
            tiles=len(tiles),
            truncated=bool(plan and plan.truncated),
            chars=len(text),
            typed=len(typed),
        )
        return LayerResult(outcome, calls)


async def index_ink(db: Database, owner: uuid.UUID, ref_id: str) -> None:
    """Index one layer's recognised text with the API process's embedding engine (S07), when it has one."""
    from ...embeddings.index import reconcile
    from ...embeddings.jobs import active_index_jobs

    jobs = active_index_jobs()
    if jobs is None:
        return
    await reconcile(db, jobs.engine, kinds=("ink",), owner=owner, ref_ids=[ref_id])


__all__ = ["RUN_PREFIX", "OcrPass", "PassStats", "calls_today", "compose_text", "index_ink"]
