"""In-process admin-triggered authoring runs (the cron path is the `author` graph; both call `pipeline`)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..logs import get_logger
from .pipeline import Deps, ItemOutcome, run_cell
from .selection import Cell

log = get_logger("naadi.authoring.jobs")
MAX_ACTIVE = 1  # one admin run at a time: the job reserve is small and the free chains are rate-limited
KEEP = 20


@dataclass
class Job:
    id: uuid.UUID
    cell: dict[str, str]
    count: int
    status: str = "running"  # running | done | failed
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    outcomes: list[str] = field(default_factory=list)
    error: str | None = None


class AuthoringJobs:
    def __init__(self) -> None:
        self.jobs: dict[uuid.UUID, Job] = {}
        self._tasks: set[asyncio.Task[Any]] = set()

    @property
    def active(self) -> list[Job]:
        return [j for j in self.jobs.values() if j.status == "running"]

    def start(
        self,
        deps: Deps,
        cell: Cell,
        objective: str,
        difficulty: str,
        count: int,
        started_by: uuid.UUID,
    ) -> Job | None:
        """None when a run is already going."""
        if len(self.active) >= MAX_ACTIVE:
            return None
        job = Job(uuid.uuid4(), cell.as_dict(), count)
        self.jobs[job.id] = job
        for old in sorted(self.jobs.values(), key=lambda j: j.started_at)[:-KEEP]:
            self.jobs.pop(old.id, None)
        task = asyncio.create_task(self._run(job, deps, cell, objective, difficulty, started_by), name="authoring-run")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _run(
        self, job: Job, deps: Deps, cell: Cell, objective: str, difficulty: str, started_by: uuid.UUID
    ) -> None:
        try:
            results: list[ItemOutcome] = await run_cell(
                deps, cell, objective, difficulty, count=job.count, trigger="admin", started_by=started_by
            )
            job.outcomes = [r.outcome for r in results]
            job.status = "done"
        except Exception as exc:  # recorded on the job; the run must always finish
            job.status = "failed"
            job.error = type(exc).__name__
            log.exception("authoring.job_failed", error=type(exc).__name__)
        finally:
            job.finished_at = datetime.now(UTC)

    async def drain(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
