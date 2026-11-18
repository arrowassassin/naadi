"""`planner`: rebalance proposals after missed days, applied only after the student confirms; and the daily reminder
pass (system cron).

Student runs (`trigger`: student | coach | tutor): `propose` computes the proposal with the pure function in
`naadi_api.plan.rebalance` (no LLM, no I/O besides the database) and stores it in `plan_rebalances`, `confirm` raises
`interrupt()` with the counts, and `apply` changes the plan in one transaction only on `{"decision": "accept"}`
(`"decline"` marks the proposal declined). A plan that changed since the proposal was made is never overwritten: the
proposal expires and the student is shown a fresh one. Zero model calls.

System run (`mode: reminders`, cron every 15 minutes): sends the daily Web Push reminders (`naadi_api.push`).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal, NotRequired, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from naadi_api.agent.identity import RunIdentityError, run_identity
from naadi_api.agent.runtime import agent_db, get_runtime
from naadi_api.coach.run import service_conn
from naadi_api.config_api.schemas import FeatureFlags, PlannerRules
from naadi_api.config_api.service import AppConfigService
from naadi_api.plan import planner_store as ps
from naadi_api.push.reminders import reminder_pass

Trigger = Literal["student", "coach", "tutor"]
Outcome = Literal["applied", "declined", "stale", "gone", "nothing_to_do", "flag_off", "reminders"]


class PlannerState(TypedDict):
    mode: NotRequired[Literal["rebalance", "reminders"]]
    trigger: NotRequired[Trigger]
    rebalance_id: NotRequired[str]
    fingerprint: NotRequired[str]
    moves: NotRequired[int]
    drops: NotRequired[int]
    warnings: NotRequired[int]
    decision: NotRequired[Literal["accept", "decline"]]
    outcome: NotRequired[Outcome]
    detail: NotRequired[dict[str, Any]]


def _cfg() -> AppConfigService:
    return AppConfigService(agent_db(), get_runtime().settings.config_dir)


def route(state: PlannerState, config: RunnableConfig) -> Literal["reminders", "propose"]:
    who = run_identity(config)
    if who.is_system:
        return "reminders" if state.get("mode") == "reminders" else "propose"
    return "propose"


async def reminders(state: PlannerState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    if not who.is_system:
        raise RunIdentityError("the reminder pass is a system job")
    cfg = _cfg()
    rules = PlannerRules.model_validate(await cfg.get("planner"))
    flags = FeatureFlags.model_validate(await cfg.get("feature_flags")).root
    if not flags.get("planner") or not flags["planner"].enabled:
        return {"outcome": "flag_off"}
    stats = await reminder_pass(agent_db(), get_runtime().settings, rules, await cfg.get("ui_copy"), transport=None)
    return {"outcome": "reminders", "detail": stats.as_dict()}


async def propose(state: PlannerState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    user_id = who.user_id
    if user_id is None:
        raise RunIdentityError("a rebalance belongs to a student: the system identity cannot propose one")
    cfg = _cfg()
    flags = FeatureFlags.model_validate(await cfg.get("feature_flags")).root
    if not flags.get("planner") or not flags["planner"].enabled:
        return {"outcome": "flag_off"}
    rules = PlannerRules.model_validate(await cfg.get("planner"))
    trigger = state.get("trigger")
    async with service_conn(agent_db()) as conn:
        row, proposal = await ps.propose(
            conn,
            user_id,
            rules,
            trigger=trigger if trigger in ("student", "coach") else "student",
            thread_id=who.thread_id,
        )
    if row is None or proposal is None:
        return {"outcome": "nothing_to_do"}
    return {
        "rebalance_id": str(row["id"]),
        "fingerprint": proposal.fingerprint,
        "moves": len(proposal.moves),
        "drops": len(proposal.drops),
        "warnings": sum(w.count for w in proposal.warnings if w.code == "unplaced"),
    }


def after_propose(state: PlannerState) -> str:
    return "confirm" if state.get("rebalance_id") and not state.get("outcome") else END


def confirm(state: PlannerState) -> dict[str, Any]:
    """The student's confirm: nothing is written before this returns `accept`."""
    answer = interrupt(
        {
            "rebalance_id": state["rebalance_id"],
            "fingerprint": state["fingerprint"],
            "moves": state.get("moves", 0),
            "drops": state.get("drops", 0),
            "warnings": state.get("warnings", 0),
        }
    )
    decision = answer.get("decision") if isinstance(answer, dict) else None
    return {"decision": "accept" if decision == "accept" else "decline"}


async def apply(state: PlannerState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    user_id = who.user_id
    if user_id is None:
        raise RunIdentityError("the system identity cannot apply a rebalance")
    rid = uuid.UUID(state["rebalance_id"])
    async with service_conn(agent_db()) as conn:
        if state.get("decision") != "accept":
            await ps.decline(conn, user_id, rid)
            return {"outcome": "declined"}
        outcome, moved, dropped = await ps.apply(conn, user_id, rid)
    return {"outcome": outcome, "detail": {"moved": moved, "dropped": dropped}}


def build() -> StateGraph[PlannerState]:
    g: StateGraph[PlannerState] = StateGraph(PlannerState)
    g.add_node("reminders", reminders)
    g.add_node("propose", propose)
    g.add_node("confirm", confirm)
    g.add_node("apply", apply)
    g.add_conditional_edges(START, route, {"reminders": "reminders", "propose": "propose"})
    g.add_edge("reminders", END)
    g.add_conditional_edges("propose", after_propose, {"confirm": "confirm", END: END})
    g.add_edge("confirm", "apply")
    g.add_edge("apply", END)
    return g


graph = build()
