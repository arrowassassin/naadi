"""`author`: question authoring and item statistics (segment 21).

Run by system crons (naadi_api.agent.host) or by an admin:

- `{"mode": "nightly"}` 22:41 UTC: fill coverage gaps (topics under `authoring.min_published_per_topic` published
  questions) with drafts that pass the linter, the citation check and the blind solver (a different model family)
  and wait in the human review queue. Capped by `max_items_per_night` and `max_llm_calls_per_night`, through the
  gateway's job reserve. Nothing is published.
- `{"mode": "psychometrics"}` 23:11 UTC: item statistics from first attempts into `questions.stats` with flags.
- `{"mode": "cell", ...}` (admins): one taxonomy cell, `count` items.

Each pass is recorded in `agent_health` (kind `author` / `psychometrics`, counts only).
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from naadi_api.agent.health import record_health
from naadi_api.agent.identity import RunIdentityError, run_identity
from naadi_api.agent.runtime import agent_db, get_runtime
from naadi_api.authoring import pipeline, psychometrics_job
from naadi_api.authoring.deps import build_deps, load_rules
from naadi_api.authoring.selection import CellError, build_cell
from naadi_api.config_api.service import AppConfigService
from naadi_api.llm import get_gateway


class AuthorState(TypedDict):
    mode: NotRequired[Literal["nightly", "psychometrics", "cell"]]
    exam: NotRequired[Literal["step1", "step2ck"]]
    topic_id: NotRequired[str]
    second_id: NotRequired[str]
    competency_id: NotRequired[str]
    objective: NotRequired[str]
    difficulty: NotRequired[Literal["easy", "medium", "hard"]]
    count: NotRequired[int]
    ok: NotRequired[bool]
    kind: NotRequired[str]
    detail: NotRequired[dict[str, Any]]
    recorded_id: NotRequired[int]


async def run(state: AuthorState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    if not (who.is_admin or who.is_system):
        raise RunIdentityError("the author graph is admin-only")
    mode = state.get("mode", "nightly")
    rt = get_runtime()
    cfg = AppConfigService(agent_db(), rt.settings.config_dir)
    rules = await load_rules(cfg)
    if mode == "psychometrics":
        stats = await psychometrics_job.run(agent_db(), rules)
        return {"ok": True, "kind": "psychometrics", "detail": {"outcome": "ok", **stats.as_dict()}}
    deps = await build_deps(agent_db(), get_gateway(), rt.settings, cfg, rules)
    if mode == "nightly":
        night = await pipeline.nightly(deps)
        return {"ok": True, "kind": "author", "detail": {"outcome": night.skipped or "ok", **night.as_dict()}}
    if who.is_system:
        raise RunIdentityError("a cell run needs an admin")
    taxonomy = await deps.store.taxonomy()
    try:
        cell = build_cell(
            taxonomy,
            state.get("exam", "step1"),
            state.get("topic_id", ""),
            second_id=state.get("second_id"),
            competency_id=state.get("competency_id"),
        )
    except CellError as exc:
        return {"ok": False, "kind": "author", "detail": {"outcome": "cell_invalid", "reason": exc.code}}
    objective = state.get("objective") or f"Apply the key concept of {taxonomy[cell.topic_id].label}."
    results = await pipeline.run_cell(
        deps,
        cell,
        objective,
        state.get("difficulty", "medium"),
        count=max(1, min(3, state.get("count", 1))),
        trigger="admin",
        started_by=who.user_id,
    )
    return {"ok": True, "kind": "author", "detail": {"outcome": "ok", "outcomes": [r.outcome for r in results]}}


async def record(state: AuthorState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    rid = await record_health(
        agent_db(),
        kind=state.get("kind", "author"),
        trigger="cron" if who.is_system else "background",
        ok=bool(state.get("ok")),
        run_id=who.run_id,
        detail=state.get("detail") or {},
    )
    return {"recorded_id": rid}


def build() -> StateGraph[AuthorState]:
    g: StateGraph[AuthorState] = StateGraph(AuthorState)
    g.add_node("run", run)
    g.add_node("record", record)
    g.add_edge(START, "run")
    g.add_edge("run", "record")
    g.add_edge("record", END)
    return g


graph = build()
