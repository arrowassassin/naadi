"""Last writer wins per row, on the device's `client_updated_at_ms` (segment 18).

The rule (golden vectors in packages/shared/fixtures/lww_vectors.json, read by the web client's tests too):

- the incoming stamp is clamped to `server_now + FUTURE_SKEW_MS` (a device clock far ahead cannot win forever);
- a write or a delete applies only when its stamp is strictly newer than both the stored row's stamp and the
  row's tombstone (a stored stamp of null is older than any stamp); a tie keeps what the server has;
- the result names the stamp the server holds afterwards (`effective_ms`) and whether the row is deleted.

`apply_lww` is that rule as ONE guarded statement (`insert … on conflict … do update … where stored < excluded`, or
`update … where stored < incoming` for a row that must already exist), so two devices racing on one row cannot
both win: Postgres re-checks the guard on the row it locks. Deletes write a tombstone with `write_tombstone`
(greatest stamp kept). Table and column names are identifiers of the caller's own constants, never user input.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

FUTURE_SKEW_MS = 300_000
STAMP_COLUMN = "client_updated_at_ms"
# Max/min a stamp may be (ms since the epoch, bigint-safe and before year 2286); the request models check it too.
MAX_STAMP_MS = 9_999_999_999_999

Entity = Literal["note", "highlight", "notebook", "page", "plan_item", "user_pathway_task", "cost", "reading_position"]
NOTE_ENTITIES: tuple[Entity, ...] = ("note", "highlight", "page")

Row = dict[str, Any]


def server_now_ms() -> int:
    return time.time_ns() // 1_000_000


def clamp(incoming_ms: int, server_now_ms: int) -> int:
    return min(incoming_ms, server_now_ms + FUTURE_SKEW_MS)


@dataclass(frozen=True, slots=True)
class Decision:
    applied: bool
    effective_ms: int = 0
    deleted: bool = False


def decide(
    stored_ms: int | None, tombstone_ms: int | None, incoming_ms: int, server_now_ms: int, is_delete: bool
) -> Decision:
    """Pure LWW: does the incoming write (or delete) win against the stored row and its tombstone?"""
    incoming = clamp(incoming_ms, server_now_ms)
    held = [v for v in (stored_ms, tombstone_ms) if v is not None]
    floor = max(held) if held else None
    if floor is None or incoming > floor:
        return Decision(applied=True, effective_ms=incoming, deleted=is_delete)
    deleted = tombstone_ms is not None and (stored_ms is None or tombstone_ms > stored_ms)
    return Decision(applied=False, effective_ms=floor, deleted=deleted)


def stamp_of(client_ms: int | None, now_ms: int) -> int:
    """The stamp a write carries: the device's (clamped), or the server's clock for a client that sends none."""
    return now_ms if client_ms is None else clamp(client_ms, now_ms)


def decide_write(
    client_ms: int | None, stored_ms: int | None, tombstone_ms: int | None, server_now_ms: int, is_delete: bool
) -> Decision:
    """`decide` for an endpoint: a write without a device stamp (an online client of before segment 18) always
    applies, as it did then, stamped with the server clock but never below what the row already holds, so later
    device stamps still compare against it."""
    if client_ms is not None:
        return decide(stored_ms, tombstone_ms, client_ms, server_now_ms, is_delete)
    held = [v for v in (stored_ms, tombstone_ms) if v is not None]
    stamp = max(server_now_ms, max(held) + 1) if held else server_now_ms
    return Decision(applied=True, effective_ms=stamp, deleted=is_delete)


# ── Tombstones ──


async def find_tombstone(
    conn: psycopg.AsyncConnection[Any], user_id: uuid.UUID, entities: Sequence[Entity], key: str
) -> tuple[str, int] | None:
    """(entity, stamp) of the newest tombstone of `key` among `entities`, if any."""
    cur = await conn.execute(
        "select entity, client_updated_at_ms from public.sync_tombstones where user_id = %s and entity = any(%s)"
        " and key = %s order by client_updated_at_ms desc limit 1",
        (user_id, list(entities), key),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    if isinstance(row, Mapping):
        return str(row["entity"]), int(row["client_updated_at_ms"])
    return str(row[0]), int(row[1])


async def tombstone_ms(
    conn: psycopg.AsyncConnection[Any], user_id: uuid.UUID, entities: Sequence[Entity], key: str
) -> int | None:
    found = await find_tombstone(conn, user_id, entities, key)
    return found[1] if found is not None else None


async def write_tombstone(
    conn: psycopg.AsyncConnection[Any], user_id: uuid.UUID, entity: Entity, keys: Sequence[str], stamp_ms: int
) -> None:
    """Record deletes (the greatest stamp per key stays; a later re-delete refreshes the 90-day clock)."""
    if not keys:
        return
    await conn.execute(
        "insert into public.sync_tombstones (user_id, entity, key, client_updated_at_ms)"
        " select %s, %s, k, %s from unnest(%s::text[]) as k"
        " on conflict (user_id, entity, key) do update set"
        "   client_updated_at_ms = greatest(sync_tombstones.client_updated_at_ms, excluded.client_updated_at_ms),"
        "   created_at = now()",
        (user_id, entity, stamp_ms, list(keys)),
    )


async def clear_tombstone(
    conn: psycopg.AsyncConnection[Any], user_id: uuid.UUID, entities: Sequence[Entity], key: str
) -> None:
    """A row re-created with a newer stamp than its delete: the tombstone no longer decides anything."""
    await conn.execute(
        "delete from public.sync_tombstones where user_id = %s and entity = any(%s) and key = %s",
        (user_id, list(entities), key),
    )


# ── The guarded write ──


@dataclass(frozen=True, slots=True)
class LwwResult:
    applied: bool
    row: Row | None = None  # the stored row after the statement (the winner), None when deleted / missing
    # The row's tombstone stamp, when it has one. `applied=False` with `row=None`: the row is deleted (or missing).
    tombstone_ms: int | None = None


def _as_param(v: Any) -> Any:
    return Jsonb(v) if isinstance(v, (dict, list)) else v


async def apply_lww(
    conn: psycopg.AsyncConnection[Any],
    *,
    table: str,
    key: Mapping[str, Any],
    values: Mapping[str, Any],
    client_ms: int | None,
    user_id: uuid.UUID,
    entities: Sequence[Entity],
    tombstone_key: str,
    returning: Sequence[str],
    insert: bool = True,
    now_ms: int | None = None,
) -> LwwResult:
    """Write `values` on the row `key` of `public.<table>` if the incoming stamp is newer than the stored one.

    `insert=True`: one `insert … on conflict (key) do update … where stored < excluded` (creates the row when
    missing). `insert=False`: one `update … where key and stored < incoming` (a missing row is not created).
    A tombstone at least as new as the stamp wins without touching the table (`tombstone_ms` set, `row` None).
    `client_ms=None` (a client without stamps) always applies, stamped `greatest(now, stored + 1)` (see
    `decide_write`). `key` must include `user_id`: every statement filters on the caller."""
    assert "user_id" in key, "apply_lww filters on the caller"
    now = server_now_ms() if now_ms is None else now_ms
    stamp = stamp_of(client_ms, now)
    tomb = await tombstone_ms(conn, user_id, entities, tombstone_key)
    if client_ms is not None and tomb is not None and stamp <= tomb:
        return LwwResult(applied=False, row=None, tombstone_ms=tomb)
    if client_ms is None and tomb is not None:
        stamp = max(stamp, tomb + 1)
    t = sql.Identifier("public", table)
    col = sql.Identifier(STAMP_COLUMN)
    ret = sql.SQL(", ").join(sql.Identifier(c) for c in returning)
    key_cols = list(key)
    val_cols = [c for c in values if c not in key and c != STAMP_COLUMN]
    key_where = sql.SQL(" and ").join(sql.SQL("{} = {}").format(sql.Identifier(c), sql.Placeholder()) for c in key_cols)
    key_params = [_as_param(key[c]) for c in key_cols]
    if insert:
        cols = [*key_cols, *val_cols, STAMP_COLUMN]
        params: list[Any] = [*key_params, *(_as_param(values[c]) for c in val_cols), stamp]
        sets: list[sql.Composable] = [
            sql.SQL("{} = excluded.{}").format(sql.Identifier(c), sql.Identifier(c)) for c in val_cols
        ]
        if client_ms is None:
            sets.append(sql.SQL("{c} = greatest(excluded.{c}, coalesce({t}.{c} + 1, excluded.{c}))").format(c=col, t=t))
            guard: sql.Composable = sql.SQL("")
        else:
            sets.append(sql.SQL("{c} = excluded.{c}").format(c=col))
            guard = sql.SQL(" where {t}.{c} is null or {t}.{c} < excluded.{c}").format(t=t, c=col)
        query = sql.SQL(
            "insert into {t} ({cols}) values ({ph}) on conflict ({keys}) do update set {sets}{guard} returning {ret}"
        ).format(
            t=t,
            cols=sql.SQL(", ").join(sql.Identifier(c) for c in cols),
            ph=sql.SQL(", ").join(sql.Placeholder() * len(cols)),
            keys=sql.SQL(", ").join(sql.Identifier(c) for c in key_cols),
            sets=sql.SQL(", ").join(sets),
            guard=guard,
            ret=ret,
        )
    else:
        sets = [sql.SQL("{} = {}").format(sql.Identifier(c), sql.Placeholder()) for c in val_cols]
        params = [_as_param(values[c]) for c in val_cols]
        if client_ms is None:
            sets.append(sql.SQL("{c} = greatest({p}, coalesce({c} + 1, {p}))").format(c=col, p=sql.Placeholder()))
            params += [stamp, stamp, *key_params]
            guard = sql.SQL("")  # a client without stamps always applies
        else:
            sets.append(sql.SQL("{c} = {p}").format(c=col, p=sql.Placeholder()))
            params += [stamp, *key_params, stamp]
            guard = sql.SQL(" and ({c} is null or {c} < {p})").format(c=col, p=sql.Placeholder())
        query = sql.SQL("update {t} set {sets} where {where}{guard} returning {ret}").format(
            t=t, sets=sql.SQL(", ").join(sets), where=key_where, guard=guard, ret=ret
        )
    cur = await conn.execute(query, params)
    written = await cur.fetchone()
    if written is not None:
        if tomb is not None:
            await clear_tombstone(conn, user_id, entities, tombstone_key)
        return LwwResult(applied=True, row=_row(written, returning))
    select = sql.SQL("select {ret} from {t} where {where}").format(ret=ret, t=t, where=key_where)
    cur = await conn.execute(select, key_params)
    stored = await cur.fetchone()
    return LwwResult(applied=False, row=_row(stored, returning) if stored is not None else None, tombstone_ms=tomb)


def _row(raw: Any, cols: Sequence[str]) -> Row:
    if isinstance(raw, Mapping):
        return dict(raw)
    return dict(zip(cols, raw, strict=True))


__all__ = [
    "FUTURE_SKEW_MS",
    "MAX_STAMP_MS",
    "NOTE_ENTITIES",
    "STAMP_COLUMN",
    "Decision",
    "Entity",
    "LwwResult",
    "apply_lww",
    "clamp",
    "clear_tombstone",
    "decide",
    "decide_write",
    "find_tombstone",
    "server_now_ms",
    "stamp_of",
    "tombstone_ms",
    "write_tombstone",
]
