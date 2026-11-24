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

import psycopg
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


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Nothing really waits: the settle delay and the backoff are recorded instead."""
    seen: list[float] = []

    async def fake_sleep(s: float) -> None:
        seen.append(s)

    monkeypatch.setattr(jobs, "_sleep", fake_sleep)
    return seen


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
    _wait(lambda: any(line.get("event") == "boot_seed_failed" for line in logs.lines()))
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


_DB = "postgresql://u:p@127.0.0.1:1/x"


def _ok_report(opts: SeedOptions) -> Any:
    return type("R", (), {"model_dump": lambda self, mode: {}})()


def test_a_full_pooler_is_retried_on_the_same_row_then_the_youtube_refresh_runs(
    client_factory: Callable[..., TestClient],
    recorded: dict[str, Any],
    sleeps: list[float],
    logs: LogCapture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[int] = []
    refreshed: list[bool] = []

    def run_seed(opts: SeedOptions) -> Any:
        attempts.append(1)
        if len(attempts) == 1:
            raise psycopg.OperationalError("FATAL: (EMAXCONNSESSION) max clients reached in session mode")
        return _ok_report(opts)

    async def refresh(app: Any) -> None:
        refreshed.append(True)

    monkeypatch.setattr(jobs, "run_seed", run_seed)
    monkeypatch.setattr(jobs, "storage_target", lambda s, u: None)
    monkeypatch.setattr(jobs, "_refresh_youtube", refresh)
    client_factory(seed_on_start=True, database_url=_DB)
    _wait(lambda: bool(refreshed))
    assert len(attempts) == 2 and len(recorded["inserted"]) == 1  # one row, one run at a time
    assert [u[0] for u in recorded["updates"]] == ["succeeded"]  # the failed attempt never finalised the row
    assert sleeps == [45.0, 30.0]  # settle delay, then the first backoff
    retries = [ln for ln in logs.lines() if ln.get("event") == "boot_seed_retry"]
    assert [(r["attempt"], r["error"]) for r in retries] == [(1, "OperationalError")]
    assert "EMAXCONN" not in logs.text


def test_a_pooler_that_stays_full_gives_up_and_records_the_failure(
    client_factory: Callable[..., TestClient],
    recorded: dict[str, Any],
    sleeps: list[float],
    logs: LogCapture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_seed(opts: SeedOptions) -> Any:
        raise psycopg.OperationalError("max clients reached")

    monkeypatch.setattr(jobs, "run_seed", run_seed)
    monkeypatch.setattr(jobs, "storage_target", lambda s, u: None)
    c = client_factory(seed_on_start=True, database_url=_DB)
    _wait(lambda: bool(recorded["updates"]))
    assert recorded["updates"][0][0] == "failed"
    assert sleeps == [45.0, 30.0, 60.0, 120.0, 240.0]
    assert c.get("/api/health").status_code == 200


@pytest.mark.parametrize("exc", [RuntimeError("boom"), jobs.SeedDataError(["bad row"])])
def test_a_real_seed_error_is_not_retried(
    client_factory: Callable[..., TestClient],
    recorded: dict[str, Any],
    sleeps: list[float],
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
) -> None:
    calls: list[int] = []

    def run_seed(opts: SeedOptions) -> Any:
        calls.append(1)
        raise exc

    monkeypatch.setattr(jobs, "run_seed", run_seed)
    monkeypatch.setattr(jobs, "storage_target", lambda s, u: None)
    client_factory(seed_on_start=True, database_url=_DB)
    _wait(lambda: bool(recorded["updates"]))
    assert recorded["updates"][0][0] == "failed"
    assert len(calls) == 1 and sleeps == [45.0]


def test_the_settle_delay_is_a_setting_and_comes_before_the_run_row(
    client_factory: Callable[..., TestClient],
    recorded: dict[str, Any],
    sleeps: list[float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    real_insert = jobs._insert_run

    async def insert(db: Any, by: Any, req: Any) -> Any:
        order.append(f"insert after {sleeps}")
        return await real_insert(db, by, req)

    monkeypatch.setattr(jobs, "_insert_run", insert)
    monkeypatch.setattr(jobs, "run_seed", _ok_report)
    monkeypatch.setattr(jobs, "storage_target", lambda s, u: None)
    client_factory(seed_on_start=True, seed_on_start_delay_s=7, database_url=_DB)
    _wait(lambda: bool(recorded["updates"]))
    assert order == ["insert after [7.0]"]
