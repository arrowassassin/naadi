"""Fixtures for the `db` tests (local container via tests/db/stack.sh, or the CI service container).

Every test module here sets `pytestmark = DB_MARKS` (`integration` + `db`), so the CI unit job
(`-m "not integration"`) never needs a database and the `db` job (`-m db`) runs all of them.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest

from .fixtures import Shared, seed_shared
from .rls import create_auth_user, database_url, ensure_migrated


@pytest.fixture(scope="session")
def db_url() -> str:
    return database_url()


@pytest.fixture(scope="session")
def migrated_url(db_url: str) -> str:
    ensure_migrated(db_url)
    return db_url


@pytest.fixture
def pg(migrated_url: str) -> Iterator[psycopg.Connection]:
    """A privileged (`postgres`) connection whose whole test runs in one transaction that is rolled back."""
    with (
        psycopg.connect(migrated_url, connect_timeout=10, application_name="naadi-tests") as conn,
        conn.transaction(force_rollback=True),
    ):
        yield conn


@pytest.fixture
def user_a(pg: psycopg.Connection) -> uuid.UUID:
    return create_auth_user(pg)


@pytest.fixture
def user_b(pg: psycopg.Connection) -> uuid.UUID:
    return create_auth_user(pg)


@pytest.fixture
def shared(pg: psycopg.Connection) -> Shared:
    return seed_shared(pg)
