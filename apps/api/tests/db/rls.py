"""Helpers for database tests that act as Supabase users (contract S02 → S03, ledger 20261116T074406).

How a test acts as a user: inside a transaction (or savepoint), `set local role authenticated` and set the
JWT claims that `auth.uid()` reads. Supabase's `auth.uid()` reads `request.jwt.claims ->> 'sub'`; older auth
schemas (the bare Postgres image) read `request.jwt.claim.sub`, so both are set. Everything is
transaction-local, and the helpers always roll back, so a test leaves no rows and no role behind.

    with pg.transaction(force_rollback=True):
        a = create_auth_user(pg)
        with as_user(pg, a):
            pg.execute("select * from profiles")          # RLS applies as user a
        with as_anon(pg):
            ...

Destructive operations (downgrades) are refused unless DATABASE_URL points at a local container.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from urllib.parse import urlparse

import psycopg
import pytest

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "postgres"})
DB_MARKS = [pytest.mark.integration, pytest.mark.db]


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        pytest.fail("missing required environment variable DATABASE_URL (apps/api/tests/db/stack.sh up)")
    return url


def is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in LOCAL_HOSTS


def require_local(url: str) -> None:
    if not is_local(url):
        pytest.fail("refusing a destructive database test: DATABASE_URL is not a local test container")


def ensure_migrated(url: str) -> str:
    """Local container: `alembic upgrade head` (advisory-locked, idempotent). Any other database is only
    migrated by the Space at startup, so there this asserts it is already at head. Returns the head."""
    from naadi_api import migrate

    head = migrate.head_revision()
    if is_local(url):
        _, after = migrate.upgrade(url)
        assert after == head
        return head
    engine = migrate.make_engine(url)
    try:
        with engine.connect() as conn:
            current = migrate.current_revision(conn)
    finally:
        engine.dispose()
    if current != head:
        pytest.fail(f"database is at revision {current!r}, expected head {head!r} (migrated by the Space at startup)")
    return head


def claims(user_id: uuid.UUID, role: str = "authenticated", **extra: object) -> str:
    return json.dumps({"sub": str(user_id), "role": role, "aud": "authenticated", **extra})


def _user_settings_sql() -> str:
    return (
        "select set_config('request.jwt.claims', %(claims)s, true),"
        " set_config('request.jwt.claim.sub', %(sub)s, true),"
        " set_config('request.jwt.claim.role', %(role)s, true)"
    )


def create_auth_user(conn: psycopg.Connection, *, email: str | None = None) -> uuid.UUID:
    """Insert a minimal `auth.users` row (as the connection's role, normally `postgres`)."""
    uid = uuid.uuid4()
    conn.execute(
        "insert into auth.users (id, email, aud, role) values (%s, %s, 'authenticated', 'authenticated')",
        (uid, email or f"{uid.hex[:12]}@test.invalid"),
    )
    return uid


def delete_auth_user(conn: psycopg.Connection, user_id: uuid.UUID) -> None:
    conn.execute("delete from auth.users where id = %s", (user_id,))


@contextmanager
def as_user(conn: psycopg.Connection, user_id: uuid.UUID) -> Iterator[psycopg.Connection]:
    """Act as `user_id` (role `authenticated`) inside a savepoint that is always rolled back."""
    with conn.transaction(force_rollback=True):
        conn.execute("set local role authenticated")
        conn.execute(_user_settings_sql(), {"claims": claims(user_id), "sub": str(user_id), "role": "authenticated"})
        yield conn


@contextmanager
def as_anon(conn: psycopg.Connection) -> Iterator[psycopg.Connection]:
    """Act as an unauthenticated client (role `anon`, no `sub`) inside a rolled-back savepoint."""
    with conn.transaction(force_rollback=True):
        conn.execute("set local role anon")
        conn.execute(
            "select set_config('request.jwt.claims', %s, true), set_config('request.jwt.claim.sub', '', true)",
            (json.dumps({"role": "anon"}),),
        )
        yield conn


@contextmanager
def as_service_role(conn: psycopg.Connection) -> Iterator[psycopg.Connection]:
    with conn.transaction(force_rollback=True):
        conn.execute("set local role service_role")
        yield conn


async def create_auth_user_async(conn: psycopg.AsyncConnection, *, email: str | None = None) -> uuid.UUID:
    uid = uuid.uuid4()
    await conn.execute(
        "insert into auth.users (id, email, aud, role) values (%s, %s, 'authenticated', 'authenticated')",
        (uid, email or f"{uid.hex[:12]}@test.invalid"),
    )
    return uid


@asynccontextmanager
async def as_user_async(conn: psycopg.AsyncConnection, user_id: uuid.UUID) -> AsyncIterator[psycopg.AsyncConnection]:
    async with conn.transaction(force_rollback=True):
        await conn.execute("set local role authenticated")
        await conn.execute(
            _user_settings_sql(), {"claims": claims(user_id), "sub": str(user_id), "role": "authenticated"}
        )
        yield conn


@asynccontextmanager
async def as_anon_async(conn: psycopg.AsyncConnection) -> AsyncIterator[psycopg.AsyncConnection]:
    async with conn.transaction(force_rollback=True):
        await conn.execute("set local role anon")
        await conn.execute(
            "select set_config('request.jwt.claims', %s, true), set_config('request.jwt.claim.sub', '', true)",
            (json.dumps({"role": "anon"}),),
        )
        yield conn
