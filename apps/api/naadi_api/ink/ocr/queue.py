"""Handwriting OCR queue state on `ink_layers` (migration 0017): `ocr_status`, `ocr_hash`, `ocr_at`.

Design (works with the single-process Aegra host, no extra infrastructure):

1. **Enqueue on save (debounce).** The ink save path (`ink/store.apply_changes`) calls `queue_after_save` in the
   same transaction: a layer whose `content_hash` differs from the `ocr_hash` it was last read at becomes `pending`
   (when it holds at least `min_items` live items, or still has recognised text that may now be stale). Every save
   also moves `ink_layers.updated_at`, so the layer's idle clock restarts with each stroke: that is the debounce.
2. **Sweep.** A system cron fires the `ink_ocr` graph every two minutes; it reads `pending` layers whose
   `updated_at` is older than `idle_seconds` (`claim_sql`). A session-level advisory lock lets one pass run at a
   time. No row lock is held across a model call: the result is written with a compare-and-set on `content_hash`
   (`write_result`), so a save during the call is never lost: the text is stored and the layer stays `pending`.
3. **Deferred.** The job budget or the student's daily cap -> `deferred`; the nightly pass (after the 00:00 UTC
   reset) retries `deferred` and `pending` layers.

Opt-out: `profiles.settings.handwriting_search = false` stops queueing and every run, and `clear_user` removes the
recognised text and the handwriting index rows (the settings route calls it; the nightly pass repeats it for a
profile changed by any other path).
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg

# Surfaces whose ink is read. Exam scratch paper is a timed exam's, not notes (and the assistant is off in exams).
OCR_SURFACES = ("section", "question", "flashcard", "pdf_page", "notebook_page")
OPTED_OUT = (
    "exists (select 1 from public.profiles p where p.user_id = {l}.user_id"
    " and p.settings -> 'handwriting_search' = 'false'::jsonb)"
)
_OPTED_OUT_L = OPTED_OUT.format(l="l")

QUEUE_AFTER_SAVE = f"""
update public.ink_layers l set ocr_status = 'pending'
where l.id = %(layer)s and l.surface_type = any(%(surfaces)s)
  and l.content_hash is distinct from l.ocr_hash
  and l.ocr_status is distinct from 'pending'
  and (l.item_count >= %(min_items)s or l.recognized_text is not null)
  and not {_OPTED_OUT_L}
"""  # noqa: S608 - module constants only


async def queue_after_save(conn: psycopg.AsyncConnection[Any], layer_id: str, min_items: int) -> None:
    """Mark the layer `pending` when its content changed since its last read (see the module docstring)."""
    await conn.execute(
        QUEUE_AFTER_SAVE, {"layer": layer_id, "surfaces": list(OCR_SURFACES), "min_items": max(1, min_items)}
    )


def claim_sql(statuses: tuple[str, ...]) -> tuple[str, dict[str, Any]]:
    """Layers due for a read: in `statuses`, idle for `idle` seconds, oldest change first."""
    return (
        "select l.id::text as id, l.user_id from public.ink_layers l"
        " where l.ocr_status = any(%(statuses)s) and l.surface_type = any(%(surfaces)s)"
        " and l.updated_at < now() - make_interval(secs => %(idle)s)"
        " order by l.updated_at, l.id limit %(limit)s",
        {"statuses": list(statuses), "surfaces": list(OCR_SURFACES)},
    )


LOAD_LAYER = f"""
select l.id::text as id, l.user_id, l.surface_type, l.surface_id, l.item_count, l.content_hash, l.ocr_status,
       l.recognized_text is not null as has_text, {_OPTED_OUT_L} as opted_out
from public.ink_layers l where l.id = %s
"""  # noqa: S608 - module constants only
LOAD_ITEMS = "select item from public.ink_items where layer_id = %s and not deleted order by item_id"

WRITE_RESULT = f"""
update public.ink_layers l set
  recognized_text = %(text)s,
  ocr_hash = %(hash)s,
  ocr_at = now(),
  ocr_status = case when l.content_hash is not distinct from %(hash)s then %(status)s else 'pending' end
where l.id = %(layer)s and l.user_id = %(uid)s and not {_OPTED_OUT_L}
returning l.ocr_status, l.surface_type, l.surface_id
"""  # noqa: S608 - module constants only

SET_STATUS = f"""
update public.ink_layers l set
  ocr_status = case when %(status)s = 'failed' and l.content_hash is distinct from %(hash)s then 'pending'
                    else %(status)s end,
  ocr_hash = case when %(status)s = 'failed' then %(hash)s else l.ocr_hash end,
  ocr_at = case when %(status)s = 'failed' then now() else l.ocr_at end
where l.id = %(layer)s and l.user_id = %(uid)s and not {_OPTED_OUT_L}
"""  # noqa: S608 - module constants only


async def clear_user(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID) -> int:
    """Opt-out: drop every recognised text and OCR state of the student, and their handwriting index rows."""
    cur = await conn.execute(
        "update public.ink_layers set recognized_text = null, ocr_status = null, ocr_hash = null, ocr_at = null"
        " where user_id = %s and (recognized_text is not null or ocr_status is not null or ocr_hash is not null)",
        (uid,),
    )
    await conn.execute("delete from public.embeddings where owner_user_id = %s and ref_type = 'ink'", (uid,))
    return cur.rowcount


async def requeue_user(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, min_items: int) -> int:
    """Opt back in: queue every layer with enough ink that has not been read at its current content."""
    cur = await conn.execute(
        "update public.ink_layers set ocr_status = 'pending'"
        " where user_id = %s and surface_type = any(%s) and item_count >= %s"
        " and content_hash is distinct from ocr_hash and ocr_status is distinct from 'pending'",
        (uid, list(OCR_SURFACES), max(1, min_items)),
    )
    return cur.rowcount


CLEAR_OPTED_OUT = f"""
with gone as (
  update public.ink_layers l set recognized_text = null, ocr_status = null, ocr_hash = null, ocr_at = null
  where (l.recognized_text is not null or l.ocr_status is not null) and {_OPTED_OUT_L}
  returning l.user_id
)
select distinct user_id from gone
"""  # noqa: S608 - module constants only


async def clear_opted_out(conn: psycopg.AsyncConnection[Any]) -> list[uuid.UUID]:
    """The nightly safety net: students who turned handwriting search off by any path lose what is left."""
    cur = await conn.execute(CLEAR_OPTED_OUT)
    users = [r[0] if isinstance(r, tuple) else r["user_id"] for r in await cur.fetchall()]
    await conn.execute(
        "delete from public.embeddings e where e.ref_type = 'ink' and exists (select 1 from public.profiles p"
        " where p.user_id = e.owner_user_id and p.settings -> 'handwriting_search' = 'false'::jsonb)"
    )
    return users


__all__ = [
    "LOAD_ITEMS",
    "LOAD_LAYER",
    "OCR_SURFACES",
    "SET_STATUS",
    "WRITE_RESULT",
    "apply_handwriting_setting",
    "claim_sql",
    "clear_opted_out",
    "clear_user",
    "queue_after_save",
    "requeue_user",
]


async def apply_handwriting_setting(db: Any, uid: uuid.UUID, enabled: bool, min_items: int) -> int:
    """After `profiles.settings.handwriting_search` changed (PATCH /api/me): off clears now, on queues the ink
    already there. Runs on the privileged connection (clients cannot write ink_layers). Returns rows changed."""
    if db.pool is None:
        await db.open()
    if db.pool is None:
        return 0
    async with db.pool.connection(timeout=5.0) as conn, conn.transaction():
        await conn.execute("set local statement_timeout = 10000")
        if not enabled:
            return await clear_user(conn, uid)
        return await requeue_user(conn, uid, min_items)
