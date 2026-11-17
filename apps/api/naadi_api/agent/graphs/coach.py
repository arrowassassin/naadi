"""`coach`: nightly (21:07 UTC ≈ 02:37 IST) study suggestions for Today.

Run by a system cron (naadi_api.agent.host). For each student with an active plan (at most
`app_config.coach.max_users_per_run`), the facts are computed in Python, one `structured` call through
`naadi_api/llm.py` words up to three suggestions (purpose `job`: the gateway strips PII and enforces the job
reserve), every number is checked against the facts, and the rest falls back to rules wording. The pass is
recorded in `agent_health` (kind `coach`, counts only). Admins may run it by hand; students may not.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from naadi_api.agent.health import record_health
from naadi_api.agent.identity import RunIdentityError, run_identity
from naadi_api.agent.runtime import agent_db, get_runtime
from naadi_api.coach.run import coach_pass
from naadi_api.config_api.schemas import CoachRules, FeatureFlags, PlannerRules
from naadi_api.config_api.service import AppConfigService
from naadi_api.llm import get_gateway


class CoachState(TypedDict):
    ok: NotRequired[bool]
    detail: NotRequired[dict[str, Any]]
    recorded_id: NotRequired[int]


async def run(state: CoachState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    if not (who.is_admin or who.is_system):
        raise RunIdentityError("the coach graph is admin-only")
    rt = get_runtime()
    cfg = AppConfigService(agent_db(), rt.settings.config_dir)
    rules = CoachRules.model_validate(await cfg.get("coach"))
    flags = FeatureFlags.model_validate(await cfg.get("feature_flags")).root
    if not flags.get("coach") or not flags["coach"].enabled:
        return {"ok": True, "detail": {"outcome": "flag_off"}}
    planner_rules = (
        PlannerRules.model_validate(await cfg.get("planner"))
        if flags.get("planner") and flags["planner"].enabled
        else None
    )
    stats = await coach_pass(agent_db(), get_gateway(), rules, run_id=who.run_id, planner_rules=planner_rules)
    return {"ok": stats.errors == 0, "detail": {"outcome": "ok", **stats.as_dict()}}


async def record(state: CoachState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    rid = await record_health(
        agent_db(),
        kind="coach",
        trigger="cron" if who.is_system else "background",
        ok=bool(state.get("ok")),
        run_id=who.run_id,
        detail=state.get("detail") or {},
    )
    return {"recorded_id": rid}


def build() -> StateGraph[CoachState]:
    g: StateGraph[CoachState] = StateGraph(CoachState)
    g.add_node("run", run)
    g.add_node("record", record)
    g.add_edge(START, "run")
    g.add_edge("run", "record")
    g.add_edge("record", END)
    return g


graph = build()
