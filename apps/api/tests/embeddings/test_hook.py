"""A successful seed import reschedules the search index; a dry run or a failure does not."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from naadi_api.db import Database
from naadi_api.seed import jobs
from tests.conftest import make_settings

pytestmark = [pytest.mark.integration, pytest.mark.db]


async def run_job(
    monkeypatch: pytest.MonkeyPatch, url: str, *, status: str, dry_run: bool, only: list[Any]
) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(jobs, "_execute", lambda settings, req: (status, {}, None if status == "succeeded" else "boom"))
    monkeypatch.setattr(jobs, "schedule_reindex", lambda app, *a, **k: calls.append((app, a, k)))
    db = Database(SecretStr(url), max_size=2)
    await db.open()
    try:
        app = SimpleNamespace(state=SimpleNamespace(settings=make_settings(), db=db, app_config=None))
        await jobs._run(app, uuid.uuid4(), jobs.SeedRequest(dry_run=dry_run, only=only))
    finally:
        await db.close()
    return calls


async def test_a_real_successful_seed_reschedules_the_index(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    assert len(await run_job(monkeypatch, url, status="succeeded", dry_run=False, only=["chapters", "questions"])) == 1


async def test_dry_runs_failures_and_unrelated_groups_do_not(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    assert await run_job(monkeypatch, url, status="succeeded", dry_run=True, only=["chapters"]) == []
    assert await run_job(monkeypatch, url, status="failed", dry_run=False, only=["chapters"]) == []
    assert await run_job(monkeypatch, url, status="succeeded", dry_run=False, only=["taxonomy", "plan"]) == []
