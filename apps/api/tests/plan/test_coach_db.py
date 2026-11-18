"""The coach pass against a real database: facts from the student's own rows, ≤ 3 validated suggestions per
student per day, rules when the (mocked) model is spent, idempotent re-runs, and nobody else's numbers."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from pydantic import SecretStr

from naadi_api.coach.run import coach_pass
from naadi_api.config_api.schemas import CoachRules
from naadi_api.db import Database
from naadi_api.llm_budget import BudgetExhausted
from tests.auth.jwt_helpers import SigningKey

from .conftest import onboard, today_local

pytestmark = [pytest.mark.integration, pytest.mark.db]

RULES = CoachRules(enabled=True, max_suggestions=3, max_users_per_run=10_000)


class FakeGateway:
    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.calls: list[Any] = []
        self.runs: dict[Any, Any] = {}

    async def ainvoke(self, role: str, messages: Any, ctx: Any, **_: Any) -> Any:
        self.calls.append((role, ctx.user_id, messages[-1].content))
        self.runs[ctx.user_id] = ctx.run_id
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _rows(pg: psycopg.Connection[Any], uid: uuid.UUID) -> list[tuple[Any, ...]]:
    return pg.execute(
        "select kind, source, title, facts, status from public.coach_suggestions where user_id = %s"
        " order by created_at",
        (uid,),
    ).fetchall()


async def test_pass_writes_validated_llm_rows_for_this_student(
    url: str,
    client: TestClient,
    key: SigningKey,
    users: tuple[uuid.UUID, uuid.UUID],
    pg: psycopg.Connection[Any],
    practice: dict[str, Any],
) -> None:
    a, b = users
    assert onboard(client, key, a).status_code == 200
    reply = AIMessage(
        content=json.dumps(
            {
                "suggestions": [
                    {"kind": "catch_up_block", "title": "Catch up", "body": "4 of 9 right lately (44%): try 20 more."},
                    {"kind": "review_misses", "title": "Misses", "body": "You missed 77 questions."},
                ]
            }
        )
    )
    gw = FakeGateway(reply)
    db = Database(SecretStr(url), max_size=2)
    try:
        stats = await coach_pass(db, gw, RULES)
    finally:
        await db.close()
    mine = [c for c in gw.calls if c[1] == a]
    assert len(mine) == 1 and mine[0][0] == "structured"
    payload = json.loads(mine[0][2])
    assert "Test Medical College" not in mine[0][2]  # facts are numbers and taxonomy labels only
    weak = next(c for c in payload["candidates"] if c["kind"] == "catch_up_block")
    assert (weak["facts"]["attempts"], weak["facts"]["correct"], weak["facts"]["percent"]) == (9, 4, 44)
    rows = _rows(pg, a)
    assert [(r[0], r[1]) for r in rows] == [("catch_up_block", "llm")]  # the invented 77 was dropped
    assert rows[0][3]["questions"] == 20
    assert stats.dropped >= 1 and stats.rows >= 1
    assert _rows(pg, b) == []  # B has no plan: not coached

    # Re-run: tonight's unanswered rows are replaced, never added to; an accepted one is kept.
    pg.execute("update public.coach_suggestions set status = 'accepted' where user_id = %s", (a,))
    db = Database(SecretStr(url), max_size=2)
    try:
        await coach_pass(db, FakeGateway(BudgetExhausted("daily_cap", dt.datetime.now(dt.UTC), purpose="job")), RULES)
        await coach_pass(db, FakeGateway(BudgetExhausted("daily_cap", dt.datetime.now(dt.UTC), purpose="job")), RULES)
    finally:
        await db.close()
    rows = _rows(pg, a)
    assert len(rows) <= 3
    assert sum(1 for r in rows if r[4] == "accepted") == 1
    assert all(r[1] == "rules" and r[2] is None for r in rows if r[4] == "new")
    assert {r[0] for r in rows if r[4] == "new"} <= {
        "review_misses",
        "cards_catchup",
        "reread_section",
        "catch_up_block",
    }
    assert pg.execute(
        "select count(*) from public.coach_suggestions where user_id = %s and for_date = %s", (a, today_local())
    ).fetchone() == (len(rows),)


async def test_disabled_coach_writes_nothing(url: str) -> None:
    db = Database(SecretStr(url), max_size=2)
    try:
        stats = await coach_pass(db, None, RULES.model_copy(update={"enabled": False}))
    finally:
        await db.close()
    assert stats.students == 0 and stats.reasons == {"disabled": 1}


def _local_today(pg: psycopg.Connection[Any], uid: uuid.UUID) -> dt.date:
    from zoneinfo import ZoneInfo

    row = pg.execute("select coalesce(timezone, 'UTC') from public.profiles where user_id = %s", (uid,)).fetchone()
    assert row is not None
    return dt.datetime.now(dt.UTC).astimezone(ZoneInfo(row[0])).date()


async def _pass(url: str, gw: Any, rules: CoachRules = RULES) -> Any:
    db = Database(SecretStr(url), max_size=2)
    try:
        return await coach_pass(db, gw, rules)
    finally:
        await db.close()


async def test_one_model_attempt_per_student_per_night_across_runs(
    url: str,
    client: TestClient,
    key: SigningKey,
    users: tuple[uuid.UUID, uuid.UUID],
    pg: psycopg.Connection[Any],
    practice: dict[str, Any],
) -> None:
    a, _ = users
    assert onboard(client, key, a).status_code == 200
    run_id = f"coach:{_local_today(pg, a).isoformat()}"
    reply = AIMessage(
        content=json.dumps({"suggestions": [{"kind": "catch_up_block", "title": "Catch up", "body": "Try 20 more."}]})
    )
    first = FakeGateway(reply)
    await _pass(url, first, RULES)
    mine = [c for c in first.calls if c[1] == a]
    assert len(mine) == 1 and first.runs[a] == run_id  # every call tonight carries the night's attempt key
    # The real gateway reserves an llm_usage row per attempt before the request; the fake does not, so add it.
    pg.execute(
        "insert into public.llm_usage (user_id, provider, purpose, role, run_id) values (%s, 'openrouter', 'job',"
        " 'structured', %s)",
        (a, run_id),
    )
    before = pg.execute(
        "select id, source, title from public.coach_suggestions where user_id = %s order by id", (a,)
    ).fetchall()
    assert [r[1] for r in before] == ["llm"]
    # A second run the same night: no model call, tonight's worded rows stand untouched.
    second = FakeGateway(reply)
    stats = await _pass(url, second, RULES)
    assert [c for c in second.calls if c[1] == a] == []
    assert stats.reasons.get("already_coached", 0) >= 1
    assert (
        pg.execute(
            "select id, source, title from public.coach_suggestions where user_id = %s order by id", (a,)
        ).fetchall()
        == before
    )
    # The model was tried (llm_usage) but nothing usable came back and the student is not claimed for tonight
    # (as for a pass that crashed after the call): the next run words rules, still without a call.
    pg.execute("delete from public.coach_suggestions where user_id = %s", (a,))
    pg.execute("update public.study_plans set last_coached_at = null where user_id = %s", (a,))
    third = FakeGateway(reply)
    stats = await _pass(url, third, RULES)
    assert [c for c in third.calls if c[1] == a] == []
    assert stats.reasons.get("llm_already_tried", 0) >= 1
    assert {r[1] for r in _rows(pg, a)} == {"rules"}
    pg.execute("delete from public.llm_usage where user_id = %s", (a,))


async def test_the_least_recently_coached_student_goes_first(
    url: str, client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID], pg: psycopg.Connection[Any]
) -> None:
    a, b = users
    assert onboard(client, key, a).status_code == 200
    assert onboard(client, key, b).status_code == 200
    # Everyone else (other tests' students) was coached just now; A an hour ago; B never.
    pg.execute("update public.study_plans set last_coached_at = now() where active and user_id <> all(%s)", ([a, b],))
    pg.execute("update public.study_plans set last_coached_at = now() - interval '1 hour' where user_id = %s", (a,))
    one = RULES.model_copy(update={"max_users_per_run": 1})

    def coached() -> dict[uuid.UUID, Any]:
        return dict(
            pg.execute(
                "select user_id, last_coached_at from public.study_plans where active and user_id = any(%s)", ([a, b],)
            ).fetchall()
        )

    start = coached()
    assert start[b] is None
    await _pass(url, None, one)
    after = coached()
    assert after[b] is not None and after[a] == start[a]  # B, never coached, went first; A waited
    await _pass(url, None, one)
    assert coached()[a] > start[a]  # then A, now the least recently coached


class GatedGateway(FakeGateway):
    """Holds the call for `who` until released, so a second pass can run while the first is inside the model."""

    def __init__(self, reply: Any, who: uuid.UUID) -> None:
        super().__init__(reply)
        self.who = who
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def ainvoke(self, role: str, messages: Any, ctx: Any, **kw: Any) -> Any:
        if ctx.user_id == self.who:
            self.entered.set()
            await asyncio.wait_for(self.release.wait(), 30)
        return await super().ainvoke(role, messages, ctx, **kw)


async def test_overlapping_passes_call_the_model_once_per_student(
    url: str,
    client: TestClient,
    key: SigningKey,
    users: tuple[uuid.UUID, uuid.UUID],
    pg: psycopg.Connection[Any],
    practice: dict[str, Any],
) -> None:
    """R2: the advisory lock on coach:<user>:<date> covers the check, the call and the write, so a pass that
    overlaps another (before the first one's llm_usage row could be seen) skips the student instead of calling."""
    a, _ = users
    assert onboard(client, key, a).status_code == 200
    reply = AIMessage(
        content=json.dumps({"suggestions": [{"kind": "catch_up_block", "title": "Catch up", "body": "Try 20 more."}]})
    )
    first, second = GatedGateway(reply, a), FakeGateway(reply)
    running = asyncio.create_task(_pass(url, first, RULES))
    await asyncio.wait_for(first.entered.wait(), 30)
    stats = await _pass(url, second, RULES)  # runs while the first pass is inside A's model call
    first.release.set()
    await running
    assert [c for c in second.calls if c[1] == a] == []
    assert stats.reasons.get("claimed", 0) + stats.reasons.get("locked", 0) >= 1
    assert len([c for c in first.calls if c[1] == a]) == 1
    assert [(r[0], r[1]) for r in _rows(pg, a)] == [("catch_up_block", "llm")]


async def test_no_lock_or_transaction_is_held_while_the_model_answers(
    url: str,
    client: TestClient,
    key: SigningKey,
    users: tuple[uuid.UUID, uuid.UUID],
    pg: psycopg.Connection[Any],
    practice: dict[str, Any],
) -> None:
    """The claim commits before the call and the rows are written in a second short transaction: while A's model
    call is in flight, the advisory lock is free, the claim is visible, and no coach transaction is open."""
    a, _ = users
    assert onboard(client, key, a).status_code == 200
    today = _local_today(pg, a)
    reply = AIMessage(
        content=json.dumps({"suggestions": [{"kind": "catch_up_block", "title": "Catch up", "body": "Try 20 more."}]})
    )
    gw = GatedGateway(reply, a)
    running = asyncio.create_task(_pass(url, gw, RULES))
    try:
        await asyncio.wait_for(gw.entered.wait(), 30)
        with pg.transaction(force_rollback=True):
            free = pg.execute(
                "select pg_try_advisory_xact_lock(hashtextextended(%s, 0))", (f"coach:{a}:{today.isoformat()}",)
            ).fetchone()
        assert free == (True,)
        claimed = pg.execute(
            "select last_coached_at is not null from public.study_plans where user_id = %s and active", (a,)
        ).fetchone()
        assert claimed == (True,)
        idle = pg.execute(
            "select count(*) from pg_stat_activity where state like 'idle in transaction%%' and pid <> pg_backend_pid()"
            " and datname = current_database()"
        ).fetchone()
        assert idle == (0,)
        assert _rows(pg, a) == []  # nothing written until the model answers
    finally:
        gw.release.set()
        await running
    assert [(r[0], r[1]) for r in _rows(pg, a)] == [("catch_up_block", "llm")]
