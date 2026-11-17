"""`pathway_verify`: the weekly check of the official pages behind the pathway tasks (Aegra cron, Mondays 04:13 UTC).

Run by a system cron (naadi_api.agent.host); admins may run it by hand, students may not. The work is in
`naadi_api.pathway.verify` (fetch with httpx, hash, flag, at most `pathway.verify_max_llm_calls` model calls through
`naadi_api/llm.py`). The pass is recorded in `agent_health` (kind `pathway_verify`, counts only). It never changes
`last_verified` and never touches student data.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from naadi_api.agent.health import record_health
from naadi_api.agent.identity import RunIdentityError, run_identity
from naadi_api.agent.runtime import agent_db, get_runtime
from naadi_api.config_api.schemas import FeatureFlags, PathwayRules
from naadi_api.config_api.service import AppConfigService
from naadi_api.llm import get_gateway
from naadi_api.pathway.verify import HttpFetcher, verify_pass


class VerifyState(TypedDict):
    ok: NotRequired[bool]
    detail: NotRequired[dict[str, Any]]
    recorded_id: NotRequired[int]


async def run(state: VerifyState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    if not (who.is_admin or who.is_system):
        raise RunIdentityError("the pathway_verify graph is admin-only")
    rt = get_runtime()
    cfg_service = AppConfigService(agent_db(), rt.settings.config_dir)
    flags = FeatureFlags.model_validate(await cfg_service.get("feature_flags")).root
    if not flags.get("pathway") or not flags["pathway"].enabled:
        return {"ok": True, "detail": {"outcome": "flag_off"}}
    rules = PathwayRules.model_validate(await cfg_service.get("pathway"))
    domains = list((await rt.config.get()).search.include_domains)
    fetcher = HttpFetcher(domains, timeout_s=rules.verify_page_timeout_s, max_bytes=rules.verify_max_page_bytes)
    try:
        stats = await verify_pass(agent_db(), get_gateway(), rules, domains, fetcher, run_id=who.run_id)
    finally:
        await fetcher.aclose()
    return {"ok": True, "detail": {"outcome": "ok", **stats.as_dict()}}


async def record(state: VerifyState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    rid = await record_health(
        agent_db(),
        kind="pathway_verify",
        trigger="cron" if who.is_system else "background",
        ok=bool(state.get("ok")),
        run_id=who.run_id,
        detail=state.get("detail") or {},
    )
    return {"recorded_id": rid}


def build() -> StateGraph[VerifyState]:
    g: StateGraph[VerifyState] = StateGraph(VerifyState)
    g.add_node("run", run)
    g.add_node("record", record)
    g.add_edge(START, "run")
    g.add_edge("run", "record")
    g.add_edge("record", END)
    return g


graph = build()
