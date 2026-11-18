"""`youtube_map`: weekly, refresh stored YouTube metadata and propose new videos for the approval queue.

Run by a system cron (Sunday 05:17 UTC, naadi_api.agent.host). It makes no model call: scoring is local fastembed
similarity. The Data API key comes from settings (`YOUTUBE_API_KEY`); without it the run records `not_configured`.
Admins may start it by hand; students may not. The outcome (counts and quota units, nothing else) goes to
`agent_health` kind `youtube_map`.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from naadi_api.agent.health import record_health
from naadi_api.agent.identity import RunIdentityError, run_identity
from naadi_api.agent.runtime import agent_db, get_runtime
from naadi_api.config_api.schemas import YoutubeRules
from naadi_api.config_api.service import AppConfigService
from naadi_api.embeddings.engine import get_engine
from naadi_api.youtube import mapper
from naadi_api.youtube.client import GoogleYouTube, Quota


class MapState(TypedDict):
    ok: NotRequired[bool]
    detail: NotRequired[dict[str, Any]]
    recorded_id: NotRequired[int]


async def run(state: MapState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    if not (who.is_admin or who.is_system):
        raise RunIdentityError("the youtube_map graph is admin-only")
    rt = get_runtime()
    key = rt.settings.youtube_api_key
    if key is None:
        return {"ok": False, "detail": {"outcome": "not_configured"}}
    rules = YoutubeRules.model_validate(await AppConfigService(agent_db(), rt.settings.config_dir).get("youtube"))
    if not rules.enabled:
        return {"ok": True, "detail": {"outcome": "disabled"}}
    api = GoogleYouTube(key.get_secret_value(), Quota(rules.quota_units_per_run))
    channels = mapper.load_channels(rt.settings.config_dir)
    rep = await mapper.run(agent_db(), api, get_engine(rt.settings.config_dir), rules, channels)
    return {
        "ok": not rep.errors,
        "detail": {
            "outcome": "stopped_early" if rep.stopped_early else "ok",
            **rep.out().model_dump(),
            "errors": rep.errors[:5],
        },
    }


async def record(state: MapState, config: RunnableConfig) -> dict[str, Any]:
    who = run_identity(config)
    rid = await record_health(
        agent_db(),
        kind="youtube_map",
        trigger="cron" if who.is_system else "background",
        ok=bool(state.get("ok")),
        run_id=who.run_id,
        detail=state.get("detail") or {},
    )
    return {"recorded_id": rid}


def build() -> StateGraph[MapState]:
    g: StateGraph[MapState] = StateGraph(MapState)
    g.add_node("run", run)
    g.add_node("record", record)
    g.add_edge(START, "run")
    g.add_edge("run", "record")
    g.add_edge("record", END)
    return g


graph = build()
