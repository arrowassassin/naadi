"""The index job as the API process runs it: one reconcile at a time, started by an admin, by the seed
importer (`schedule_reindex`), or once at start-up when the models are cached.

State lives in this process (the Space runs one worker); the durable truth is `public.embeddings` itself,
whose row counts the status endpoint reports.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..db import Database
from ..logs import get_logger
from .engine import Engine, ModelsNotCached
from .index import IndexReport, reconcile
from .sources import KINDS, Kind

log = get_logger("naadi.embeddings.jobs")


@dataclass
class JobState:
    status: str = "idle"  # idle | running | succeeded | failed
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    error: str | None = None
    report: dict[str, Any] | None = None
    kinds: tuple[str, ...] = ()


@dataclass
class IndexJobs:
    db: Database
    engine: Engine
    state: JobState = field(default_factory=JobState)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _tasks: set[asyncio.Task[Any]] = field(default_factory=set)

    @property
    def running(self) -> bool:
        return self.state.status == "running"

    async def run(
        self, kinds: Sequence[Kind] = KINDS, *, owner: uuid.UUID | None = None, ref_ids: Sequence[str] | None = None
    ) -> IndexReport | None:
        """Run a reconcile now (serialised; a second caller waits for the first)."""
        async with self._lock:
            self.state = JobState(status="running", started_at=dt.datetime.now(dt.UTC), kinds=tuple(kinds))
            try:
                report = await reconcile(self.db, self.engine, kinds=kinds, owner=owner, ref_ids=ref_ids)
            except ModelsNotCached as exc:
                self._finish("failed", error=str(exc))
                log.warning("embeddings.job_models_missing")
                return None
            except Exception as exc:  # recorded on the state; the job must always finish
                self._finish("failed", error=f"{type(exc).__name__}")
                log.exception("embeddings.job_failed", error=type(exc).__name__)
                return None
            self._finish("succeeded", report=report.to_dict())
            return report

    def _finish(self, status: str, *, error: str | None = None, report: dict[str, Any] | None = None) -> None:
        self.state.status = status
        self.state.finished_at = dt.datetime.now(dt.UTC)
        self.state.error = error
        self.state.report = report

    def schedule(
        self, kinds: Sequence[Kind] = KINDS, *, owner: uuid.UUID | None = None, ref_ids: Sequence[str] | None = None
    ) -> asyncio.Task[Any]:
        task = asyncio.create_task(self.run(kinds, owner=owner, ref_ids=ref_ids), name="embeddings-reindex")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


def schedule_reindex(app: Any, kinds: Sequence[Kind] = KINDS, **kw: Any) -> asyncio.Task[Any] | None:
    """Hook for other segments: `schedule_reindex(request.app, ("sections",), ref_ids=[section_id])` after a
    content change (seed importer, chapter editor, notes, uploads). Returns None when search is not set up."""
    jobs: IndexJobs | None = getattr(app.state, "embedding_jobs", None)
    return jobs.schedule(kinds, **kw) if jobs is not None else None


# The API process's job (set by `install_search_routes`), for code that runs outside a request in the same process:
# the `ink_ocr` graph indexes recognised handwriting with its engine.
_ACTIVE: IndexJobs | None = None


def register_index_jobs(jobs: IndexJobs | None) -> None:
    global _ACTIVE
    _ACTIVE = jobs


def active_index_jobs() -> IndexJobs | None:
    return _ACTIVE
