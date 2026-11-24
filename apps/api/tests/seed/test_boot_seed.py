"""The deploy seed (NAADI_SEED_ON_START): run on startup when on, skipped when off, never blocks startup.

No database: the run row and its final update are stubbed; a missing database is the real failure path.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from naadi_api.seed import jobs
from naadi_api.seed.importer import SeedOptions

from ..conftest import LogCapture


def _wait(cond: Callable[[], bool], timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not cond():
        assert time.monotonic() < deadline, "the background seed did not finish"
        time.sleep(0.02)


class FakeConn:
    def __init__(self, updates: list[tuple[Any, ...]]) -> None:
        self.updates = updates

    async def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.updates.append(params)


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the run row (insert + final update); keep the real _run / _execute / run_seed path."""
    rec: dict[str, Any] = {"inserted": [], "updates": []}

    async def insert_run(db: Any, started_by: uuid.UUID | None, req: jobs.SeedRequest) -> jobs.SeedRun:
        rec["inserted"].append((started_by, req))
        return jobs.SeedRun(
            id=uuid.uuid4(),
            status="running",
            options=req.model_dump(mode="json"),
            started_by=started_by,
            started_at=dt.datetime.now(dt.UTC),
            finished_at=None,
            error=None,
            report=None,
        )

    async def conn(db: Any) -> Any:
        @asynccontextmanager
        async def cm() -> AsyncIterator[FakeConn]:
            yield FakeConn(rec["updates"])

        return cm()

    monkeypatch.setattr(jobs, "_insert_run", insert_run)
    monkeypatch.setattr(jobs, "_conn", conn)
    return rec


def test_on_runs_the_deploy_groups_and_records_the_run(
    client_factory: Callable[..., TestClient], recorded: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[SeedOptions] = []

    def run_seed(opts: SeedOptions) -> Any:
        seen.append(opts)
        return type("R", (), {"model_dump": lambda self, mode: {"ok": True}})()

    monkeypatch.setattr(jobs, "run_seed", run_seed)
    monkeypatch.setattr(jobs, "storage_target", lambda s, u: None)
    c = client_factory(seed_on_start=True, database_url="postgresql://u:p@127.0.0.1:1/x")
    _wait(lambda: bool(recorded["updates"]))
    assert c.get("/api/health").status_code == 200
    ((started_by, req),) = recorded["inserted"]
    assert started_by is None and req == jobs.BOOT_SEED
    (opts,) = seen
    assert opts.only == ("sources", "videos", "app_config")
    assert not opts.dry_run and not opts.force
    assert recorded["updates"][0][0] == "succeeded"


def test_off_skips_the_seed(client_factory: Callable[..., TestClient], recorded: dict[str, Any]) -> None:
    c = client_factory()  # the default: off (local dev, tests)
    assert c.get("/api/health").status_code == 200
    assert recorded["inserted"] == []


def test_a_seed_error_is_recorded_and_the_api_still_serves(
    client_factory: Callable[..., TestClient], recorded: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(opts: SeedOptions) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(jobs, "run_seed", boom)
    monkeypatch.setattr(jobs, "storage_target", lambda s, u: None)
    c = client_factory(seed_on_start=True, database_url="postgresql://u:p@127.0.0.1:1/x")
    _wait(lambda: bool(recorded["updates"]))
    status, _report, error, _id = recorded["updates"][0]
    assert status == "failed" and "boom" in error
    assert c.get("/api/health").status_code == 200


def test_no_database_logs_and_the_api_still_serves(client_factory: Callable[..., TestClient], logs: LogCapture) -> None:
    c = client_factory(seed_on_start=True)  # Database(None): the real insert fails
    assert c.get("/api/health").status_code == 200
    assert any(line.get("event") == "boot_seed_failed" for line in logs.lines())


def test_youtube_refresh_runs_after_a_successful_seed(
    client_factory: Callable[..., TestClient], recorded: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    async def mapper_run(db: Any, api: Any, embedder: Any, rules: Any, channels: Any) -> Any:
        calls.append(channels)
        return jobs.mapper.Report(channels=0)

    monkeypatch.setattr(jobs, "run_seed", lambda opts: type("R", (), {"model_dump": lambda self, mode: {}})())
    monkeypatch.setattr(jobs, "storage_target", lambda s, u: None)
    monkeypatch.setattr(jobs, "GoogleYouTube", lambda key, quota: object())
    monkeypatch.setattr(jobs.mapper, "run", mapper_run)
    client_factory(seed_on_start=True, youtube_api_key="k" * 20, database_url="postgresql://u:p@127.0.0.1:1/x")
    _wait(lambda: bool(calls))
    assert calls == [[]]  # refresh only: no channel discovery
