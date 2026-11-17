"""Integration: the real Aegra + Naadi process against the local Supabase Postgres, with real model calls
(the `router` role; NVIDIA first). Acceptance 2 (migrations coexist), 3 (isolation) and the ping path of 4
(streaming, custom block, interrupt → resume, background run, cron fires and records).

    apps/api/tests/db/stack.sh up && DATABASE_URL=... uv run pytest tests/agent -m db
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path

import httpx
import pytest

from .conftest import agent_health_rows, client, expect_404, pg
from .host_server import Host

pytestmark = [pytest.mark.integration, pytest.mark.db]


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


async def test_background_run_completes(host: Host) -> None:
    c = client(host, host.admin_token)
    thread = await c.threads.create()
    run = await c.runs.create(thread["thread_id"], "ping", input={"trigger": "background"})
    await c.runs.join(thread["thread_id"], run["run_id"])
    got = await c.runs.get(thread["thread_id"], run["run_id"])
    assert got["status"] == "success"
    state = await c.threads.get_state(thread["thread_id"])
    assert state["values"]["recorded_id"]
    # With provider keys the outcome is "ok" (test_aegra_live.py asserts it); without them the run still
    # completes and records "unavailable" rather than failing.
    assert state["values"]["outcome"] in ("ok", "unavailable", "budget_exhausted")
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
            break
        await asyncio.sleep(2)
    else:
        pytest.fail(f"the system cron did not record a ping; see {host.log_path}")
    r = (await asyncio.to_thread(httpx.get, f"{host.url}/api/health", timeout=30)).json()
    rt = r["components"]["agent_runtime"]
    assert rt["status"] == "ok", rt
    # Structured facts for the client to word (ui_copy); the English `detail` is for logs and ops only.
    assert rt["agent"]["reason"] == "ok", rt
    assert "ping" in rt["agent"]["graphs"] and rt["agent"]["failing"] == []
    assert rt["agent"]["last_cron_ping"] is not None


async def test_user_b_cannot_see_or_touch_user_a_objects(host: Host) -> None:
    a = client(host, host.admin_token)
    b = client(host, host.other_token)
    thread = await a.threads.create(metadata={"probe": "isolation"})
    tid = thread["thread_id"]
    run = await a.runs.create(tid, "ping", input={"trigger": "background"})
    await a.runs.join(tid, run["run_id"])
    # The store holds long-term memory (S14): it is server-only, so no client can write or read it directly.
    with pytest.raises(httpx.HTTPStatusError) as denied:
        await a.store.put_item(["notes"], "k1", {"v": 1})
    assert denied.value.response.status_code in (403, 404)
    # Crons are the server's (the system ping is ensured at startup); the protocol's cron routes are refused for
    # every client, admins included (S14 review14: deny by default, only the routes the web app uses).
    with pytest.raises(httpx.HTTPStatusError) as denied:
        await a.crons.create("ping", schedule="0 3 * * *", input={"trigger": "background"})
    assert denied.value.response.status_code == 403

    calls = {
        "threads.get_state": lambda: b.threads.get_state(tid),
        "threads.get_history": lambda: b.threads.get_history(tid),
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
    # Routes the web app does not use are refused for every client before any ownership check (S14 review14:
    # deny by default), so B gets 403 there, not 404.
    for name, call in {
        "threads.get": lambda: b.threads.get(tid),
        "threads.update": lambda: b.threads.update(tid, metadata={"x": 1}),
        "threads.delete": lambda: b.threads.delete(tid),
        "threads.search": lambda: b.threads.search(limit=100),
        "crons.search": lambda: b.crons.search(),
    }.items():
        with pytest.raises(httpx.HTTPStatusError) as refused:
            await call()
        assert refused.value.response.status_code in (403, 404), name
    assert await b.runs.list(tid) == []  # listing another user's thread's runs yields nothing

    async def stream_other() -> None:
        async for _ in b.runs.join_stream(tid, run["run_id"]):
            pass

    await expect_404(stream_other)
    for who in (a, b):
        with pytest.raises(httpx.HTTPStatusError):
            await who.store.search_items(["notes"])
    # The owner still sees everything through the routes the app uses.
    assert (await a.threads.get_state(tid))["values"] is not None
    assert any(r["run_id"] == run["run_id"] for r in await a.runs.list(tid))


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
    r = httpx.get(f"{host.url}/threads/{uuid.uuid4()}/state", headers=headers, timeout=10)
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
    r = httpx.post(f"{host.url}/threads", json={}, headers=headers, timeout=10)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    r = httpx.get(f"{host.url}/api/nope", timeout=10)
    assert r.status_code == 404


async def test_concurrent_requests_all_authenticate_within_the_connection_budget(host: Host) -> None:
    """Regression (S04 review item 1): bursts of authenticated Agent Protocol calls never come back 401/503,
    and the process stays within its connection budget (Aegra pools sized in naadi_api.agent.host)."""
    tokens = [host.admin_token, host.student_token, host.other_token]
    peak = 0

    async def watch(stop: asyncio.Event) -> None:
        nonlocal peak
        while not stop.is_set():
            with pg() as c:
                n = c.execute(
                    "select count(*) from pg_stat_activity where backend_type = 'client backend'"
                    " and client_port is not null and pid <> pg_backend_pid()"
                ).fetchone()
            peak = max(peak, int(n[0]) if n else 0)
            await asyncio.sleep(0.05)

    stop = asyncio.Event()
    watcher = asyncio.create_task(watch(stop))
    async with httpx.AsyncClient(timeout=30) as c:
        own: list[str] = []
        for tok in tokens:
            created = await c.post(f"{host.url}/threads", json={}, headers={"authorization": f"Bearer {tok}"})
            own.append(created.json()["thread_id"])

        async def one(i: int) -> int:
            hdr = {"authorization": f"Bearer {tokens[i % 3]}"}
            r = await c.get(f"{host.url}/threads/{own[i % 3]}/state", headers=hdr)
            return r.status_code

        for n in (20, 120):
            codes = await asyncio.gather(*(one(i) for i in range(n)))
            assert set(codes) == {200}, codes
    stop.set()
    await watcher
    # Every client of this test database (this host, the test's own connections); far below max_connections.
    assert peak <= 30, peak


async def test_checkpoint_retention_keeps_only_the_latest_state(host: Host) -> None:
    """aegra.json keeps_latest after 30 days; the test host sweeps every 3 s. An expired thread loses its
    checkpoint history but keeps its latest state, and its TTL is re-armed."""
    c = client(host, host.admin_token)
    thread = await c.threads.create()
    tid = thread["thread_id"]
    for _ in range(2):
        run = await c.runs.create(tid, "ping", input={"trigger": "background"})
        await c.runs.join(tid, run["run_id"])

    def checkpoints() -> int:
        with pg() as conn:
            row = conn.execute("select count(*) from public.checkpoints where thread_id = %s", (tid,)).fetchone()
        return int(row[0]) if row else 0

    before = checkpoints()
    assert before > 1
    with pg() as conn:
        assert conn.execute("select strategy from public.thread_ttl where thread_id = %s", (tid,)).fetchone() == (
            "keep_latest",
        )
        conn.execute(
            "update public.thread_ttl set expires_at = now() - interval '1 second' where thread_id = %s", (tid,)
        )
    for _ in range(60):  # the sweeper runs every 3 s
        if checkpoints() <= 1:
            break
        await asyncio.sleep(1)
    assert checkpoints() == 1
    state = await c.threads.get_state(tid)
    assert state["values"]["recorded_id"]  # the latest state survives
    with pg() as conn:
        rearmed = conn.execute(
            "select expires_at > now() + interval '29 days' from public.thread_ttl where thread_id = %s", (tid,)
        ).fetchone()
    assert rearmed == (True,)


async def test_tavily_usage_reconciliation_records_and_never_blocks(host: Host) -> None:
    """The daily system cron exists; a run records the reading next to our ledger (no key here: recorded as
    not configured, the run still succeeds). Students cannot run it."""
    with pg() as conn:
        cron = conn.execute(
            "select schedule from public.crons where user_id = 'naadi-system'"
            " and metadata->>'naadi_key' = 'naadi.tavily_usage.daily'"
        ).fetchone()
    assert cron == ("43 3 * * *",)
    a = client(host, host.admin_token)
    thread = await a.threads.create()
    run = await a.runs.create(thread["thread_id"], "tavily_usage", input={})
    await a.runs.join(thread["thread_id"], run["run_id"])
    assert (await a.runs.get(thread["thread_id"], run["run_id"]))["status"] == "success"
    with pg() as conn:
        row = conn.execute(
            "select kind, ok, detail->>'outcome', (detail->>'ledger_month_credits')::int >= 0"
            " from public.agent_health where run_id = %s",
            (run["run_id"],),
        ).fetchone()
    assert row == ("tavily_usage", False, "not_configured", True)
    s = client(host, host.student_token)
    st = await s.threads.create()
    srun = await s.runs.create(st["thread_id"], "tavily_usage", input={})
    await s.runs.join(st["thread_id"], srun["run_id"])
    assert (await s.runs.get(st["thread_id"], srun["run_id"]))["status"] == "error"


async def test_ink_ocr_graph_runs_in_the_host_on_its_crons(host: Host) -> None:
    """Segment 13: both system crons exist; an admin sweep runs the graph end to end in the real host (no layer is
    due, so no model is called and nothing is recorded); the nightly pass records its counts. Students cannot run
    it."""
    with pg() as conn:
        rows = conn.execute(
            "select metadata->>'naadi_key', schedule, payload->'input' from public.crons"
            " where user_id = 'naadi-system' and metadata->>'naadi_key' like 'naadi.ink_ocr.%%' order by 1"
        ).fetchall()
        conn.execute("update public.ink_layers set ocr_status = null where ocr_status is not null")
    assert [(k, s) for k, s, _ in rows] == [
        ("naadi.ink_ocr.nightly", "23 0 * * *"),
        ("naadi.ink_ocr.sweep", "*/2 * * * *"),
    ]
    a = client(host, host.admin_token)
    for mode, recorded in (("sweep", False), ("nightly", True)):
        thread = await a.threads.create()
        run = await a.runs.create(thread["thread_id"], "ink_ocr", input={"mode": mode})
        await a.runs.join(thread["thread_id"], run["run_id"])
        assert (await a.runs.get(thread["thread_id"], run["run_id"]))["status"] == "success", mode
        with pg() as conn:
            row = conn.execute(
                "select kind, ok, detail->>'mode' from public.agent_health where run_id = %s", (run["run_id"],)
            ).fetchone()
        assert (row == ("ink_ocr", True, "nightly")) if recorded else row is None, (mode, row)
    s = client(host, host.student_token)
    st = await s.threads.create()
    srun = await s.runs.create(st["thread_id"], "ink_ocr", input={"mode": "sweep"})
    await s.runs.join(st["thread_id"], srun["run_id"])
    assert (await s.runs.get(st["thread_id"], srun["run_id"]))["status"] == "error"


async def test_ink_saved_in_the_host_is_queued_and_read_through_the_real_gateway(host: Host) -> None:
    """The whole path in one process: the student opts in to handwriting search (off by default), the notes API
    makes a notebook page, the ink API's save hook queues its layer, the admin-run sweep reads it through the real
    LLM gateway (privacy directory included). No provider key is configured in this host, so the gateway answers
    `LLMUnavailable` and the layer waits as `deferred`."""
    from ..ink.test_ink_db import stroke

    h = {"authorization": f"Bearer {host.student_token}"}
    async with httpx.AsyncClient(base_url=host.url, timeout=20.0) as http:
        # Handwriting search is opt-in (off by default): the student turns it on first.
        r = await http.patch("/api/me", json={"settings": {"handwriting_search": True}}, headers=h)
        assert r.status_code == 200, r.text
        nb = (await http.post("/api/notebooks", json={"title": "Renal"}, headers=h)).json()
        page = nb["pages"][0]["id"]
        items = [stroke(points=[[0.1, 0.1 + i / 20, 0.5, 0], [0.5, 0.1 + i / 20, 0.5, 9]]) for i in range(3)]
        r = await http.post(f"/api/ink/notebook_page/{page}/changes", json={"base_rev": 0, "items": items}, headers=h)
        assert r.status_code == 200, r.text
    with pg() as conn:
        conn.execute(
            "update public.ink_layers set ocr_status = null where ocr_status is not null and surface_id <> %s", (page,)
        )
        assert conn.execute(
            "select ocr_status from public.ink_layers where surface_type = 'notebook_page' and surface_id = %s", (page,)
        ).fetchone() == ("pending",)
        conn.execute("set session_replication_role = replica")
        conn.execute(
            "update public.ink_layers set updated_at = now() - interval '1 hour' where surface_id = %s", (page,)
        )
        conn.execute("set session_replication_role = origin")
    a = client(host, host.admin_token)
    thread = await a.threads.create()
    run = await a.runs.create(thread["thread_id"], "ink_ocr", input={"mode": "sweep"})
    await a.runs.join(thread["thread_id"], run["run_id"])
    assert (await a.runs.get(thread["thread_id"], run["run_id"]))["status"] == "success"
    with pg() as conn:
        status = conn.execute(
            "select ocr_status, recognized_text from public.ink_layers where surface_id = %s", (page,)
        ).fetchone()
        health = conn.execute(
            "select (detail->>'deferred')::int from public.agent_health where run_id = %s", (run["run_id"],)
        ).fetchone()
    assert status == ("deferred", None)
    assert health == (1,)
