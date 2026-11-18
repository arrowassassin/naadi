"""Apply Naadi's Alembic migrations (`python -m naadi_api.migrate`).

The Space container runs this before uvicorn starts. Every Alembic run (this module, the `alembic` CLI,
tests) goes through `migrations/env.py`, which holds a session-level Postgres advisory lock for the whole
run, so two containers or workers starting at once never migrate concurrently: the second one waits, then
finds the database already at head. Failure exits non-zero with one JSON log line naming the error class
and a scrubbed message (never the URL or password), which stops the container before the app serves.

Naadi's version table is `naadi_alembic_version`; Aegra (segment 04) keeps its own `alembic_version`.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.pool import NullPool

from .logs import configure_logging, get_logger
from .scrub import scrub
from .settings import APP_DIR, Settings, get_settings

VERSION_TABLE = "naadi_alembic_version"
# pg_advisory_lock key: the first 8 bytes of sha256("naadi.migrations") as a signed bigint. Fixed forever,
# so every Naadi process (old and new deploys) contends on the same lock.
ADVISORY_LOCK_KEY = -2_938_390_036_235_118_414
DEFAULT_LOCK_WAIT_S = 300.0
MIGRATIONS_DIR = APP_DIR / "migrations"

log = get_logger("naadi.migrate")


class MigrationLockTimeout(RuntimeError):
    pass


def sqlalchemy_url(database_url: str) -> str:
    """`postgresql://…` (Supabase connection string) → the psycopg 3 SQLAlchemy dialect."""
    url = make_url(database_url)
    if url.get_backend_name() not in ("postgresql", "postgres"):
        raise ValueError("DATABASE_URL must be a postgresql:// URL")
    return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def make_engine(database_url: str) -> Engine:
    # NullPool: one short-lived connection per run. Works through the Supabase session pooler (port 5432),
    # which keeps the session (and therefore the advisory lock) for the life of the connection.
    return create_engine(
        sqlalchemy_url(database_url),
        poolclass=NullPool,
        connect_args={"connect_timeout": 10, "application_name": "naadi-migrate"},
    )


def alembic_config(*, configure_logger: bool = True) -> Config:
    cfg = Config(str(MIGRATIONS_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.attributes["configure_logger"] = configure_logger
    return cfg


@contextmanager
def advisory_lock(conn: Connection, *, wait_s: float = DEFAULT_LOCK_WAIT_S, poll_s: float = 1.0) -> Iterator[None]:
    """Hold the Naadi migration lock on `conn` (session level, so it survives the migration's COMMIT).

    Polls `pg_try_advisory_lock` instead of blocking so a stuck holder produces a clear timeout rather than
    a container that hangs forever.
    """
    deadline = time.monotonic() + wait_s
    waited = False
    while True:
        got = conn.execute(text("select pg_try_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar_one()
        conn.commit()
        if got:
            break
        if not waited:
            log.info("migrate.waiting_for_lock")
            waited = True
        if time.monotonic() >= deadline:
            raise MigrationLockTimeout(f"another process held the migration lock for more than {wait_s:.0f}s")
        time.sleep(poll_s)
    try:
        yield
    finally:
        try:
            conn.rollback()
            conn.execute(text("select pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - the lock dies with the session anyway
            log.warning("migrate.unlock_failed", error=type(exc).__name__)


def current_revision(conn: Connection) -> str | None:
    ctx = MigrationContext.configure(conn, opts={"version_table": VERSION_TABLE})
    rev = ctx.get_current_revision()
    conn.rollback()
    return rev


def head_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:
        raise RuntimeError("no migrations found")
    return head


def upgrade(
    database_url: str, target: str = "head", *, lock_wait_s: float = DEFAULT_LOCK_WAIT_S
) -> tuple[str | None, str | None]:
    """Upgrade to `target`; returns (revision before, revision after)."""
    engine = make_engine(database_url)
    try:
        with engine.connect() as conn:
            before = current_revision(conn)
        cfg = alembic_config(configure_logger=False)
        cfg.attributes["engine"] = engine
        cfg.attributes["lock_wait_s"] = lock_wait_s
        command.upgrade(cfg, target)
        with engine.connect() as conn:
            after = current_revision(conn)
        return before, after
    finally:
        engine.dispose()


def downgrade(database_url: str, target: str, *, lock_wait_s: float = DEFAULT_LOCK_WAIT_S) -> None:
    engine = make_engine(database_url)
    try:
        cfg = alembic_config(configure_logger=False)
        cfg.attributes["engine"] = engine
        cfg.attributes["lock_wait_s"] = lock_wait_s
        command.downgrade(cfg, target)
    finally:
        engine.dispose()


def run(settings: Settings, *, target: str = "head", if_configured: bool = False) -> int:
    if settings.database_url is None:
        if if_configured:
            log.info("migrate.skipped", reason="DATABASE_URL is not set")
            return 0
        log.error("migrate.failed", error="DatabaseNotConfigured", detail="DATABASE_URL is not set")
        return 2
    started = time.monotonic()
    try:
        before, after = upgrade(settings.database_url.get_secret_value(), target)
    except Exception as exc:  # noqa: BLE001 - any failure must stop startup with one clear line
        log.error(
            "migrate.failed",
            error=type(exc).__name__,
            detail=scrub(str(exc), settings.secret_values(), max_len=400),
            hint="the app was not started; fix the migration or the database and redeploy",
        )
        return 1
    log.info(
        "migrate.ok",
        revision_before=before,
        revision_after=after,
        changed=before != after,
        seconds=round(time.monotonic() - started, 2),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m naadi_api.migrate", description=__doc__.splitlines()[0])
    parser.add_argument("--target", default="head", help="revision to upgrade to (default: head)")
    parser.add_argument(
        "--if-configured",
        action="store_true",
        help="exit 0 when DATABASE_URL is unset (local e2e without a database); the Space never passes it",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    return run(settings, target=args.target, if_configured=args.if_configured)


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "ADVISORY_LOCK_KEY",
    "DEFAULT_LOCK_WAIT_S",
    "MIGRATIONS_DIR",
    "VERSION_TABLE",
    "MigrationLockTimeout",
    "advisory_lock",
    "alembic_config",
    "current_revision",
    "downgrade",
    "head_revision",
    "make_engine",
    "run",
    "sqlalchemy_url",
    "upgrade",
]
