"""Integration: the `tutor` graph on the real Aegra + Naadi process and local Postgres, with the scripted fake LLM
(tests/e2e_fake_llm.py) standing in for OpenRouter: streaming tokens/blocks/citations, the confirm interrupt
writing (or not) a note, the thread list, thread isolation between users, deletion, and retention: a thread older
than the checkpoint TTL still loads after a sweep.

    apps/api/tests/db/stack.sh up && DATABASE_URL=... uv run pytest tests/agent/test_tutor_host.py -m db
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from ..db.rls import database_url, require_local
from .conftest import client, expect_404, pg
from .host_server import API_DIR, Host, free_port, running_host

pytestmark = [pytest.mark.integration, pytest.mark.db]

STREAM = ["messages-tuple", "custom", "updates", "values"]


@pytest.fixture(scope="module")
def fake_llm() -> Iterator[str]:
    port = free_port()
    proc = subprocess.Popen(  # noqa: S603 - fixed argv
        [sys.executable, str(API_DIR / "tests" / "e2e_fake_llm.py"), "--port", str(port)], cwd=API_DIR
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        yield f"{url}/api/v1"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="module")
def host(fake_llm: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Host]:
    url = database_url()
    require_local(url)
    # The fake gets its own daily cap (as in playwright.config.ts): the shared local database also counts e2e turns.
    extra = {
        "OPENROUTER_API_KEY": "e2e-fake",
        "NAADI_OPENROUTER_BASE_URL": fake_llm,
        "NVIDIA_API_KEY": "",
        "OPENROUTER_DAILY_CAP": "100000",
    }
    with running_host(url, tmp_path_factory.mktemp("tutor-host"), extra_env=extra) as h:
        yield h


async def turn(c: Any, tid: str, text: str, page: dict[str, Any] | None = None) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    async for ev in c.runs.stream(
        tid,
        "tutor",
        input={"messages": [{"type": "human", "content": text}], "page_context": page or {"type": "page"}},
        stream_mode=STREAM,
    ):
        events.append((ev.event, ev.data))
    return events


async def resume(c: Any, tid: str, decision: dict[str, Any]) -> list[tuple[str, Any]]:
    return [
        (ev.event, ev.data)
        async for ev in c.runs.stream(tid, "tutor", command={"resume": decision}, stream_mode=STREAM)
    ]


def rest(h: Host, tok: str) -> httpx.Client:
    return httpx.Client(base_url=h.url, headers={"authorization": f"Bearer {tok}"}, timeout=20)


async def test_a_turn_streams_tokens_tool_status_and_records_the_thread(host: Host) -> None:
    a = client(host, host.student_token)
    tid = (await a.threads.create())["thread_id"]
    events = await turn(a, tid, "e2e:answer what is distal RTA?")
    kinds = {e for e, _ in events}
    assert "error" not in kinds, events[-3:]
    tokens = [d for e, d in events if e.startswith("messages")]
    assert tokens, "tokens streamed"
    customs = [d for e, d in events if e == "custom"]
    assert {"type": "tool", "tool": "search_knowledge", "status": "running"} in customs, [
        d for e, d in events if e != "messages"
    ][-6:]
    assert any(c.get("type") == "block" and c["block"]["data"] == {"kind": "disclaimer"} for c in customs)
    state = await a.threads.get_state(tid)
    final = state["values"]["messages"][-1]
    assert final["type"] == "ai" and "[1]" in final["content"]
    with rest(host, host.student_token) as r:
        items = r.get("/api/assistant/threads").json()["items"]
    mine = [t for t in items if t["aegra_thread_id"] == tid]
    assert mine and mine[0]["title"] == "e2e:answer what is distal RTA?"


async def test_save_note_waits_for_the_confirm(host: Host) -> None:
    a = client(host, host.student_token)

    def notes() -> int:
        with pg() as conn:
            row = conn.execute("select count(*) from public.notes where user_id = %s", (host.student_id,)).fetchone()
        return int(row[0]) if row else 0

    before = notes()
    tid = (await a.threads.create())["thread_id"]
    await turn(a, tid, "e2e:save this formula")
    state = await a.threads.get_state(tid)
    interrupts = [i for t in state.get("tasks", []) for i in t.get("interrupts", [])]
    assert interrupts and interrupts[0]["value"]["tool"] == "save_note"
    assert interrupts[0]["value"]["preview"]["kind"] == "note"
    assert notes() == before, "nothing is written before the student confirms"
    await resume(a, tid, {"decision": "cancel"})
    assert notes() == before
    tid2 = (await a.threads.create())["thread_id"]
    await turn(a, tid2, "e2e:save this formula")
    await resume(a, tid2, {"decision": "accept"})
    assert notes() == before + 1
    final = (await a.threads.get_state(tid2))["values"]["messages"][-1]
    assert final["content"].startswith("Saved")


async def test_user_b_cannot_read_stream_resume_cancel_or_delete_user_a_threads(host: Host) -> None:
    a = client(host, host.student_token)
    b = client(host, host.other_token)
    tid = (await a.threads.create())["thread_id"]
    await turn(a, tid, "e2e:save private")  # left waiting on its confirm
    runs = await a.runs.list(tid)
    run_id = runs[0]["run_id"]
    calls = {
        "get": lambda: b.threads.get(tid),
        "state": lambda: b.threads.get_state(tid),
        "history": lambda: b.threads.get_history(tid),
        "resume": lambda: b.runs.create(tid, "tutor", command={"resume": {"decision": "accept"}}),
        "new_run": lambda: b.runs.create(tid, "tutor", input={"messages": [{"type": "human", "content": "hi"}]}),
        "cancel": lambda: b.runs.cancel(tid, run_id),
        "join": lambda: b.runs.join(tid, run_id),
    }
    failed = []
    for name, call in calls.items():
        try:
            await expect_404(call)
        except pytest.fail.Exception:
            failed.append(name)
    assert failed == []

    async def stream_other() -> None:
        async for _ in b.runs.join_stream(tid, run_id):
            pass

    await expect_404(stream_other)
    with rest(host, host.student_token) as ra:
        row = next(t for t in ra.get("/api/assistant/threads").json()["items"] if t["aegra_thread_id"] == tid)
    with rest(host, host.other_token) as rb:
        assert all(t["aegra_thread_id"] != tid for t in rb.get("/api/assistant/threads").json()["items"])
        assert rb.delete(f"/api/assistant/threads/{row['id']}").status_code == 404
        assert rb.patch(f"/api/assistant/threads/{row['id']}", json={"title": "pwn"}).status_code == 404
    # A can still resume its own run: nothing B did changed it.
    await resume(a, tid, {"decision": "cancel"})


async def test_rename_search_and_delete_a_thread(host: Host) -> None:
    a = client(host, host.student_token)
    tid = (await a.threads.create())["thread_id"]
    await turn(a, tid, "e2e:other something about murmurs")
    with rest(host, host.student_token) as r:
        row = next(t for t in r.get("/api/assistant/threads").json()["items"] if t["aegra_thread_id"] == tid)
        renamed = r.patch(f"/api/assistant/threads/{row['id']}", json={"title": "  Cardiac   murmurs "}).json()
        assert renamed["title"] == "Cardiac murmurs"
        assert [t["id"] for t in r.get("/api/assistant/threads", params={"q": "murmur"}).json()["items"]] == [row["id"]]
        assert r.delete(f"/api/assistant/threads/{row['id']}").status_code == 204
        assert all(t["id"] != row["id"] for t in r.get("/api/assistant/threads").json()["items"])
    await expect_404(lambda: a.threads.get(tid))
    with pg() as conn:
        left = conn.execute("select count(*) from public.checkpoints where thread_id = %s", (tid,)).fetchone()
    assert left == (0,)


async def test_a_thread_older_than_the_ttl_still_loads_after_a_sweep(host: Host) -> None:
    a = client(host, host.student_token)
    tid = (await a.threads.create())["thread_id"]
    await turn(a, tid, "e2e:other first question")
    await turn(a, tid, "e2e:other second question")
    with pg() as conn:
        conn.execute(
            "update public.thread_ttl set expires_at = now() - interval '1 second' where thread_id = %s", (tid,)
        )

    def checkpoints() -> int:
        with pg() as conn:
            row = conn.execute("select count(*) from public.checkpoints where thread_id = %s", (tid,)).fetchone()
        return int(row[0]) if row else 0

    for _ in range(60):
        if checkpoints() <= 1:
            break
        await asyncio.sleep(1)
    assert checkpoints() == 1
    msgs = (await a.threads.get_state(tid))["values"]["messages"]
    humans = [m["content"] for m in msgs if m["type"] == "human"]
    assert humans == ["e2e:other first question", "e2e:other second question"], "the whole history reopens"
    events = await turn(a, tid, "e2e:other third question")
    assert "error" not in {e for e, _ in events}


async def test_store_is_server_only(host: Host) -> None:
    a = client(host, host.student_token)
    with pytest.raises(httpx.HTTPStatusError):
        await a.store.put_item(["memories", str(host.student_id)], "k", {"content": "forged"})


def test_memories_endpoint_lists_nothing_for_a_new_student(host: Host) -> None:
    with rest(host, host.other_token) as r:
        body = r.get("/api/assistant/memories").json()
    assert body == {"items": [], "suggestions": [], "max_items": 100}
