"""Integration: the real Aegra + Naadi process against the local Supabase Postgres, with real model calls
(the `router` role; NVIDIA first). Acceptance 2 (migrations coexist), 3 (isolation) and the ping path of 4
(streaming, custom block, interrupt → resume, background run, cron fires and records).

    apps/api/tests/db/stack.sh up && DATABASE_URL=... uv run pytest tests/agent -m db
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from langgraph_sdk import get_client
from langgraph_sdk.errors import NotFoundError

from ..db.rls import database_url, require_local
from .host_server import Host, running_host

pytestmark = [pytest.mark.integration, pytest.mark.db]


@pytest.fixture(scope="module")
def host(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Host]:
    url = database_url()
    require_local(url)
    with running_host(url, tmp_path_factory.mktemp("agent-host")) as h:
        yield h


def client(h: Host, tok: str | None) -> Any:
    headers = {"authorization": f"Bearer {tok}"} if tok else {}
    return get_client(url=h.url, headers=headers)


async def expect_404(call: Any) -> None:
    """The SDK raises NotFoundError on most routes and a bare httpx.HTTPStatusError on some (join)."""
    try:
        await call()
    except NotFoundError:
        return
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
    else:
        pytest.fail("expected 404")
    if status != 404:
        pytest.fail(f"expected 404, got {status}")


def pg() -> psycopg.Connection:
    return psycopg.connect(database_url(), autocommit=True)


def agent_health_rows(run_id: str) -> list[tuple[Any, ...]]:
    with pg() as c:
        return c.execute(
            "select trigger, ok, provider from public.agent_health where run_id = %s", (run_id,)
        ).fetchall()


def test_aegra_and_naadi_migrations_coexist(host: Host) -> None:
    from aegra_api.core.migrations import get_alembic_config
    from alembic.script import ScriptDirectory

    from naadi_api import migrate

    aegra_head = ScriptDirectory.from_config(get_alembic_config()).get_current_head()
    with pg() as c:
        assert c.execute("select version_num from public.alembic_version").fetchone() == (aegra_head,)
        assert c.execute("select version_num from public.naadi_alembic_version").fetchone() == (
            migrate.head_revision(),
        )
        assert c.execute(
            "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace"
            " where n.nspname = 'public' and c.relkind in ('r','p') and not c.relrowsecurity"
        ).fetchone() == (0,)
        for t in ("thread", "runs", "crons", "checkpoints", "store", "assistant"):
            assert c.execute(
                "select has_table_privilege('anon', %s, 'select'), has_table_privilege('authenticated', %s, 'select')",
                (f"public.{t}", f"public.{t}"),
            ).fetchone() == (False, False), t


def test_unauthenticated_and_bad_tokens_are_refused(host: Host) -> None:
    for headers in ({}, {"authorization": "Bearer nope"}, {"authorization": "Basic abc"}):
        r = httpx.post(f"{host.url}/threads", json={}, headers=headers, timeout=10)
        assert r.status_code == 401, headers
    assert httpx.get(f"{host.url}/info", timeout=10).status_code == 200  # public
    assert httpx.get(f"{host.url}/metrics", timeout=10).status_code in (401, 404)  # metrics never exposed


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


async def test_background_run_completes(host: Host) -> None:
    c = client(host, host.admin_token)
    thread = await c.threads.create()
    run = await c.runs.create(thread["thread_id"], "ping", input={"trigger": "background"})
    await c.runs.join(thread["thread_id"], run["run_id"])
    got = await c.runs.get(thread["thread_id"], run["run_id"])
    assert got["status"] == "success"
    state = await c.threads.get_state(thread["thread_id"])
    assert state["values"]["recorded_id"]
    assert agent_health_rows(run["run_id"])[0][0] == "background"


async def test_system_cron_exists_and_fires(host: Host) -> None:
    with pg() as conn:
        row = conn.execute(
            "select cron_id, schedule from public.crons"
            " where user_id = 'naadi-system' and metadata->>'naadi_key' = 'naadi.ping.hourly'"
        ).fetchone()
        assert row is not None
        before = conn.execute("select coalesce(max(id), 0) from public.agent_health where trigger = 'cron'").fetchone()[
            0
        ]
        conn.execute(
            "update public.crons set next_run_date = now() - interval '1 second' where cron_id = %s", (row[0],)
        )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        with pg() as conn:
            got = conn.execute(
                "select id, ok from public.agent_health where trigger = 'cron' and id > %s", (before,)
            ).fetchone()
        if got:
            assert got[1] is True
            break
        await asyncio.sleep(2)
    else:
        pytest.fail(f"the system cron did not record a ping; see {host.log_path}")
    r = (await asyncio.to_thread(httpx.get, f"{host.url}/api/health", timeout=30)).json()
    assert r["components"]["agent_runtime"]["status"] == "ok", r["components"]["agent_runtime"]


async def test_user_b_cannot_see_or_touch_user_a_objects(host: Host) -> None:
    a = client(host, host.admin_token)
    b = client(host, host.other_token)
    thread = await a.threads.create(metadata={"probe": "isolation"})
    tid = thread["thread_id"]
    run = await a.runs.create(tid, "ping", input={"trigger": "background"})
    await a.runs.join(tid, run["run_id"])
    await a.store.put_item(["notes"], "k1", {"v": 1})
    probe = uuid.uuid4().hex
    await a.crons.create("ping", schedule="0 3 * * *", input={"trigger": "background"}, metadata={"probe": probe})
    with pg() as conn:
        found = conn.execute("select cron_id from public.crons where metadata->>'probe' = %s", (probe,)).fetchone()
    assert found is not None
    cron = {"cron_id": found[0]}

    calls = {
        "threads.get": lambda: b.threads.get(tid),
        "threads.get_state": lambda: b.threads.get_state(tid),
        "threads.get_history": lambda: b.threads.get_history(tid),
        "threads.update": lambda: b.threads.update(tid, metadata={"x": 1}),
        "threads.delete": lambda: b.threads.delete(tid),
        "runs.get": lambda: b.runs.get(tid, run["run_id"]),
        "runs.join": lambda: b.runs.join(tid, run["run_id"]),
        "runs.cancel": lambda: b.runs.cancel(tid, run["run_id"]),
        "runs.create": lambda: b.runs.create(tid, "ping", input={"trigger": "background"}),
    }
    not_404 = []
    for name, call in calls.items():
        try:
            await expect_404(call)
        except pytest.fail.Exception:
            not_404.append(name)
    assert not_404 == []
    assert await b.runs.list(tid) == []  # listing another user's thread's runs yields nothing

    async def stream_other() -> None:
        async for _ in b.runs.join_stream(tid, run["run_id"]):
            pass

    await expect_404(stream_other)
    assert all(t["thread_id"] != tid for t in await b.threads.search(limit=100))
    assert (
        await b.store.search_items(["notes"]) == {"items": []} or not (await b.store.search_items(["notes"]))["items"]
    )
    await expect_404(lambda: b.store.get_item(["notes"], "k1"))
    assert all(c["cron_id"] != cron["cron_id"] for c in await b.crons.search())
    # The owner still sees everything.
    assert (await a.threads.get(tid))["thread_id"] == tid
    assert (await a.store.get_item(["notes"], "k1"))["value"] == {"v": 1}
    await a.crons.delete(cron["cron_id"])


async def test_students_cannot_schedule_crons_or_edit_assistants_or_ping(host: Host) -> None:
    s = client(host, host.student_token)
    r = await asyncio.to_thread(
        httpx.post,
        f"{host.url}/runs/crons",
        json={"assistant_id": "ping", "schedule": "* * * * *", "input": {"trigger": "background"}},
        headers={"authorization": f"Bearer {host.student_token}"},
        timeout=10,
    )
    assert r.status_code == 403
    r = await asyncio.to_thread(
        httpx.post,
        f"{host.url}/assistants",
        json={"graph_id": "ping", "config": {}},
        headers={"authorization": f"Bearer {host.student_token}"},
        timeout=10,
    )
    assert r.status_code == 403
    thread = await s.threads.create()
    # A forged configurable.user_id (the admin's id) does not make the run an admin run.
    run = await s.runs.create(
        thread["thread_id"],
        "ping",
        input={"trigger": "background"},
        config={"configurable": {"user_id": str(host.admin_id)}},
    )
    await s.runs.join(thread["thread_id"], run["run_id"])
    got = await s.runs.get(thread["thread_id"], run["run_id"])
    assert got["status"] == "error"
    with pg() as conn:
        assert conn.execute("select count(*) from public.llm_usage where run_id = %s", (run["run_id"],)).fetchone() == (
            0,
        )


def test_spa_fallback_does_not_shadow_agent_routes(host: Host, tmp_path: Path) -> None:
    headers = {"authorization": f"Bearer {host.admin_token}"}
    r = httpx.get(f"{host.url}/threads/{uuid.uuid4()}", headers=headers, timeout=10)
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
    r = httpx.post(f"{host.url}/threads/search", json={"limit": 1}, headers=headers, timeout=10)
    assert r.status_code == 200
    r = httpx.get(f"{host.url}/api/nope", timeout=10)
    assert r.status_code == 404
