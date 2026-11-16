"""Run user-scoped SQL as the signed-in user, so Postgres RLS is the enforcement path.

On a pooled connection (autocommit) each call opens an explicit transaction and, inside it only:

    set local role authenticated;
    select set_config('request.jwt.claims', '{"sub": ..., "role": "authenticated", ...}', true);
    -- plus the legacy request.jwt.claim.sub / .role, which older auth.uid() definitions read

Both settings are transaction-local, so the connection goes back to the pool clean after commit or
rollback. `auth.uid()` (Supabase built-in) reads `sub` from these claims. The service role is never used
here; it is only for shared-content writes in later segments.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg.rows import dict_row, tuple_row

from .db import Database, DatabaseNotConfigured

STATEMENT_TIMEOUT_MS = 5000


def claims_json(user_id: uuid.UUID) -> str:
    # Minimal claims: what auth.uid()/auth.role() read. No email or other PII goes into the session.
    return json.dumps({"sub": str(user_id), "role": "authenticated", "aud": "authenticated"}, separators=(",", ":"))


@asynccontextmanager
async def user_tx(db: Database, user_id: uuid.UUID, *, deadline_s: float = 5.0) -> AsyncIterator[AsyncConnection]:
    """Yield a connection inside a transaction running as `authenticated` with `sub = user_id`."""
    if not db.configured:
        raise DatabaseNotConfigured("DATABASE_URL is not set")
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    async with db.pool.connection(timeout=deadline_s) as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]
        try:
            async with asyncio.timeout(deadline_s), conn.transaction():
                await conn.execute(f"set local statement_timeout = {STATEMENT_TIMEOUT_MS}")
                await conn.execute("set local role authenticated")
                # Both forms: hosted Supabase's auth.uid() reads `request.jwt.claims`; the bare
                # supabase/postgres image (CI) still has the legacy `request.jwt.claim.sub` definition.
                await conn.execute(
                    "select set_config('request.jwt.claims', %s, true), "
                    "set_config('request.jwt.claim.sub', %s, true), "
                    "set_config('request.jwt.claim.role', 'authenticated', true)",
                    (claims_json(user_id), str(user_id)),
                )
                yield conn
        finally:
            conn.row_factory = tuple_row
