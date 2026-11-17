"""Ink layer storage: surface checks (as the user, under RLS), the merge upsert and the tombstone purge.

Writes run on the API's privileged connection (clients have SELECT only on ink_layers / ink_items, migration 0009)
and every statement filters on the caller's id explicitly. The surface check runs first, as the user, so a surface
the caller cannot see is a 404 whether or not it exists.

Merge order (`mergeInkItems` in packages/shared/src/ink.ts): a higher `updatedAt` wins; on a tie a tombstone beats a
live item; on a full tie the stored row stays (the client compares JSON strings, which Python cannot reproduce, so
the server's copy is the tie-break and clients adopt it from `remote`).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg

from .models import INK_MAX_ITEMS

Row = dict[str, Any]

# A client clock more than this far ahead of the server is clamped to the server clock, so a device with a wrong
# date cannot win every future merge.
FUTURE_SKEW_MS = 5 * 60 * 1000
TOMBSTONE_HORIZON_DAYS = 90
PDF_MAX_PAGE = 10_000
SURFACE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,199}$")
_PAIR_RE = re.compile(r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):([1-9][0-9]{0,4})$")


class SurfaceNotFound(Exception):
    """Unknown surface type, malformed id, or a surface the caller cannot see (always a plain 404)."""


class TooManyItems(Exception):
    def __init__(self, live: int) -> None:
        super().__init__(live)
        self.live = live


@dataclass(frozen=True)
class Surface:
    type: str
    id: str
    # Revision of the content under the ink (md5 of the section body, question stem + options, card front);
    # None for surfaces whose content is the ink itself or a page image.
    content_rev: str | None


def _uuid(raw: str) -> str:
    try:
        u = uuid.UUID(raw)
    except ValueError as exc:
        raise SurfaceNotFound from exc
    if str(u) != raw:
        raise SurfaceNotFound
    return raw


async def _one(conn: psycopg.AsyncConnection[Any], query: str, params: tuple[Any, ...] | dict[str, Any]) -> Row | None:
    cur = await conn.execute(query, params)
    row = await cur.fetchone()
    return dict(row) if row is not None else None


async def resolve_surface(conn: psycopg.AsyncConnection[Any], surface_type: str, surface_id: str) -> Surface:
    """Check that the caller may ink this surface. `conn` runs as the user (auth_db.user_tx): RLS decides."""
    if not SURFACE_ID_RE.fullmatch(surface_id):
        raise SurfaceNotFound
    if surface_type == "section":
        row = await _one(
            conn,
            "select md5(coalesce(body::text, body_html, '')) as rev from public.chapter_sections where id = %s",
            (surface_id,),
        )
    elif surface_type == "question":
        # The layer covers the stem and the options only; the explanation never carries ink.
        row = await _one(
            conn, "select md5(stem_html || options::text) as rev from public.questions where id = %s", (surface_id,)
        )
    elif surface_type == "flashcard":
        row = await _one(
            conn, "select md5(front_html) as rev from public.flashcards where id = %s", (_uuid(surface_id),)
        )
    elif surface_type == "notebook_page":
        row = await _one(
            conn,
            "select null::text as rev from public.notes where id = %s and user_id = (select auth.uid())",
            (_uuid(surface_id),),
        )
    elif surface_type in ("exam_scratch", "pdf_page"):
        m = _PAIR_RE.fullmatch(surface_id)
        if not m:
            raise SurfaceNotFound
        owner_id, n = m.group(1), int(m.group(2))
        if surface_type == "exam_scratch":
            row = await _one(
                conn,
                "select null::text as rev from public.blocks where id = %s and user_id = (select auth.uid())"
                " and %s <= coalesce(array_length(question_ids, 1), 0)",
                (owner_id, n),
            )
        else:
            row = await _one(
                conn,
                "select null::text as rev from public.uploads where id = %s and user_id = (select auth.uid())"
                " and kind = 'pdf' and %s <= %s",
                (owner_id, n, PDF_MAX_PAGE),
            )
    else:
        raise SurfaceNotFound
    if row is None:
        raise SurfaceNotFound
    return Surface(surface_type, surface_id, row["rev"])


# ── Reads ──

_LAYER = (
    "select id::text as id, rev, item_count, content_hash, purged_rev, purged_before_ms from public.ink_layers"
    " where user_id = %s and surface_type = %s and surface_id = %s"
)
_OUTDATED = (
    "select count(*)::int as n from public.ink_items where layer_id = %s and not deleted"
    " and content_rev is distinct from %s"
)


@dataclass
class LayerRead:
    layer_id: str | None
    rev: int
    items: list[dict[str, Any]]
    reset: bool
    hash: str | None
    item_count: int
    outdated: int


async def read_layer(
    conn: psycopg.AsyncConnection[Any], user_id: uuid.UUID, surface: Surface, since: int | None
) -> LayerRead:
    layer = await _one(conn, _LAYER, (user_id, surface.type, surface.id))
    if layer is None:
        return LayerRead(None, 0, [], since is not None and since > 0, None, 0, 0)
    reset = since is not None and (since < layer["purged_rev"] or since > layer["rev"])
    if since is None or reset:
        cur = await conn.execute(
            "select item from public.ink_items where layer_id = %s and not deleted order by item_id", (layer["id"],)
        )
    else:
        cur = await conn.execute(
            "select item from public.ink_items where layer_id = %s and seq > %s order by seq", (layer["id"], since)
        )
    items = [r["item"] for r in await cur.fetchall()]
    outdated = 0
    if surface.content_rev is not None and layer["item_count"]:
        row = await _one(conn, _OUTDATED, (layer["id"], surface.content_rev))
        outdated = int(row["n"]) if row else 0
    return LayerRead(
        layer["id"], int(layer["rev"]), items, reset, layer["content_hash"], int(layer["item_count"]), outdated
    )


# ── Writes ──

_ENSURE = """
insert into public.ink_layers (user_id, surface_type, surface_id) values (%s, %s, %s)
on conflict (user_id, surface_type, surface_id) do nothing
"""
_LOCK = _LAYER + " for update"

# One statement, LWW in SQL: the `where` is mergeInkItems' order with "stored row stays" on a full tie.
_UPSERT = """
insert into public.ink_items (user_id, layer_id, item_id, updated_at_ms, deleted, item, content_rev)
select %(uid)s, %(layer)s, x.item_id, x.updated_at_ms, x.deleted, x.item, %(content_rev)s
from jsonb_to_recordset(%(rows)s::jsonb) as x(item_id uuid, updated_at_ms bigint, deleted boolean, item jsonb)
-- At or below the purge horizon, only an item the table still holds can be merged: one that is gone may have
-- been deleted later by a tombstone that is now purged, and must not come back.
where x.updated_at_ms > %(horizon)s
   or exists (select 1 from public.ink_items i where i.layer_id = %(layer)s and i.item_id = x.item_id)
on conflict (layer_id, item_id) do update set
  updated_at_ms = excluded.updated_at_ms,
  deleted = excluded.deleted,
  item = excluded.item,
  content_rev = excluded.content_rev,
  seq = nextval('public.ink_items_seq'),
  written_at = now()
where excluded.updated_at_ms > ink_items.updated_at_ms
   or (excluded.updated_at_ms = ink_items.updated_at_ms and excluded.deleted and not ink_items.deleted)
returning item_id::text as id
"""

_STATS = """
update public.ink_layers l set
  rev = greatest(l.rev, s.max_seq),
  item_count = s.live,
  content_hash = s.hash
from (
  select coalesce(max(seq), 0) as max_seq,
         (count(*) filter (where not deleted))::int as live,
         encode(sha256(convert_to(coalesce(string_agg(
           item_id::text || ':' || updated_at_ms::text || ':' || case when deleted then '1' else '0' end,
           ',' order by item_id), ''), 'UTF8')), 'hex') as hash
  from public.ink_items where layer_id = %(layer)s
) s
where l.id = %(layer)s
returning l.rev, l.item_count, l.content_hash
"""


def layer_hash(items: list[dict[str, Any]]) -> str:
    """The `content_hash` of a replica, computed like _STATS (tests and tools; the web has the same in TS)."""
    import hashlib

    parts = sorted((str(i["id"]), f"{i['id']}:{int(i['updatedAt'])}:{'1' if i.get('deleted') else '0'}") for i in items)
    return hashlib.sha256(",".join(p for _, p in parts).encode()).hexdigest()


def compact_tombstone(item: dict[str, Any]) -> dict[str, Any]:
    """Tombstones keep their id, stamps and a valid minimal shape (one point), not the whole stroke: they are never
    rendered, and a 10k-point stroke should not weigh on the database for 90 days after it was erased."""
    if item.get("deleted") and isinstance(item.get("points"), list) and len(item["points"]) > 1:
        return {**item, "points": item["points"][:1]}
    return item


@dataclass
class ChangeResult:
    layer_id: str
    rev: int
    applied: list[str]
    stale: list[str]
    remote: list[dict[str, Any]]
    reset: bool
    hash: str | None
    item_count: int


async def apply_changes(
    conn: psycopg.AsyncConnection[Any],
    user_id: uuid.UUID,
    surface: Surface,
    base_rev: int,
    items: list[dict[str, Any]],
    *,
    echo: set[str] | frozenset[str] = frozenset(),
) -> ChangeResult:
    """Merge validated items into the caller's layer (privileged `conn`, one transaction). Raises TooManyItems
    (the caller's transaction then rolls back) when the layer would hold more than INK_MAX_ITEMS live items.

    `remote` holds what the client lacks: changes after `base_rev` from elsewhere, the stored version of every
    stale item, and the stored version of the `echo` ids (items this request wrote with a stamp the server
    changed, i.e. clamped). An item at or below the layer's purge horizon that the table no longer holds is stale
    and forces `reset` (a full list without it), so the device drops it as well."""
    await conn.execute(_ENSURE, (user_id, surface.type, surface.id))
    layer = await _one(conn, _LOCK, (user_id, surface.type, surface.id))
    assert layer is not None
    layer_id = layer["id"]
    applied: list[str] = []
    if items:
        rows = [
            {
                "item_id": i["id"],
                "updated_at_ms": int(i["updatedAt"]),
                "deleted": bool(i.get("deleted")),
                "item": compact_tombstone(i),
            }
            for i in items
        ]
        cur = await conn.execute(
            _UPSERT,
            {
                "uid": user_id,
                "layer": layer_id,
                "content_rev": surface.content_rev,
                "rows": json.dumps(rows),
                "horizon": int(layer["purged_before_ms"]),
            },
        )
        applied = [r["id"] for r in await cur.fetchall()]
    stats = await _one(conn, _STATS, {"layer": layer_id})
    assert stats is not None
    if stats["item_count"] > INK_MAX_ITEMS:
        raise TooManyItems(int(stats["item_count"]))
    done = set(applied)
    stale = [i["id"] for i in items if i["id"] not in done]
    reset = base_rev < layer["purged_rev"] or base_rev > layer["rev"]
    if any(i["updatedAt"] <= layer["purged_before_ms"] for i in items if i["id"] not in done):
        cur = await conn.execute(
            "select count(*)::int as n from public.ink_items where layer_id = %s and item_id = any(%s::uuid[])",
            (layer_id, stale),
        )
        row = await cur.fetchone()
        reset = reset or (row is not None and row["n"] < len(stale))
    if reset:
        cur = await conn.execute(
            "select item from public.ink_items where layer_id = %s and not deleted order by item_id", (layer_id,)
        )
    else:
        # Everything newer than the client's cursor except what this request just wrote (the client has it), plus
        # the stored version of every stale item, so the client converges on the server's copy.
        cur = await conn.execute(
            "select item from public.ink_items where layer_id = %s"
            " and ((seq > %s and not (item_id = any(%s::uuid[]))) or item_id = any(%s::uuid[])) order by seq",
            (layer_id, base_rev, applied, [*stale, *(i for i in applied if i in echo)]),
        )
    remote = [r["item"] for r in await cur.fetchall()]
    return ChangeResult(
        layer_id, int(stats["rev"]), applied, stale, remote, reset, stats["content_hash"], int(stats["item_count"])
    )


# ── Tombstone purge (daily) ──

_PURGE_BATCH = """
with dead as (
  delete from public.ink_items where ctid = any(array(
    select ctid from public.ink_items
    where deleted and written_at < now() - make_interval(days => %(days)s)
    limit %(limit)s
  ))
  returning layer_id, seq, updated_at_ms
)
select layer_id::text as layer_id, max(seq) as max_seq, max(updated_at_ms) as max_ms, count(*)::int as n
from dead group by layer_id
"""


async def purge_tombstones(
    conn: psycopg.AsyncConnection[Any], *, days: int = TOMBSTONE_HORIZON_DAYS, batch: int = 5000
) -> int:
    """Hard-delete tombstones written more than `days` ago (privileged `conn`). A sync cursor below a layer's
    `purged_rev` can no longer see those deletions, so GET/POST answer it with `reset`. Returns rows removed."""
    total = 0
    while True:
        cur = await conn.execute(_PURGE_BATCH, {"days": days, "limit": batch})
        groups = [dict(r) for r in await cur.fetchall()]
        if not groups:
            return total
        for g in groups:
            await conn.execute(
                "update public.ink_layers set purged_rev = greatest(purged_rev, %s),"
                " purged_before_ms = greatest(purged_before_ms, %s) where id = %s",
                (g["max_seq"], g["max_ms"], g["layer_id"]),
            )
            await conn.execute(_STATS, {"layer": g["layer_id"]})
            total += g["n"]
        if sum(g["n"] for g in groups) < batch:
            return total
