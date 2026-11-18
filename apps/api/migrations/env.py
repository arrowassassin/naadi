"""Alembic environment for Naadi.

- The URL comes from `naadi_api.settings` (DATABASE_URL), never from alembic.ini.
- Version table `naadi_alembic_version` (Aegra keeps its own `alembic_version` in the same database).
- Every online run holds the Naadi advisory lock (`naadi_api.migrate.advisory_lock`) for its whole duration.
- All pending migrations run in one transaction: a failure leaves the database at the previous revision.
- Offline (`--sql`) mode is refused: migrations inspect the live catalog (extension schema, storage columns).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, text
from sqlalchemy.engine import Engine

from naadi_api.migrate import DEFAULT_LOCK_WAIT_S, VERSION_TABLE, advisory_lock, make_engine
from naadi_api.settings import get_settings

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _engine() -> tuple[Engine, bool]:
    given = config.attributes.get("engine")
    if isinstance(given, Engine):
        return given, False
    url = get_settings().database_url
    if url is None:
        raise RuntimeError("DATABASE_URL is not set (see docs/SECRETS.md)")
    return make_engine(url.get_secret_value()), True


def _run(conn: Connection) -> None:
    # Bounded waits for table locks, so a long-running client query cannot wedge startup forever.
    conn.execute(text("set lock_timeout = '30s'"))
    conn.commit()
    with advisory_lock(conn, wait_s=float(config.attributes.get("lock_wait_s", DEFAULT_LOCK_WAIT_S))):
        context.configure(
            connection=conn,
            target_metadata=None,
            version_table=VERSION_TABLE,
            version_table_schema="public",
            transaction_per_migration=False,
        )
        with context.begin_transaction():
            context.run_migrations()
        conn.commit()


def run_migrations_online() -> None:
    engine, owned = _engine()
    try:
        with engine.connect() as conn:
            _run(conn)
    finally:
        if owned:
            engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline (--sql) mode is not supported for Naadi migrations; run against a database")
run_migrations_online()
