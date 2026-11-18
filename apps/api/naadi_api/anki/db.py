"""Database access of the Anki import and export: the pool's privileged role, always filtered by the caller's id.

Imported cards are written here and not as the user because RLS lets a student write `origin 'user'` cards only.
Every statement below carries `owner_user_id = %(uid)s`, so the privileged role can never touch another student's
rows. Pictures are private `media_assets` rows (`owner_user_id` set) that RLS shows to their owner alone.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row, tuple_row

from ..db import Database, DatabaseNotConfigured


class AnkiError(Exception):
    """An error with a stable code the client words from `ui_copy` `anki.error.<code>`."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


@asynccontextmanager
async def service_tx(db: Database, *, timeout_ms: int = 30_000) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    if not db.configured:
        raise DatabaseNotConfigured("DATABASE_URL is not set")
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    async with db.pool.connection(timeout=5.0) as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]
        try:
            async with conn.transaction():
                await conn.execute(f"set local statement_timeout = {int(timeout_ms)}")
                yield conn
        finally:
            conn.row_factory = tuple_row


MAX_CARDS_PER_USER = 100_000


def derived_guid(card_id: uuid.UUID) -> str:
    """The note GUID an export gives a card that has no `anki_guid` (its own typed cards)."""
    return "n" + hashlib.sha256(str(card_id).encode()).hexdigest()[:20]


async def existing_guids(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID, guids: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(guids), 5000):
        cur = await conn.execute(
            "select anki_guid from public.flashcards where owner_user_id = %s and anki_guid = any(%s)",
            (uid, guids[i : i + 5000]),
        )
        found.update(r["anki_guid"] for r in await cur.fetchall())
    # A package exported from this library names the student's own cards by an id-derived GUID: importing it back
    # must find them (it would otherwise copy every typed card).
    cur = await conn.execute("select id from public.flashcards where owner_user_id = %s and anki_guid is null", (uid,))
    wanted = set(guids)
    found.update(g for g in (derived_guid(r["id"]) for r in await cur.fetchall()) if g in wanted)
    return found


async def card_total(conn: psycopg.AsyncConnection[Any], uid: uuid.UUID) -> int:
    cur = await conn.execute("select count(*)::int as n from public.flashcards where owner_user_id = %s", (uid,))
    row = await cur.fetchone()
    return int(row["n"]) if row else 0
