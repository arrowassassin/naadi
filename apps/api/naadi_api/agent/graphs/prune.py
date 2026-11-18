"""`prune`: the daily retention pass (segment 22, naadi_api.ops.prune), 04:11 UTC after the nightly backup.

Admins may run it by hand; students may not. Records one `agent_health` row (kind `prune`): rows deleted per table
(-1 for a table that does not exist yet).
"""

from __future__ import annotations

import asyncio
from typing import Any, NotRequired, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from naadi_api.agent.health import record_health
from naadi_api.agent.identity import RunIdentityError, run_identity
from naadi_api.agent.runtime import agent_db, get_runtime
from naadi_api.config import load_models_config
from naadi_api.ops.prune import prune, search_cache_hours


class PruneState(TypedDict):
    ok: NotRequired[bool]
    detail: NotRequired[dict[str, Any]]
    recorded_id: NotRequired[int]


async def run(state: PruneState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    if not (who.is_admin or who.is_system):
        raise RunIdentityError("the prune graph is admin-only")
    hours = search_cache_hours(load_models_config(get_runtime().settings.config_dir))
    db = agent_db()
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    async with db.pool.connection(timeout=10.0) as conn, asyncio.timeout(120.0):
        deleted = await prune(conn, hours)
    return {
        "ok": True,
        "detail": {"outcome": "ok", "deleted": {k: (-1 if v is None else v) for k, v in deleted.items()}},
    }


async def record(state: PruneState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    rid = await record_health(
        agent_db(),
        kind="prune",
        trigger="cron" if who.is_system else "background",
        ok=bool(state.get("ok")),
        run_id=who.run_id,
        detail=state.get("detail") or {},
    )
    return {"recorded_id": rid}


def build() -> StateGraph[PruneState]:
    g: StateGraph[PruneState] = StateGraph(PruneState)
    g.add_node("run", run)
    g.add_node("record", record)
    g.add_edge(START, "run")
    g.add_edge("run", "record")
    g.add_edge("record", END)
    return g


graph = build()
