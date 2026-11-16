"""Process-wide agent services: runtime config, the LLM gateway, search and the agent DB pool.

Started once in the host lifespan (`naadi_api.agent.host`) and read by graphs through the module-level
getters (`naadi_api.llm.get_gateway`, `naadi_api.search.get_search`, `agent_db`). Graph modules are imported
by Aegra into the same process, so they share these singletons.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..db import Database
from ..llm import LLMGateway, set_gateway
from ..llm_budget import PgUsageLedger
from ..llm_config import RuntimeConfig, set_runtime_config
from ..logs import get_logger
from ..search import PgSearchStore, SearchService, set_search
from ..settings import Settings

log = get_logger("naadi.agent.runtime")


@dataclass
class AgentRuntime:
    settings: Settings
    db: Database
    config: RuntimeConfig
    gateway: LLMGateway
    search: SearchService

    async def close(self) -> None:
        await self.search.aclose()
        await self.db.close()


_RUNTIME: AgentRuntime | None = None


async def init_runtime(settings: Settings) -> AgentRuntime:
    """Idempotent. Opens a small dedicated pool (Aegra keeps its own pools for checkpoints and metadata)."""
    global _RUNTIME
    if _RUNTIME is not None:
        return _RUNTIME
    db = Database(settings.database_url, max_size=4)
    await db.open()
    rc = RuntimeConfig(db, settings.config_dir)
    gateway = LLMGateway(settings, rc, PgUsageLedger(db))
    search = SearchService(rc, PgSearchStore(db), tavily_key=settings.tavily_api_key, app_version=settings.app_version)
    set_runtime_config(rc)
    set_gateway(gateway)
    set_search(search)
    _RUNTIME = AgentRuntime(settings, db, rc, gateway, search)
    log.info("agent_runtime.started", database_configured=db.configured)
    return _RUNTIME


async def close_runtime() -> None:
    global _RUNTIME
    if _RUNTIME is None:
        return
    await _RUNTIME.close()
    set_gateway(None)
    set_search(None)
    set_runtime_config(None)
    _RUNTIME = None


def get_runtime() -> AgentRuntime:
    if _RUNTIME is None:
        raise RuntimeError("the agent runtime is not started (naadi_api.agent.host lifespan)")
    return _RUNTIME


def agent_db() -> Database:
    return get_runtime().db
