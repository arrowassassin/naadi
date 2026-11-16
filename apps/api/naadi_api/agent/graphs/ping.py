"""`ping`: the agent runtime's self-test (admin diagnostics + hourly system cron).

One run exercises the whole platform path:
1. `ask` streams tokens from the `router` role through `llm.py` (budget guards, usage ledger);
2. `report` emits a custom `block` event (a table of what served the call);
3. `confirm` raises `interrupt()` and resumes on the admin's answer (interactive runs only);
4. `record` writes the result to `agent_health`.

`trigger` in the input: `interactive` (diagnostics panel: streams + interrupt), `background` (a background
run, no interrupt) or `cron` (the hourly system cron). Only admins and the system identity may run it:
it spends model quota. When the budget is exhausted the run still completes and records
`budget_exhausted` with the reset time, which is the degradation path the UI shows.
"""

from __future__ import annotations

import time
from typing import Any, Literal, NotRequired, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from naadi_api.agent.health import record_ping
from naadi_api.agent.identity import RunIdentityError, run_identity
from naadi_api.agent.runtime import agent_db
from naadi_api.llm import LLMUnavailable, get_gateway
from naadi_api.llm_budget import BudgetExhausted

Trigger = Literal["interactive", "background", "cron"]
PING_SYSTEM = "You are the health probe of a study app. Answer in one short sentence."
PING_PROMPT = "Confirm you are online and name one organ of the urinary system."


class PingState(TypedDict):
    trigger: NotRequired[Trigger]
    reply: NotRequired[str]
    outcome: NotRequired[Literal["ok", "budget_exhausted", "unavailable"]]
    provider: NotRequired[str | None]
    model: NotRequired[str | None]
    latency_ms: NotRequired[int | None]
    reset_at: NotRequired[str | None]
    confirmed: NotRequired[bool | None]
    recorded_id: NotRequired[int | None]


def _trigger(state: PingState) -> Trigger:
    t = state.get("trigger") or "interactive"
    return t if t in ("interactive", "background", "cron") else "interactive"


async def ask(state: PingState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    if not (who.is_admin or who.is_system):
        raise RunIdentityError("the ping graph is admin-only")
    trigger = _trigger(state)
    if trigger == "cron" and not who.is_system:
        trigger = "background"  # only the system cron records cron pings
    gateway = get_gateway()
    ctx = gateway.context_for_run("job", who.run_id, user_id=who.user_id)
    model = gateway.chat_model("router", ctx)
    started = time.monotonic()
    try:
        msg = await model.ainvoke([SystemMessage(PING_SYSTEM), HumanMessage(PING_PROMPT)], config)
    except BudgetExhausted as exc:
        return {"trigger": trigger, "outcome": "budget_exhausted", "reset_at": exc.reset_at.isoformat(), "reply": ""}
    except LLMUnavailable:
        return {"trigger": trigger, "outcome": "unavailable", "reply": ""}
    meta = msg.response_metadata
    return {
        "trigger": trigger,
        "outcome": "ok",
        "reply": str(msg.content)[:500],
        "provider": meta.get("naadi_provider"),
        "model": meta.get("naadi_model"),
        "latency_ms": int((time.monotonic() - started) * 1000),
        "reset_at": None,
    }


async def report(state: PingState) -> dict[str, Any]:
    writer = get_stream_writer()
    if state.get("outcome") == "budget_exhausted":
        writer(
            {
                "type": "block",
                "block": {"type": "notice", "data": {"kind": "budget_exhausted", "reset_at": state.get("reset_at")}},
            }
        )
    else:
        writer(
            {
                "type": "block",
                "block": {
                    "type": "table",
                    "data": {
                        "columns": ["Check", "Result"],
                        "rows": [
                            ["Outcome", state.get("outcome") or "unknown"],
                            ["Provider", state.get("provider") or "—"],
                            ["Model", state.get("model") or "—"],
                            ["Latency (ms)", str(state.get("latency_ms") or "—")],
                        ],
                    },
                },
            }
        )
    return {}


def route_after_report(state: PingState) -> str:
    return "confirm" if _trigger(state) == "interactive" else "record"


async def confirm(state: PingState) -> dict[str, Any]:
    answer = interrupt(
        {"type": "confirm", "action": "record_ping", "question": "Record this ping result in the health log?"}
    )
    confirmed = bool(answer.get("confirmed")) if isinstance(answer, dict) else bool(answer)
    return {"confirmed": confirmed}


async def record(state: PingState, config: RunnableConfig) -> dict[str, Any]:
    if _trigger(state) == "interactive" and not state.get("confirmed"):
        return {"recorded_id": None}
    who = run_identity(config)
    rid = await record_ping(
        agent_db(),
        trigger=_trigger(state),
        ok=state.get("outcome") == "ok",
        run_id=who.run_id,
        provider=state.get("provider"),
        model=state.get("model"),
        latency_ms=state.get("latency_ms"),
        detail={"outcome": state.get("outcome"), "reset_at": state.get("reset_at")},
    )
    return {"recorded_id": rid}


def build() -> StateGraph[PingState]:
    g: StateGraph[PingState] = StateGraph(PingState)
    g.add_node("ask", ask)
    g.add_node("report", report)
    g.add_node("confirm", confirm)
    g.add_node("record", record)
    g.add_edge(START, "ask")
    g.add_edge("ask", "report")
    g.add_conditional_edges("report", route_after_report, {"confirm": "confirm", "record": "record"})
    g.add_edge("confirm", "record")
    g.add_edge("record", END)
    return g


# Aegra compiles it with its Postgres checkpointer and store.
graph = build()
