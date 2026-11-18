"""Segment 16: the `planner` graph in the real Aegra host: a student's rebalance proposes, waits on an interrupt and
changes the plan only after the confirm; a decline leaves it as it was. No model is called."""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from typing import Any

import httpx
import pytest

from tests.plan.helpers import template

from .conftest import client, pg
from .host_server import Host

pytestmark = [pytest.mark.integration, pytest.mark.db]

SHIFT = 1000


def seed_template() -> None:
    with pg() as conn:
        conn.execute(
            "insert into public.sources (id, name, license) values ('naadi-original-v1', 'Naadi original',"
            " 'proprietary-naadi') on conflict (id) do nothing"
        )
        conn.execute(
            "insert into public.plan_templates (id, exam, title, days) values ('step1-60d', 'step1',"
            " 'Step 1 in 60 days', 60) on conflict (id) do nothing"
        )
        for t in template():
            conn.execute(
                "insert into public.plan_template_days (template_id, day, phase, chapter_ref, chapter_ids, topic,"
                " chapter_questions, qbank, self_assessment, admin_task, extra_resource, target_hours)"
                " values ('step1-60d', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict do nothing",
                (
                    t.day,
                    t.phase,
                    t.chapter_ref,
                    list(t.chapter_ids),
                    t.topic,
                    t.chapter_questions,
                    t.qbank,
                    t.self_assessment,
                    t.admin_task,
                    t.extra_resource,
                    t.target_hours,
                ),
            )


def onboard_with_missed_days(host: Host) -> None:
    """The student's plan starts today and 60 days long; the plan then moves 6 days into the past."""
    seed_template()
    today = dt.datetime.now(dt.UTC).date()
    body = {
        "exam": "step1",
        "exam_date": (today + dt.timedelta(days=60)).isoformat(),
        "daily_hours": 10,
        "study_days": [1, 2, 3, 4, 5, 6, 7],
        "materials": [],
        "timezone": "UTC",
    }
    r = httpx.put(
        f"{host.url}/api/onboarding", json=body, headers={"authorization": f"Bearer {host.student_token}"}, timeout=30
    )
    assert r.status_code == 200, r.text
    with pg() as conn:
        for delta in (-SHIFT, SHIFT - 6):
            conn.execute(
                "update public.study_plan_days set date = date + %s where user_id = %s", (delta, host.student_id)
            )
            conn.execute("update public.plan_items set day = day + %s where user_id = %s", (delta, host.student_id))


async def interrupt_of(c: Any, thread_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = await c.threads.get_state(thread_id)
        for task in state["tasks"]:
            for it in task.get("interrupts") or []:
                return it["value"]  # type: ignore[no-any-return]
        await asyncio.sleep(0.5)
    pytest.fail("the planner run never reached its confirm")


def plan_days() -> list[tuple[Any, ...]]:
    with pg() as conn:
        return conn.execute("select id, day, status, moved_from from public.plan_items order by id").fetchall()


async def test_a_rebalance_waits_for_the_confirm_and_applies_only_on_accept(host: Host) -> None:
    onboard_with_missed_days(host)
    s = client(host, host.student_token)
    before = plan_days()

    async with httpx.AsyncClient(timeout=30) as http:
        started = await http.post(
            f"{host.url}/api/plan/rebalance", headers={"authorization": f"Bearer {host.student_token}"}
        )
    assert started.status_code == 202, started.text
    thread_id = started.json()["thread_id"]
    waiting = await interrupt_of(s, thread_id)
    assert waiting["moves"] > 0 and waiting["fingerprint"]
    assert plan_days() == before, "nothing may change before the student confirms"
    with pg() as conn:
        row = conn.execute(
            "select status, thread_id, trigger from public.plan_rebalances where user_id = %s", (host.student_id,)
        ).fetchone()
    assert row == ("proposed", thread_id, "student")

    # another student cannot see or resume this thread
    other = client(host, host.other_token)
    with pytest.raises(Exception):  # noqa: B017, PT011 - the SDK raises its own not-found error
        await other.runs.wait(thread_id, "planner", command={"resume": {"decision": "accept"}})
    assert plan_days() == before

    done = await s.runs.wait(thread_id, "planner", command={"resume": {"decision": "accept"}})
    assert done["outcome"] == "applied" and done["detail"]["moved"] == waiting["moves"]
    before_by_id = {r[0]: r for r in before}
    changed = [r for r in plan_days() if r[1] != before_by_id[r[0]][1] or r[2] != before_by_id[r[0]][2]]
    assert len(changed) >= waiting["moves"]
    # every moved item remembers where it came from
    assert all(r[3] == before_by_id[r[0]][1] for r in changed if r[1] != before_by_id[r[0]][1])
    with pg() as conn:
        assert conn.execute(
            "select status, applied_at is not null from public.plan_rebalances where user_id = %s and thread_id = %s",
            (host.student_id, thread_id),
        ).fetchone() == ("accepted", True)


async def test_declining_leaves_the_plan_as_it_was(host: Host) -> None:
    with pg() as conn:
        # three open items back in the past again, so there is something to propose
        conn.execute(
            "update public.plan_items set day = day - 8, status = 'planned', moved_from = null, rebalance_id = null"
            " where id in (select id from public.plan_items where user_id = %s and kind = 'read' and day > current_date"
            " order by day, sort limit 3)",
            (host.student_id,),
        )
    s = client(host, host.student_token)
    before = plan_days()
    thread = await s.threads.create()
    await s.runs.create(thread["thread_id"], "planner", input={"trigger": "student"})
    waiting = await interrupt_of(s, thread["thread_id"])
    assert waiting["moves"] > 0
    done = await s.runs.wait(thread["thread_id"], "planner", command={"resume": {"decision": "decline"}})
    assert done["outcome"] == "declined"
    assert plan_days() == before
    with pg() as conn:
        assert conn.execute(
            "select status from public.plan_rebalances where user_id = %s and thread_id = %s",
            (host.student_id, thread["thread_id"]),
        ).fetchone() == ("declined",)


async def test_nothing_missed_means_nothing_to_confirm(host: Host) -> None:
    with pg() as conn:
        conn.execute(
            "update public.plan_items set status = 'done' where user_id = %s and day < current_date", (host.student_id,)
        )
    s = client(host, host.student_token)
    thread = await s.threads.create()
    run = await s.runs.create(thread["thread_id"], "planner", input={"trigger": "student"})
    await s.runs.join(thread["thread_id"], run["run_id"])
    state = await s.threads.get_state(thread["thread_id"])
    assert state["values"]["outcome"] == "nothing_to_do" and not any(t.get("interrupts") for t in state["tasks"])
