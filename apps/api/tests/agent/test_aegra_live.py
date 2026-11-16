"""Live services (real model and search calls, budget-aware): run with provider keys in the environment.

    DATABASE_URL=<local test container> uv run pytest tests/agent -m live

- one streamed `ping` (router role, NVIDIA first) with token streaming, a custom block and interrupt → resume;
- one real OpenRouter `:free` call through llm.py that lands an `llm_usage` row;
- one real Tavily search that is served from `search_cache` the second time.
Calls per run: 2 model requests (1 NVIDIA, 1 OpenRouter) and at most 1 Tavily credit (none when cached).
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from langchain_core.messages import HumanMessage

from naadi_api.db import Database
from naadi_api.llm import LLMGateway
from naadi_api.llm_budget import PgUsageLedger
from naadi_api.llm_config import RuntimeConfig
from naadi_api.search import PgSearchStore, SearchService
from naadi_api.settings import Settings

from ..db.rls import database_url, require_local
from .conftest import agent_health_rows, client, pg
from .host_server import Host

pytestmark = [pytest.mark.integration, pytest.mark.live]


async def test_ping_streams_tokens_a_block_and_interrupts_then_resumes(host: Host) -> None:
    c = client(host, host.admin_token)
    thread = await c.threads.create()
    events: dict[str, int] = {}
    tokens: list[str] = []
    blocks: list[dict[str, Any]] = []
    interrupted = False
    run_id: str | None = None
    async for part in c.runs.stream(
        thread["thread_id"],
        "ping",
        input={"trigger": "interactive"},
        stream_mode=["messages-tuple", "custom", "updates"],
    ):
        events[part.event] = events.get(part.event, 0) + 1
        if part.event == "metadata":
            run_id = part.data["run_id"]
        elif part.event == "messages":
            chunk, _meta = part.data
            if chunk.get("content"):
                tokens.append(str(chunk["content"]))
        elif part.event == "custom" and part.data.get("type") == "block":
            blocks.append(part.data["block"])
        elif part.event == "updates" and "__interrupt__" in part.data:
            interrupted = True
    assert run_id
    assert tokens, events  # tokens streamed from the router role
    assert blocks and blocks[0]["type"] == "table"
    assert interrupted
    state = await c.threads.get_state(thread["thread_id"])
    assert state["next"] == ["confirm"] or "confirm" in state["next"]
    resumed: list[str] = []
    async for part in c.runs.stream(
        thread["thread_id"], "ping", command={"resume": {"confirmed": True}}, stream_mode="updates"
    ):
        if part.event == "metadata":
            resumed.append(part.data["run_id"])
    final = await c.threads.get_state(thread["thread_id"])
    assert final["values"]["outcome"] == "ok"
    assert final["values"]["recorded_id"]
    rows = agent_health_rows(resumed[0])
    assert rows and rows[0][0] == "interactive" and rows[0][1] is True
    with pg() as conn:
        usage = conn.execute(
            "select provider, purpose, ok from public.llm_usage where run_id = %s", (run_id,)
        ).fetchall()
    assert usage and usage[-1][2] is True and usage[-1][1] == "job"


async def test_real_free_openrouter_call_lands_an_llm_usage_row() -> None:
    url = database_url()
    require_local(url)
    settings = Settings(_env_file=None, database_url=url)
    assert settings.openrouter_api_key is not None, "OPENROUTER_API_KEY is required for the live tests"
    db = Database(settings.database_url)
    await db.open()
    try:
        gw = LLMGateway(settings, RuntimeConfig(db, settings.config_dir), PgUsageLedger(db))
        run_id = f"live-{uuid.uuid4().hex[:12]}"
        msg = await gw.ainvoke(
            "reviewer", [HumanMessage("Reply with the single word: ready")], gw.context_for_run("job", run_id)
        )
        assert msg.response_metadata["naadi_provider"] == "openrouter"
        assert str(msg.response_metadata["naadi_model"]).endswith(":free")
    finally:
        await db.close()
    with psycopg.connect(url) as c:
        rows = c.execute(
            "select provider, purpose, role, ok, status, latency_ms from public.llm_usage"
            " where run_id = %s order by id",
            (run_id,),
        ).fetchall()
    assert rows[-1][:5] == ("openrouter", "job", "reviewer", True, 200)
    assert rows[-1][5] is not None and rows[-1][5] > 0


async def test_real_tavily_search_is_cached_on_the_second_call() -> None:
    url = database_url()
    require_local(url)
    settings = Settings(_env_file=None, database_url=url)
    assert settings.tavily_api_key is not None, "TAVILY_API_KEY is required for the live tests"
    db = Database(settings.database_url)
    await db.open()
    query = "ECFMG certification steps for international medical graduates"
    try:
        svc = SearchService(
            RuntimeConfig(db, settings.config_dir),
            PgSearchStore(db),
            tavily_key=settings.tavily_api_key,
            app_version="test",
        )
        first = await svc.search(query, user_id=None)
        second = await svc.search(query, user_id=None)
        await svc.aclose()
    finally:
        await db.close()
    assert first.intent == "pathway"
    assert first.layers["tavily"] in ("hit", "cache")
    assert second.layers["tavily"] == "cache"
    assert [r.url for r in first.results] == [r.url for r in second.results]
    assert first.results and all(r.url.startswith("https://") for r in first.results)
