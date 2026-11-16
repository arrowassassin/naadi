"""Async Postgres pool (psycopg 3). One pool per process, opened in the app lifespan.

The pool opens without waiting, so the API starts (and reports `database: down`) even when Postgres is
unreachable; psycopg_pool keeps reconnecting in the background.
"""

from __future__ import annotations

import asyncio

from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr


class DatabaseNotConfigured(RuntimeError):
    pass


class Database:
    def __init__(self, url: SecretStr | None, *, max_size: int = 3) -> None:
        self._url = url
        self._max_size = max_size
        self.pool: AsyncConnectionPool | None = None
        self._open_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return self._url is not None

    def conninfo(self) -> str:
        """The connection string, for a dedicated (non-pooled) connection such as a LISTEN session."""
        if self._url is None:
            raise DatabaseNotConfigured("DATABASE_URL is not set")
        return self._url.get_secret_value()

    @property
    def opened(self) -> bool:
        return self.pool is not None and not self.pool.closed

    async def open(self) -> None:
        if self._url is None or self.opened:
            return
        self.pool = AsyncConnectionPool(
            conninfo=self._url.get_secret_value(),
            min_size=1,
            max_size=self._max_size,
            open=False,
            name="naadi",
            timeout=5.0,
            max_idle=300.0,
            reconnect_timeout=60.0,
            kwargs={"autocommit": True, "connect_timeout": 5, "application_name": "naadi-api"},
        )
        await self.pool.open(wait=False)

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close(timeout=5.0)
            self.pool = None

    async def ping(self, deadline_s: float = 4.0) -> None:
        """`select 1` on a pooled connection; raises on failure or timeout."""
        if self._url is None:
            raise DatabaseNotConfigured("DATABASE_URL is not set")
        if self.pool is None:
            # Lazy open: a host process (e.g. an outer ASGI server) may not run this app's lifespan.
            async with self._open_lock:
                await self.open()
        assert self.pool is not None
        # pool.connection() raises PoolTimeout ("couldn't get a connection after N sec") on its own deadline;
        # the statement itself is bounded by an asyncio timeout.
        async with self.pool.connection(timeout=deadline_s) as conn:
            async with asyncio.timeout(deadline_s):
                cur = await conn.execute("select 1")
                row = await cur.fetchone()
        if row is None or row[0] != 1:
            raise RuntimeError("select 1 returned an unexpected result")
