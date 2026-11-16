"""The coach pass against a real database: facts from the student's own rows, ≤ 3 validated suggestions per
student per day, rules when the (mocked) model is spent, idempotent re-runs, and nobody else's numbers."""

from __future__ import annotations

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

    async def ainvoke(self, role: str, messages: Any, ctx: Any, **_: Any) -> Any:
        self.calls.append((role, ctx.user_id, messages[-1].content))
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
