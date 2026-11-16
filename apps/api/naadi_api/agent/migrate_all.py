"""Container startup migration: Aegra's schema first, then Naadi's (`python -m naadi_api.agent.migrate_all`).

Order matters: Naadi's agent-platform migration enables row-level security on Aegra's tables, so they must
exist first. Steps, each idempotent:

1. Aegra's Alembic migrations (its own `alembic_version` table), under Aegra's advisory lock;
2. LangGraph's checkpointer and store tables (`AsyncPostgresSaver.setup()` / `AsyncPostgresStore.setup()`),
   which Aegra would otherwise create lazily at first boot;
3. Naadi's Alembic migrations (`naadi_alembic_version`, naadi_api.migrate);
4. `public.naadi_secure_aegra_tables()` (defined by migration 0005): RLS on, client privileges off, for each
   Aegra / LangGraph table, including any created after Naadi's head was already applied;
5. a tripwire: every table in `public` must have RLS enabled. A new Aegra / LangGraph release that adds a
   table fails startup here (one clear log line) until a Naadi migration covers it.

Failure exits non-zero with one JSON log line (scrubbed), and the container stops before serving.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from .. import migrate
from ..logs import configure_logging, get_logger
from ..scrub import scrub
from ..settings import APP_DIR, Settings, get_settings

log = get_logger("naadi.migrate_all")

AEGRA_TABLES = (
    "alembic_version",
    "assistant",
    "assistant_versions",
    "thread",
    "runs",
    "crons",
    "thread_ttl",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
    "store",
    "store_migrations",
)


def _aegra_env(settings: Settings) -> None:
    assert settings.database_url is not None
    os.environ.setdefault("AEGRA_CONFIG", str(APP_DIR / "aegra.json"))
    os.environ["DATABASE_URL"] = settings.database_url.get_secret_value()


def aegra_upgrade() -> None:
    from aegra_api.core.migrations import run_migrations

    run_migrations()


async def langgraph_setup(database_url: str) -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres.aio import AsyncPostgresStore
    from psycopg.rows import dict_row

    async with await psycopg.AsyncConnection.connect(
        database_url, autocommit=True, prepare_threshold=None, row_factory=dict_row
    ) as conn:
        await AsyncPostgresSaver(conn).setup()
        await AsyncPostgresStore(conn).setup()


def secure_aegra_tables(database_url: str) -> int:
    """Run 0005's `naadi_secure_aegra_tables()` again: Aegra may have created tables after Naadi's head."""
    with psycopg.connect(database_url, connect_timeout=10, autocommit=True) as conn:
        row = conn.execute("select public.naadi_secure_aegra_tables()").fetchone()
    return int(row[0]) if row else 0


def tables_without_rls(database_url: str) -> list[str]:
    with psycopg.connect(database_url, connect_timeout=10) as conn:
        rows = conn.execute(
            "select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace"
            " where n.nspname = 'public' and c.relkind in ('r', 'p') and not c.relrowsecurity order by 1"
        ).fetchall()
    return [r[0] for r in rows]


# Session advisory lock held around the whole startup migration (Aegra + LangGraph + Naadi + securing), so two
# containers or processes starting at once never run LangGraph's CREATE ... IF NOT EXISTS concurrently.
# Distinct from naadi_api.migrate.ADVISORY_LOCK_KEY, which Naadi's own Alembic run takes inside this one.
STARTUP_LOCK_KEY = 4_611_686_018_427_387_913
LOCK_WAIT_S = 600.0


@contextmanager
def startup_lock(database_url: str) -> Iterator[None]:
    conn = psycopg.connect(database_url, connect_timeout=10, autocommit=True, application_name="naadi-migrate-all")
    try:
        deadline = time.monotonic() + LOCK_WAIT_S
        while not conn.execute("select pg_try_advisory_lock(%s)", (STARTUP_LOCK_KEY,)).fetchone()[0]:  # type: ignore[index]
            if time.monotonic() > deadline:
                raise TimeoutError(f"another process held the startup migration lock for more than {LOCK_WAIT_S:.0f}s")
            time.sleep(1.0)
        yield
    finally:
        conn.close()  # releases the session lock


def run(settings: Settings) -> int:
    if settings.database_url is None:
        log.error("migrate_all.failed", error="DatabaseNotConfigured", detail="DATABASE_URL is not set")
        return 2
    url = settings.database_url.get_secret_value()
    started = time.monotonic()
    step = "aegra"
    try:
        lock = startup_lock(url)
        lock.__enter__()
    except Exception as exc:  # noqa: BLE001 - one clear line, then stop
        log.error(
            "migrate_all.failed",
            step="lock",
            error=type(exc).__name__,
            detail=scrub(str(exc), settings.secret_values()),
        )
        return 1
    try:
        _aegra_env(settings)
        aegra_upgrade()
        # Aegra's alembic env.py applies its own logging fileConfig in the main thread: restore JSON lines.
        configure_logging(settings.log_level)
        step = "langgraph"
        asyncio.run(langgraph_setup(url))
        step = "naadi"
        before, after = migrate.upgrade(url)
        step = "secure_aegra_tables"
        secured = secure_aegra_tables(url)
        step = "rls_tripwire"
        missing = tables_without_rls(url)
        if missing:
            log.error(
                "migrate_all.failed",
                error="TablesWithoutRLS",
                detail=", ".join(missing)[:400],
                hint="add a Naadi migration enabling RLS on these tables (closed to anon/authenticated)",
            )
            return 1
    except Exception as exc:  # noqa: BLE001 - any failure must stop startup with one clear line
        log.error(
            "migrate_all.failed",
            step=step,
            error=type(exc).__name__,
            detail=scrub(str(exc), settings.secret_values(), max_len=400),
            hint="the app was not started; fix the migration or the database and redeploy",
        )
        return 1
    finally:
        lock.__exit__(None, None, None)
    log.info(
        "migrate_all.ok",
        naadi_revision_before=before,
        naadi_revision_after=after,
        aegra_tables_secured=secured,
        seconds=round(time.monotonic() - started, 2),
    )
    return 0


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    return run(settings)


if __name__ == "__main__":
    sys.exit(main())
