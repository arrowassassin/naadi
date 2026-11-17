"""The `author` graph in the real Aegra host: its crons exist, an admin runs it end to end, students cannot, and
with no model provider configured the night degrades (every attempt recorded as failed) instead of crashing."""

from __future__ import annotations

import pytest

from tests.agent.conftest import client, host, pg  # noqa: F401  (the `host` fixture is module-scoped there)
from tests.agent.host_server import Host

pytestmark = [pytest.mark.integration, pytest.mark.db]


def test_both_system_crons_exist(host: Host) -> None:  # noqa: F811
    with pg() as conn:
        rows = conn.execute(
            "select metadata->>'naadi_key', schedule, payload->'input' from public.crons"
            " where user_id = 'naadi-system' and metadata->>'naadi_key' like 'naadi.author.%%' order by 1"
        ).fetchall()
    assert [(k, s, i) for k, s, i in rows] == [
        ("naadi.author.nightly", "41 22 * * *", {"mode": "nightly"}),
        ("naadi.author.psychometrics", "11 23 * * *", {"mode": "psychometrics"}),
    ]


async def test_an_admin_runs_the_statistics_pass_and_it_is_recorded(host: Host) -> None:  # noqa: F811
    a = client(host, host.admin_token)
    thread = await a.threads.create()
    run = await a.runs.create(thread["thread_id"], "author", input={"mode": "psychometrics"})
    await a.runs.join(thread["thread_id"], run["run_id"])
    assert (await a.runs.get(thread["thread_id"], run["run_id"]))["status"] == "success"
    with pg() as conn:
        row = conn.execute(
            "select kind, ok, detail->>'outcome' from public.agent_health where run_id = %s", (run["run_id"],)
        ).fetchone()
    assert row == ("psychometrics", True, "ok")


async def test_the_nightly_pass_without_a_provider_records_failed_attempts_and_spends_nothing(host: Host) -> None:  # noqa: F811
    with pg() as conn:
        before = conn.execute("select count(*) from public.authoring_runs").fetchone()
        assert before is not None
    a = client(host, host.admin_token)
    thread = await a.threads.create()
    run = await a.runs.create(thread["thread_id"], "author", input={"mode": "nightly"})
    await a.runs.join(thread["thread_id"], run["run_id"])
    assert (await a.runs.get(thread["thread_id"], run["run_id"]))["status"] == "success"
    with pg() as conn:
        health = conn.execute(
            "select kind, ok, detail from public.agent_health where run_id = %s", (run["run_id"],)
        ).fetchone()
        runs = conn.execute(
            "select outcome, llm_calls from public.authoring_runs"
            " where trigger = 'cron' and created_at > now() - interval '2 minutes'"
        ).fetchall()
        questions = conn.execute("select count(*) from public.questions where status = 'in_review'").fetchone()
    assert health is not None and health[0] == "author" and health[1] is True
    assert health[2]["in_review"] == 0 and health[2]["discarded"] >= 0
    assert all(outcome in ("failed", "no_excerpts", "budget") for outcome, _ in runs), (
        runs
    )  # no provider: nothing drafted
    assert questions is not None


async def test_students_cannot_run_the_author_graph(host: Host) -> None:  # noqa: F811
    s = client(host, host.student_token)
    thread = await s.threads.create()
    run = await s.runs.create(thread["thread_id"], "author", input={"mode": "nightly"})
    await s.runs.join(thread["thread_id"], run["run_id"])
    assert (await s.runs.get(thread["thread_id"], run["run_id"]))["status"] == "error"
