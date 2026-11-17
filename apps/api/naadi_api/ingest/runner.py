"""Starting and resuming the `ingest` graph's background runs for a student.

- Hosted (the Space: `naadi_api.agent.host`): Aegra's own run preparation (`_prepare_run`, the path Aegra's cron
  scheduler uses), so runs are durable (Postgres checkpointer), visible to the Agent Protocol (`/threads/{id}/runs`)
  and resumed with `command.resume`. The run's user is the student (identity = their id, admin permission only
  for admins); the graph reads identity only from `langgraph_auth_user`.
- Not hosted (`naadi_api.main:app` alone, tests): the same graph compiled in-process with an in-memory checkpointer,
  run as an asyncio task; the agent runtime is started on first use.

The thread and run ids are stored on the item (`thread_id`, `run_id`), so `confirm` resumes the right run.
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from typing import Any, Protocol

from ..db import Database
from ..logs import get_logger
from . import store as st

log = get_logger("naadi.ingest.runner")
GRAPH = "ingest"


class Runner(Protocol):
    async def start(
        self, user_id: uuid.UUID, is_admin: bool, item_id: uuid.UUID, payload: dict[str, Any], db: Database
    ) -> tuple[str, str]: ...

    async def resume(
        self,
        user_id: uuid.UUID,
        is_admin: bool,
        item_id: uuid.UUID,
        thread_id: str,
        value: dict[str, Any],
        db: Database,
    ) -> str: ...


def _user(user_id: uuid.UUID, is_admin: bool) -> dict[str, Any]:
    return {
        "identity": str(user_id),
        "permissions": ["admin"] if is_admin else [],
        "is_authenticated": True,
        "display_name": "ingest",
    }


async def _record(db: Database, item_id: uuid.UUID, user_id: uuid.UUID, thread_id: str, run_id: str) -> None:
    async with st.service_tx(db) as conn:
        await conn.execute(
            "update public.ingest_items set thread_id = %s, run_id = %s where id = %s and user_id = %s",
            (thread_id, run_id, item_id, user_id),
        )


class AegraRunner:
    async def _run(self, user_id: uuid.UUID, is_admin: bool, thread_id: str, **req: Any) -> str:
        from aegra_api.core.database import db_manager
        from aegra_api.models import RunCreate, User
        from aegra_api.services.run_preparation import _prepare_run
        from sqlalchemy.ext.asyncio import AsyncSession

        async with AsyncSession(db_manager.get_engine()) as session:
            run_id, _run, _job = await _prepare_run(
                session,
                thread_id,
                RunCreate(assistant_id=GRAPH, **req),
                User(**_user(user_id, is_admin)),
                initial_status="pending",
            )
            await session.commit()
        return str(run_id)

    async def start(
        self, user_id: uuid.UUID, is_admin: bool, item_id: uuid.UUID, payload: dict[str, Any], db: Database
    ) -> tuple[str, str]:
        thread_id = str(uuid.uuid4())
        run_id = await self._run(user_id, is_admin, thread_id, input=payload, metadata={"naadi_item": str(item_id)})
        await _record(db, item_id, user_id, thread_id, run_id)
        return thread_id, run_id

    async def resume(
        self,
        user_id: uuid.UUID,
        is_admin: bool,
        item_id: uuid.UUID,
        thread_id: str,
        value: dict[str, Any],
        db: Database,
    ) -> str:
        run_id = await self._run(user_id, is_admin, thread_id, command={"resume": value})
        await _record(db, item_id, user_id, thread_id, run_id)
        return run_id


class InProcessRunner:
    def __init__(self, settings: Any = None, *, inline: bool = False) -> None:
        from langgraph.checkpoint.memory import InMemorySaver

        self._settings = settings
        # inline: the run finishes (or reaches its interrupt) before start/resume returns (tests, CLI tools).
        self._inline = inline
        self._saver = InMemorySaver()
        self._graph: Any = None
        self.tasks: set[asyncio.Task[None]] = set()

    def _compiled(self) -> Any:
        if self._graph is None:
            from ..agent.graphs.ingest import build

            self._graph = build().compile(checkpointer=self._saver)
        return self._graph

    async def _ensure_runtime(self) -> None:
        from ..agent.runtime import _RUNTIME, init_runtime

        if _RUNTIME is None and self._settings is not None:
            await init_runtime(self._settings)

    async def _spawn(self, inp: Any, config: dict[str, Any]) -> None:
        async def go() -> None:
            try:
                await self._ensure_runtime()
                async for _ in self._compiled().astream(inp, config, stream_mode="updates"):
                    pass
            except Exception as exc:  # noqa: BLE001 - the pipeline records the item's failure; log the kind only
                log.error("ingest.run_crashed", error=type(exc).__name__)

        if self._inline:
            await go()
            return
        task = asyncio.get_running_loop().create_task(go())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def _config(self, user_id: uuid.UUID, is_admin: bool, thread_id: str, run_id: str) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": thread_id, "run_id": run_id, "langgraph_auth_user": _user(user_id, is_admin)}
        }

    async def start(
        self, user_id: uuid.UUID, is_admin: bool, item_id: uuid.UUID, payload: dict[str, Any], db: Database
    ) -> tuple[str, str]:
        thread_id, run_id = str(uuid.uuid4()), str(uuid.uuid4())
        await _record(db, item_id, user_id, thread_id, run_id)
        await self._spawn(payload, self._config(user_id, is_admin, thread_id, run_id))
        return thread_id, run_id

    async def resume(
        self,
        user_id: uuid.UUID,
        is_admin: bool,
        item_id: uuid.UUID,
        thread_id: str,
        value: dict[str, Any],
        db: Database,
    ) -> str:
        from langgraph.types import Command

        run_id = str(uuid.uuid4())
        await _record(db, item_id, user_id, thread_id, run_id)
        await self._spawn(Command(resume=value), self._config(user_id, is_admin, thread_id, run_id))
        return run_id

    def knows(self, thread_id: str) -> bool:
        return any(k[0] == thread_id for k in self._saver.storage)

    async def drain(self) -> None:
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)


_RUNNER: Runner | None = None


def set_runner(r: Runner | None) -> None:
    global _RUNNER
    _RUNNER = r


def get_runner(settings: Any = None) -> Runner:
    global _RUNNER
    if _RUNNER is None:
        # Never import the host module here: importing it builds the hosted app. It is loaded only when hosted.
        host = sys.modules.get("naadi_api.agent.host")
        _RUNNER = AegraRunner() if getattr(host, "HOSTED", False) else InProcessRunner(settings)
    return _RUNNER


__all__ = ["AegraRunner", "InProcessRunner", "Runner", "get_runner", "set_runner"]
