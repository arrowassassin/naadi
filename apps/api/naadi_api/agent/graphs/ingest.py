"""`ingest`: universal ingest, one background run per item (segment 15; docs/AGENTS.md "Universal ingest").

    receive -> gate -> [ask_vault] -> vault | extract -> understand -> propose -> [ask_confirm] -> place -> index

The steps live in `naadi_api.ingest.pipeline` (framework-free); this module wires them and raises the two
interrupts:

- `ask_vault` `{"type": "confirm", "action": "ingest_vault", "item_id", "signals", "doc_type"}` when the local
  sensitivity gate finds a personal document; resume `{"decision": "vault" | "continue" | "discard"}`;
- `ask_confirm` `{"type": "confirm", "action": "ingest_placement", "item_id", "batch_id", "options": [...]}`;
  resume `{"option_id": "..."}`. Nothing is written before this answer.

Input: `{"item_id": "<uuid>", "mode": "start"}`, or `{"item_id", "mode": "place", "option_id"}` to place an item
that is waiting with a stored proposal (after Undo, or when its run was lost). The item must belong to the run's
user (`langgraph_auth_user`); the run never reads identity from its input. Vault items never reach `llm.py`: the
gate runs before the first model call and a vaulted run ends at `vault`.
"""

from __future__ import annotations

import uuid
from collections.abc import Hashable
from typing import Any, Literal, NotRequired, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from naadi_api.agent.identity import RunIdentityError, run_identity
from naadi_api.ingest import pipeline
from naadi_api.ingest.deps import build_deps


class IngestState(TypedDict):
    item_id: str
    mode: NotRequired[Literal["start", "place"]]
    option_id: NotRequired[str | None]
    next: NotRequired[str]
    signals: NotRequired[list[str]]
    doc_type: NotRequired[str | None]
    options: NotRequired[list[dict[str, Any]]]
    decision: NotRequired[str]
    error: NotRequired[str | None]


def _who(state: IngestState, config: RunnableConfig) -> tuple[uuid.UUID, uuid.UUID]:
    who = run_identity(config)
    if who.user_id is None:
        raise RunIdentityError("the ingest graph runs for a student")
    try:
        item = uuid.UUID(str(state.get("item_id")))
    except ValueError as exc:
        raise RunIdentityError("invalid item id") from exc
    return item, who.user_id


async def _step(state: IngestState, config: RunnableConfig, name: str, **kw: Any) -> dict[str, Any]:
    item, user = _who(state, config)
    deps = await build_deps(config)
    fn = {
        "receive": pipeline.receive,
        "gate": pipeline.gate,
        "extract": pipeline.extract,
        "understand": pipeline.understand_step,
        "propose": pipeline.propose,
        "index": pipeline.index_step,
    }[name]
    out = await pipeline.guarded(deps, item, user, lambda: fn(deps, item, user, **kw))
    update: dict[str, Any] = {"next": out.next, "error": out.detail.get("error")}
    if out.next == "ask_vault":
        update |= {"signals": out.detail.get("signals", []), "doc_type": out.detail.get("doc_type")}
    if out.next == "ask_confirm":
        update["options"] = out.detail.get("options", [])
    return update


async def receive(state: IngestState, config: RunnableConfig) -> dict[str, Any]:
    if state.get("mode") == "place" and state.get("option_id"):
        return {"next": "place"}
    return await _step(state, config, "receive")


async def gate(state: IngestState, config: RunnableConfig) -> dict[str, Any]:
    return await _step(state, config, "gate")


async def ask_vault(state: IngestState) -> dict[str, Any]:
    answer = interrupt(
        {
            "type": "confirm",
            "action": "ingest_vault",
            "item_id": state["item_id"],
            "signals": state.get("signals", []),
            "doc_type": state.get("doc_type"),
        }
    )
    decision = answer.get("decision") if isinstance(answer, dict) else None
    return {"decision": decision if decision in ("vault", "continue", "discard") else "vault"}


async def vault(state: IngestState, config: RunnableConfig) -> dict[str, Any]:
    item, user = _who(state, config)
    deps = await build_deps(config)
    decision = state.get("decision") or "vault"
    out = await pipeline.guarded(deps, item, user, lambda: pipeline.vault(deps, item, user, decision))
    return {"next": out.next, "error": out.detail.get("error")}


async def extract(state: IngestState, config: RunnableConfig) -> dict[str, Any]:
    out = await _step(state, config, "extract")
    if out["next"] == "understand" and state.get("option_id"):
        out["next"] = "place"  # rebuilding an expired extract for a placement that was already chosen
    return out


async def understand(state: IngestState, config: RunnableConfig) -> dict[str, Any]:
    return await _step(state, config, "understand")


async def propose(state: IngestState, config: RunnableConfig) -> dict[str, Any]:
    return await _step(state, config, "propose")


async def ask_confirm(state: IngestState, config: RunnableConfig) -> dict[str, Any]:
    item, user = _who(state, config)
    deps = await build_deps(config)
    batch = await pipeline.batch_of(deps, item, user)
    answer = interrupt(
        {
            "type": "confirm",
            "action": "ingest_placement",
            "item_id": state["item_id"],
            "batch_id": batch,
            "options": state.get("options", []),
        }
    )
    option = answer.get("option_id") if isinstance(answer, dict) else None
    valid = {o.get("option_id") for o in state.get("options", [])}
    if option not in valid:
        return {"next": "end", "error": "no_placement"}
    return {"option_id": option, "next": "place"}


async def place(state: IngestState, config: RunnableConfig) -> dict[str, Any]:
    item, user = _who(state, config)
    deps = await build_deps(config)
    option = str(state.get("option_id") or "")
    out = await pipeline.guarded(deps, item, user, lambda: pipeline.place(deps, item, user, option))
    return {"next": out.next, "error": out.detail.get("error")}


async def index(state: IngestState, config: RunnableConfig) -> dict[str, Any]:
    return await _step(state, config, "index")


def _m(**targets: str) -> dict[Hashable, str]:
    out: dict[Hashable, str] = {"end": END}
    out.update(targets.items())
    return out


def _route(state: IngestState) -> str:
    return state.get("next") or "end"


def build() -> StateGraph[IngestState]:
    g: StateGraph[IngestState] = StateGraph(IngestState)
    for name, fn in (
        ("receive", receive),
        ("gate", gate),
        ("ask_vault", ask_vault),
        ("vault", vault),
        ("extract", extract),
        ("understand", understand),
        ("propose", propose),
        ("ask_confirm", ask_confirm),
        ("place", place),
        ("index", index),
    ):
        g.add_node(name, fn)
    g.add_edge(START, "receive")
    ends: dict[Hashable, str] = {"end": END}
    g.add_conditional_edges("receive", _route, _m(gate="gate", place="place"))
    g.add_conditional_edges("gate", _route, _m(ask_vault="ask_vault", understand="understand", extract="extract"))
    g.add_edge("ask_vault", "vault")
    g.add_conditional_edges("vault", _route, _m(extract="extract"))
    g.add_conditional_edges("extract", _route, _m(understand="understand", place="place"))
    g.add_conditional_edges("understand", _route, _m(propose="propose"))
    g.add_conditional_edges("propose", _route, _m(ask_confirm="ask_confirm"))
    g.add_conditional_edges("ask_confirm", _route, _m(place="place"))
    g.add_conditional_edges("place", _route, _m(index="index", extract="extract"))
    g.add_conditional_edges("index", _route, ends)
    return g


graph = build()
