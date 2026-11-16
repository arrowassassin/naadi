"""Shared fixtures for the search tests that need the real database and the real fastembed models."""

from __future__ import annotations

import pytest

from naadi_api.embeddings.engine import Engine, ModelsNotCached, spec_from_config
from tests.conftest import REPO_ROOT


@pytest.fixture(scope="module")
def url() -> str:
    from tests.db.rls import database_url, ensure_migrated, require_local

    u = database_url()
    require_local(u)
    ensure_migrated(u)
    return u


@pytest.fixture(scope="module")
def engine() -> Engine:
    eng = Engine(spec_from_config(REPO_ROOT / "config"))
    try:
        eng.warm()
    except ModelsNotCached as exc:  # a missing cache is a setup error, not a skip: the suite must prove retrieval
        pytest.fail(str(exc))
    return eng
