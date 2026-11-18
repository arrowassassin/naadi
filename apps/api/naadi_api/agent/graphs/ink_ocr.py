"""`ink_ocr`: handwriting search. Reads idle ink layers into `ink_layers.recognized_text` (segment 13).

Queueing (naadi_api/ink/ocr/queue.py): the ink save path marks a layer `pending` when its content changed; every
save restarts its idle clock. Two system crons (naadi_api.agent.host) run this graph:

- `{"mode": "sweep"}` every two minutes: `pending` layers idle for `app_config.ink_ocr.idle_seconds`;
- `{"mode": "nightly"}` at 00:23 UTC, after the daily caps reset: `deferred` and `pending` layers, plus the opt-in
  safety net (recognised text, OCR state and handwriting index rows of every student who has not opted in are
  cleared).

Handwriting search is opt-in: OFF unless `profiles.settings.handwriting_search` is exactly `true` (Settings ->
Pencil & ink). Layers of a student who has not opted in are never claimed, rendered or sent (ink/ocr/queue.py).

Each layer is rasterized with Pillow (ink only, black on white, 1600 px wide, tiles <= 2048 px, <= `max_tiles`),
one `vision` call per tile through `naadi_api/llm.py` (purpose `job`: the gateway scrubs text and enforces the job
reserve), the `{text, lines}` reply is validated with pydantic, stored and indexed (`ref_type 'ink'`). Over the job
budget or the student's `per_user_daily` cap a layer is `deferred`. A pass that did work is recorded in
`agent_health` (kind `ink_ocr`, counts only). Admins may run it by hand; students may not.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from naadi_api.agent.health import record_health
from naadi_api.agent.identity import RunIdentityError, run_identity
from naadi_api.agent.runtime import agent_db, get_runtime
from naadi_api.config_api.schemas import InkOcrRules
from naadi_api.config_api.service import AppConfigService
from naadi_api.ink.ocr.run import OcrPass, index_ink
from naadi_api.llm import get_gateway


class InkOcrState(TypedDict):
    mode: NotRequired[Literal["sweep", "nightly"]]
    ok: NotRequired[bool]
    worked: NotRequired[bool]
    detail: NotRequired[dict[str, Any]]
    recorded_id: NotRequired[int]


async def run(state: InkOcrState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    if not (who.is_admin or who.is_system):
        raise RunIdentityError("the ink_ocr graph is admin-only")
    mode: Literal["sweep", "nightly"] = "nightly" if state.get("mode") == "nightly" else "sweep"
    rt = get_runtime()
    rules = InkOcrRules.model_validate(await AppConfigService(agent_db(), rt.settings.config_dir).get("ink_ocr"))
    stats = await OcrPass(agent_db(), get_gateway(), rules, indexer=index_ink).run(mode)
    return {
        "ok": stats.errors == 0,
        "worked": stats.worked or mode == "nightly",
        "detail": {"outcome": "ok", **stats.as_dict()},
    }


async def record(state: InkOcrState, config: RunnableConfig) -> dict[str, Any]:
    if not state.get("worked"):
        return {}  # an idle sweep every two minutes would only fill agent_health
    who = run_identity(config)
    rid = await record_health(
        agent_db(),
        kind="ink_ocr",
        trigger="cron" if who.is_system else "background",
        ok=bool(state.get("ok")),
        run_id=who.run_id,
        detail=state.get("detail") or {},
    )
    return {"recorded_id": rid}


def build() -> StateGraph[InkOcrState]:
    g: StateGraph[InkOcrState] = StateGraph(InkOcrState)
    g.add_node("run", run)
    g.add_node("record", record)
    g.add_edge(START, "run")
    g.add_edge("run", "record")
    g.add_edge("record", END)
    return g


graph = build()
