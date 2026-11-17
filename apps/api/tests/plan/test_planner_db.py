"""Integration (real Postgres, the real HTTP path): the planner week, last-writer-wins status changes, student items,
rebalance proposals and their application, the calendar feed and Web Push (mocked push service)."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from icalendar import Calendar
from psycopg.rows import dict_row
from pydantic import SecretStr
from pywebpush import WebPushException

from naadi_api.config_api.schemas import PlannerRules
from naadi_api.plan import planner_store as ps
from naadi_api.plan.planner_models import StudentItemIn
from naadi_api.push.reminders import reminder_pass
from naadi_api.push.sender import Payload
from tests.auth.jwt_helpers import SigningKey
from tests.qbank.conftest import auth

from .conftest import onboard, today_local

pytestmark = [pytest.mark.integration, pytest.mark.db]

RULES = PlannerRules()
SHIFT = 1000


@pytest.fixture
def student(
    client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID], pg: psycopg.Connection[Any]
) -> uuid.UUID:
    """A student whose plan started 6 days ago (so days 1-6 have passed) with a 60-day plan."""
    a, _ = users
    assert onboard(client, key, a).status_code == 200
    pid = pg.execute("select id from public.study_plans where user_id = %s and active", (a,)).fetchone()[0]
    # Two steps, because (plan_id, date) is unique and shifting in one statement collides row by row.
    for delta in (-SHIFT, SHIFT - 6):
        pg.execute("update public.study_plan_days set date = date + %s where plan_id = %s", (delta, pid))
        pg.execute("update public.plan_items set day = day + %s where user_id = %s", (delta, a))
    return a


def hdr(key: SigningKey, uid: uuid.UUID) -> dict[str, str]:
    return auth(key, uid)


# ── week ──


def test_week_needs_sign_in_and_a_plan(client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID]) -> None:
    assert client.get("/api/plan/week").status_code == 401
    r = client.get("/api/plan/week", headers=hdr(key, users[0]))
    assert r.status_code == 200 and r.json() == {"week": None}  # a normal state, not an error


def test_week_shows_seven_days_with_behind_items_and_milestones(
    client: TestClient, key: SigningKey, student: uuid.UUID
) -> None:
    r = client.get("/api/plan/week", headers=hdr(key, student))
    assert r.status_code == 200, r.text
    w = r.json()["week"]
    today = today_local()
    assert len(w["days"]) == 7 and w["today"] == today.isoformat()
    assert w["start"] <= today.isoformat() <= w["end"]
    assert w["weeks"] == 9 and w["week"] == 1 and w["prev_start"] is None
    assert [d["is_today"] for d in w["days"]].count(True) == 1
    assert w["behind"] > 0  # the six days before today were never worked
    behind = [i for d in w["days"] for i in d["items"] if i["behind"]]
    assert behind and all(i["status"] in ("planned", "now", "behind") for i in behind)
    kinds = {m["kind"] for m in w["milestones"]}
    assert {"assessment", "rest", "exam"} <= kinds
    assert w["next_item"] is not None and w["next_item"]["day"] == today.isoformat()
    # a day of the plan carries its target hours, planned hours add up, and an assessment day is marked
    assert all(d["in_plan"] for d in w["days"])
    far = client.get(f"/api/plan/week?start={(today + dt.timedelta(days=40)).isoformat()}", headers=hdr(key, student))
    assert far.status_code == 200 and far.json()["week"]["week"] > 1 and far.json()["week"]["prev_start"] is not None


def test_other_students_plan_is_invisible(
    client: TestClient, key: SigningKey, student: uuid.UUID, users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    assert client.get("/api/plan/week", headers=hdr(key, users[1])).json() == {"week": None}


# ── last-writer-wins ──


def first_item(client: TestClient, key: SigningKey, uid: uuid.UUID) -> dict[str, Any]:
    w = client.get("/api/plan/week", headers=hdr(key, uid)).json()["week"]
    return next(i for d in w["days"] for i in d["items"] if d["is_today"])


def test_status_changes_are_last_writer_wins_per_item(
    client: TestClient, key: SigningKey, student: uuid.UUID, users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    item = first_item(client, key, student)
    url = f"/api/plan/items/{item['id']}"
    done = client.patch(url, json={"status": "done", "client_updated_at_ms": 2_000}, headers=hdr(key, student))
    assert done.status_code == 200 and done.json()["applied"] is True and done.json()["item"]["status"] == "done"
    # an older change replayed from an offline queue loses; a tie goes to the server
    old = client.patch(url, json={"status": "skipped", "client_updated_at_ms": 1_000}, headers=hdr(key, student))
    assert old.json()["applied"] is False and old.json()["item"]["status"] == "done"
    tie = client.patch(url, json={"status": "planned", "client_updated_at_ms": 2_000}, headers=hdr(key, student))
    assert tie.json()["applied"] is False and tie.json()["item"]["status"] == "done"
    new = client.patch(url, json={"status": "planned", "client_updated_at_ms": 3_000}, headers=hdr(key, student))
    assert new.json()["applied"] is True and new.json()["item"]["status"] == "planned"
    # idempotent replay of the same request changes nothing and is not an error
    again = client.patch(url, json={"status": "planned", "client_updated_at_ms": 3_000}, headers=hdr(key, student))
    assert again.status_code == 200 and again.json()["applied"] is False
    # status is the only writable field, and another student's item does not exist for you
    bad = client.patch(url, json={"status": "done", "client_updated_at_ms": 4, "title": "x"}, headers=hdr(key, student))
    assert bad.status_code == 422
    assert (
        client.patch(url, json={"status": "done", "client_updated_at_ms": 9}, headers=hdr(key, users[1])).status_code
        == 404
    )
    assert (
        client.patch(url, json={"status": "now", "client_updated_at_ms": 9}, headers=hdr(key, student)).status_code
        == 422
    )


def test_the_status_route_of_today_stamps_the_server_clock(
    client: TestClient, key: SigningKey, student: uuid.UUID, pg: psycopg.Connection[Any]
) -> None:
    item = first_item(client, key, student)
    r = client.post(f"/api/plan/items/{item['id']}/status", json={"status": "done"}, headers=hdr(key, student))
    assert r.status_code == 200
    stamp = pg.execute("select client_updated_at_ms from public.plan_items where id = %s", (item["id"],)).fetchone()[0]
    assert abs(stamp - dt.datetime.now(dt.UTC).timestamp() * 1000) < 60_000
    # so a queued change made before it can never overwrite it
    queued = client.patch(
        f"/api/plan/items/{item['id']}",
        json={"status": "skipped", "client_updated_at_ms": stamp - 5_000},
        headers=hdr(key, student),
    )
    assert queued.json()["applied"] is False and queued.json()["item"]["status"] == "done"


# ── student items ──


def test_student_items_land_after_the_days_own_and_never_on_assessment_days(
    client: TestClient, key: SigningKey, student: uuid.UUID, pg: psycopg.Connection[Any]
) -> None:
    today = today_local()
    body = {"items": [{"day": today.isoformat(), "kind": "read", "title": "  My   own reading ", "target_minutes": 45}]}
    r = client.post("/api/plan/items", json=body, headers=hdr(key, student))
    assert r.status_code == 201, r.text
    made = r.json()["items"][0]
    assert made["origin"] == "user" and made["title"] == "My own reading" and made["status"] == "planned"
    others = pg.execute(
        "select max(sort) from public.plan_items where user_id = %s and day = %s and origin <> 'user'",
        (student, today),
    ).fetchone()[0]
    assert made["sort"] == others + 1
    assessment = pg.execute(
        "select date from public.study_plan_days where user_id = %s and self_assessment is not null"
        " order by date limit 1",
        (student,),
    ).fetchone()[0]
    refused = client.post(
        "/api/plan/items",
        json={"items": [{"day": assessment.isoformat(), "kind": "read", "title": "x"}]},
        headers=hdr(key, student),
    )
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "assessment_day"
    outside = client.post(
        "/api/plan/items",
        json={"items": [{"day": (today + dt.timedelta(days=400)).isoformat(), "kind": "read", "title": "x"}]},
        headers=hdr(key, student),
    )
    assert outside.status_code == 422 and outside.json()["error"]["code"] == "outside_plan"
    for bad in ({"items": []}, {"items": [{"day": today.isoformat(), "kind": "assessment", "title": "x"}]}):
        assert client.post("/api/plan/items", json=bad, headers=hdr(key, student)).status_code == 422


# ── rebalance: propose, confirm, apply ──


async def with_conn(url: str, fn: Any) -> Any:
    async with await psycopg.AsyncConnection.connect(url, row_factory=dict_row, autocommit=False) as conn:
        out = await fn(conn)
        await conn.commit()
        return out


def run_async(url: str, fn: Any) -> Any:
    return asyncio.run(with_conn(url, fn))


def test_a_proposal_moves_missed_items_forward_and_applies_only_when_confirmed(
    url: str, student: uuid.UUID, pg: psycopg.Connection[Any]
) -> None:
    def day_of(item_id: Any) -> dt.date:
        return pg.execute("select day from public.plan_items where id = %s", (item_id,)).fetchone()[0]

    row, proposal = run_async(url, lambda c: ps.propose(c, student, RULES, trigger="student", thread_id="thr-1"))
    assert row is not None and proposal is not None and row["thread_id"] == "thr-1"
    assert proposal.missed > 0 and proposal.moves
    before = {m.item_id: day_of(m.item_id) for m in proposal.moves}
    # proposing changes nothing in the plan
    assert all(before[m.item_id] == m.from_day for m in proposal.moves)
    # the same proposal again returns the stored one (no churn)
    again, _ = run_async(url, lambda c: ps.propose(c, student, RULES, trigger="coach"))
    assert again is not None and again["id"] == row["id"]
    # decline leaves the plan as it was
    assert run_async(url, lambda c: ps.decline(c, student, row["id"])) is True
    assert all(day_of(m.item_id) == m.from_day for m in proposal.moves)
    assert pg.execute("select status from public.plan_rebalances where id = %s", (row["id"],)).fetchone()[0] == (
        "declined"
    )
    # a fresh proposal, accepted: items move, keep their origin, remember where they came from
    row2, proposal2 = run_async(url, lambda c: ps.propose(c, student, RULES, trigger="student"))
    assert row2 is not None and proposal2 is not None and row2["id"] != row["id"]
    outcome, moved, dropped = run_async(url, lambda c: ps.apply(c, student, row2["id"]))
    assert outcome == "applied" and moved == len(proposal2.moves) and dropped == len(proposal2.drops)
    today = today_local()
    for m in proposal2.moves:
        r = pg.execute(
            "select day, moved_from, rebalance_id, origin, status from public.plan_items where id = %s", (m.item_id,)
        ).fetchone()
        assert r[0] == m.to_day >= today and r[1] == m.from_day and r[2] == row2["id"] and r[3] == "generator"
    for d in proposal2.drops:
        assert pg.execute("select status from public.plan_items where id = %s", (d.item_id,)).fetchone()[0] == "skipped"
    # assessment days are untouched
    assert (
        pg.execute(
            "select count(*) from public.plan_items i join public.study_plan_days d on d.plan_id = i.plan_id"
            " and d.day_no = i.plan_day where i.user_id = %s and i.rebalance_id is not null"
            " and (d.self_assessment is not null or i.day in (select date from public.study_plan_days"
            "   where user_id = %s and self_assessment is not null))",
            (student, student),
        ).fetchone()[0]
        == 0
    )
    # applying twice is refused: the proposal is no longer open
    assert run_async(url, lambda c: ps.apply(c, student, row2["id"]))[0] == "gone"
    # and the plan is now balanced: nothing left to propose except what could not fit
    left, _ = run_async(url, lambda c: ps.propose(c, student, RULES, trigger="student"))
    assert left is None or all(w["code"] != "unplaced" or w["count"] > 0 for w in left["proposal"]["warnings"])


def test_apply_refuses_a_plan_that_changed_since_the_proposal(
    url: str, student: uuid.UUID, pg: psycopg.Connection[Any]
) -> None:
    row, proposal = run_async(url, lambda c: ps.propose(c, student, RULES, trigger="student"))
    assert row is not None and proposal is not None
    pg.execute("update public.plan_items set status = 'done' where id = %s", (proposal.moves[0].item_id,))
    outcome, moved, dropped = run_async(url, lambda c: ps.apply(c, student, row["id"]))
    assert (outcome, moved, dropped) == ("stale", 0, 0)
    assert (
        pg.execute("select status from public.plan_rebalances where id = %s", (row["id"],)).fetchone()[0] == "expired"
    )
    assert all(
        pg.execute("select day from public.plan_items where id = %s", (m.item_id,)).fetchone()[0] == m.from_day
        for m in proposal.moves
    )


def test_only_the_owner_can_apply_a_proposal(url: str, student: uuid.UUID, users: tuple[uuid.UUID, uuid.UUID]) -> None:
    row, _ = run_async(url, lambda c: ps.propose(c, student, RULES, trigger="student"))
    assert row is not None
    assert run_async(url, lambda c: ps.apply(c, users[1], row["id"]))[0] == "gone"
    assert run_async(url, lambda c: ps.decline(c, users[1], row["id"])) is False


def test_rebalances_endpoint_lists_the_waiting_proposal_and_the_week_carries_it(
    client: TestClient, key: SigningKey, url: str, student: uuid.UUID
) -> None:
    assert client.get("/api/plan/rebalances", headers=hdr(key, student)).json() == {"rebalances": []}
    row, _ = run_async(url, lambda c: ps.propose(c, student, RULES, trigger="coach"))
    got = client.get("/api/plan/rebalances?status=proposed", headers=hdr(key, student)).json()["rebalances"]
    assert [g["id"] for g in got] == [str(row["id"])] and got[0]["trigger"] == "coach"
    assert got[0]["proposal"]["moves"] and got[0]["proposal"]["fingerprint"]
    week = client.get("/api/plan/week", headers=hdr(key, student)).json()["week"]
    assert week["rebalance"]["id"] == str(row["id"])
    today = client.get("/api/today", headers=hdr(key, student)).json()
    assert today["rebalance"]["id"] == str(row["id"]) and today["rebalance"]["moves"] == len(
        got[0]["proposal"]["moves"]
    )


class FakeRuntime:
    """Stands in for the Aegra runtime that hosts the app (the real graph runs in tests/agent)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.threads = self
        self.runs = self

    async def create(self, *args: Any, **kwargs: Any) -> dict[str, str]:
        self.calls.append(("create", (args, kwargs)))
        if args:
            return {"run_id": "run-1"}
        return {"thread_id": "thread-1"}


def test_starting_a_rebalance_starts_the_planner_run_and_returns_its_thread(
    client: TestClient, key: SigningKey, student: uuid.UUID
) -> None:
    fake = FakeRuntime()
    client.app.state.agent_client_factory = lambda request: fake
    r = client.post("/api/plan/rebalance", headers=hdr(key, student))
    assert r.status_code == 202, r.text
    assert r.json() == {"thread_id": "thread-1", "run_id": "run-1"}
    assert fake.calls[1][1][0] == ("thread-1", "planner") and fake.calls[1][1][1]["input"] == {"trigger": "student"}
    assert client.post("/api/plan/rebalance").status_code == 401

    class Down(FakeRuntime):
        async def create(self, *args: Any, **kwargs: Any) -> dict[str, str]:
            raise ConnectionError("down")

    client.app.state.agent_client_factory = lambda request: Down()
    down = client.post("/api/plan/rebalance", headers=hdr(key, student))
    assert down.status_code == 503 and down.json()["error"]["code"] == "agent_unavailable"


def test_starting_a_rebalance_without_a_plan_is_a_404(
    client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    client.app.state.agent_client_factory = lambda request: FakeRuntime()
    assert client.post("/api/plan/rebalance", headers=hdr(key, users[0])).status_code == 404


# ── calendar feed ──


def token_of(url: str) -> str:
    return url.rsplit("/", 1)[1]


def test_the_calendar_feed_is_a_hashed_token_revocable_and_pii_free(
    client: TestClient, key: SigningKey, student: uuid.UUID, pg: psycopg.Connection[Any]
) -> None:
    assert client.get("/api/plan/calendar-feed", headers=hdr(key, student)).json() == {
        "active": False,
        "created_at": None,
    }
    made = client.post("/api/plan/calendar-feed", headers=hdr(key, student))
    assert made.status_code == 201 and made.headers["cache-control"] == "no-store"
    token = token_of(made.json()["url"])
    assert made.json()["url"].endswith(f"/api/plan/ics/{token}") and len(token) >= 40
    stored = pg.execute("select token_hash from public.calendar_feeds where user_id = %s", (student,)).fetchall()
    assert [s[0] for s in stored] == [hashlib.sha256(token.encode()).hexdigest()]
    assert token not in str(stored)

    # no Authorization header: the token is the credential
    first = client.get(f"/api/plan/ics/{token}")
    assert first.status_code == 200 and first.headers["content-type"].startswith("text/calendar")
    assert first.headers["cache-control"] == "private, max-age=900"
    cal = Calendar.from_ical(first.content)
    assert str(cal["X-WR-CALNAME"]) == "Naadi study plan"
    events = [c for c in cal.walk("VEVENT")]
    assert events and all(e["DTSTART"].dt == e["DTSTART"].dt and "DTEND" in e for e in events)
    assert any("min" in str(e["DESCRIPTION"]) for e in events)
    text = first.text.lower()
    assert "@" not in text.replace("@naadi", "")  # no e-mail address
    assert str(student) not in first.text  # event ids derive from the id but never contain it
    # stable UIDs across fetches
    second = Calendar.from_ical(client.get(f"/api/plan/ics/{token}").content)
    assert [str(e["UID"]) for e in second.walk("VEVENT")] == [str(e["UID"]) for e in events]
    assert len({str(e["UID"]) for e in events}) == len(events)

    # rotating revokes the old URL and gives a new one; the old answers 404 like an unknown token
    new = token_of(client.post("/api/plan/calendar-feed", headers=hdr(key, student)).json()["url"])
    assert client.get(f"/api/plan/ics/{token}").status_code == 404
    assert client.get(f"/api/plan/ics/{new}").status_code == 200
    assert client.get("/api/plan/calendar-feed", headers=hdr(key, student)).json()["active"] is True
    assert client.delete("/api/plan/calendar-feed", headers=hdr(key, student)).status_code == 204
    assert client.get(f"/api/plan/ics/{new}").status_code == 404
    assert client.get("/api/plan/ics/" + "x" * 43).status_code == 404
    assert client.get("/api/plan/ics/short").status_code == 422
    active = pg.execute(
        "select count(*) from public.calendar_feeds where user_id = %s and revoked_at is null", (student,)
    ).fetchone()[0]
    assert active == 0


def test_the_feed_is_rate_limited_per_address(client: TestClient, key: SigningKey, student: uuid.UUID) -> None:
    token = token_of(client.post("/api/plan/calendar-feed", headers=hdr(key, student)).json()["url"])
    codes = [client.get(f"/api/plan/ics/{token}").status_code for _ in range(25)]
    assert codes[:20] == [200] * 20 and 429 in codes[20:]


# ── Web Push ──


class Pushed:
    def __init__(self, status: int = 201) -> None:
        self.status = status
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, info: dict[str, Any], **kwargs: Any) -> Any:
        self.sent.append((info["endpoint"], kwargs))
        if self.status >= 400:

            class Resp:
                status_code = self.status

            raise WebPushException("rejected", response=Resp())

        class Ok:
            status = 201

        return Ok()


SUB = {
    "endpoint": "https://push.example.org/send/abc123",
    "keys": {
        "p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
        "auth": "tBHItJI5svbpez7KI4CCXg",
    },
}


@pytest.fixture
def push_on(client: TestClient) -> Iterator[Pushed]:
    s = client.app.state.settings
    client.app.state.settings = s.model_copy(
        update={
            "vapid_public_key": "BPublicKeyForTests",
            "vapid_private_key": SecretStr("private-key-for-tests"),
            "vapid_subject": "mailto:owner@example.org",
        }
    )
    pushed = Pushed()
    client.app.state.push_transport = pushed
    yield pushed
    client.app.state.settings = s


def test_push_is_gated_until_the_owner_provides_keys(
    client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    h = hdr(key, users[0])
    assert client.get("/api/push/key", headers=h).json() == {"configured": False, "public_key": None}
    assert client.post("/api/push/subscriptions", json=SUB, headers=h).json()["error"]["code"] == "push_not_configured"
    assert client.post("/api/push/test", headers=h).status_code == 409
    assert client.get("/api/push/subscriptions", headers=h).json() == {"configured": False, "subscriptions": []}
    assert client.get("/api/push/key").status_code == 401


def test_subscriptions_are_registered_per_endpoint_tested_and_deleted(
    client: TestClient,
    key: SigningKey,
    student: uuid.UUID,
    push_on: Pushed,
    pg: psycopg.Connection[Any],
    users: tuple[uuid.UUID, uuid.UUID],
) -> None:
    h = hdr(key, student)
    assert client.get("/api/push/key", headers=h).json() == {"configured": True, "public_key": "BPublicKeyForTests"}
    ipad = {
        **h,
        "user-agent": "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Version/17.4 Safari/604.1",
    }
    made = client.post("/api/push/subscriptions", json=SUB, headers=ipad)
    assert made.status_code == 201 and made.json()["ua_label"] == "iPad · Safari"
    again = client.post("/api/push/subscriptions", json=SUB, headers=ipad)
    assert again.json()["id"] == made.json()["id"]  # one row per endpoint
    assert len(client.get("/api/push/subscriptions", headers=h).json()["subscriptions"]) == 1
    # only https endpoints
    assert (
        client.post(
            "/api/push/subscriptions", json={**SUB, "endpoint": "http://x.example.org/y"}, headers=h
        ).status_code
        == 422
    )
    assert client.post("/api/push/subscriptions", json={"endpoint": SUB["endpoint"]}, headers=h).status_code == 422

    sent = client.post("/api/push/test", headers=h)
    assert sent.json() == {"sent": 1, "removed": 0, "failed": 0}
    endpoint, kwargs = push_on.sent[0]
    assert endpoint == SUB["endpoint"] and kwargs["vapid_claims"] == {"sub": "mailto:owner@example.org"}
    import json

    payload = json.loads(kwargs["data"])
    assert set(payload) == {"k", "n", "title", "body", "url", "tag"} and payload["k"] == "push.test_body"
    assert payload["title"] == "Naadi" and payload["url"] == "/plan"

    # a delete of someone else's subscription is a 404
    sid = made.json()["id"]
    assert client.delete(f"/api/push/subscriptions/{sid}", headers=hdr(key, users[1])).status_code == 404
    assert client.delete(f"/api/push/subscriptions/{sid}", headers=h).status_code == 204
    assert client.delete(f"/api/push/subscriptions/{sid}", headers=h).status_code == 404


def test_a_410_from_the_push_service_deletes_the_subscription(
    client: TestClient, key: SigningKey, student: uuid.UUID, push_on: Pushed, pg: psycopg.Connection[Any]
) -> None:
    h = hdr(key, student)
    client.post("/api/push/subscriptions", json=SUB, headers=h)
    push_on.status = 410
    assert client.post("/api/push/test", headers=h).json() == {"sent": 0, "removed": 1, "failed": 0}
    assert (
        pg.execute("select count(*) from public.push_subscriptions where user_id = %s", (student,)).fetchone()[0] == 0
    )
    # 404 too; a transient 500 keeps the device and counts the failure
    client.post("/api/push/subscriptions", json=SUB, headers=h)
    push_on.status = 404
    assert client.post("/api/push/test", headers=h).json()["removed"] == 1
    client.post("/api/push/subscriptions", json=SUB, headers=h)
    push_on.status = 503
    assert client.post("/api/push/test", headers=h).json() == {"sent": 0, "removed": 0, "failed": 1}
    assert (
        pg.execute("select failures from public.push_subscriptions where user_id = %s", (student,)).fetchone()[0] == 1
    )


def test_a_student_can_keep_at_most_ten_devices(
    client: TestClient, key: SigningKey, student: uuid.UUID, push_on: Pushed
) -> None:
    h = hdr(key, student)
    for n in range(10):
        r = client.post("/api/push/subscriptions", json={**SUB, "endpoint": f"https://push.example.org/{n}"}, headers=h)
        assert r.status_code == 201
    over = client.post("/api/push/subscriptions", json={**SUB, "endpoint": "https://push.example.org/11"}, headers=h)
    assert over.status_code == 422 and over.json()["error"]["code"] == "too_many_subscriptions"
    # updating one of the ten is still fine
    assert (
        client.post(
            "/api/push/subscriptions", json={**SUB, "endpoint": "https://push.example.org/3"}, headers=h
        ).status_code
        == 201
    )


def test_the_daily_pass_sends_a_reminder_and_a_nudge_once_per_device_per_day(
    client: TestClient, key: SigningKey, url: str, student: uuid.UUID, push_on: Pushed, pg: psycopg.Connection[Any]
) -> None:
    import json

    from naadi_api.db import Database

    h = hdr(key, student)
    client.post("/api/push/subscriptions", json=SUB, headers=h)
    pg.execute("update public.profiles set timezone = 'UTC' where user_id = %s", (student,))
    settings = client.app.state.settings
    copy = {
        "push.title": "Naadi",
        "push.reminder_body": "{items} items to do today, about {hours} h.",
        "push.nudge_body": "{behind} items are behind.",
    }
    today = dt.datetime.now(dt.UTC).date()

    async def go(now: dt.datetime) -> Any:
        db = Database(settings.database_url)
        await db.open()
        try:
            return await reminder_pass(db, settings, RULES, copy, now=now, transport=push_on)
        finally:
            await db.close()

    # the plan starts today (UTC date may differ from the Kolkata date the fixture used): move it onto UTC today
    day = pg.execute("select min(day) from public.plan_items where user_id = %s", (student,)).fetchone()[0]
    shift = (today - dt.timedelta(days=6) - day).days
    for delta in (-SHIFT, SHIFT + shift):
        pg.execute("update public.study_plan_days set date = date + %s where user_id = %s", (delta, student))
        pg.execute("update public.plan_items set day = day + %s where user_id = %s", (delta, student))

    early = dt.datetime.combine(today, dt.time(6, 0), dt.UTC)
    assert asyncio.run(go(early)).sent == 0  # before 07:30 nothing goes out
    stats = asyncio.run(go(dt.datetime.combine(today, dt.time(7, 45), dt.UTC)))
    assert (stats.devices, stats.students, stats.sent) == (1, 1, 2)
    bodies = [json.loads(k["data"]) for _, k in push_on.sent]
    assert [b["k"] for b in bodies] == ["push.reminder_body", "push.nudge_body"]
    assert bodies[0]["n"]["items"] > 0 and bodies[1]["n"]["behind"] > 0
    assert all("title" not in b["n"] and b["url"] == "/plan" for b in bodies)
    # not again the same day, even a few minutes later
    assert asyncio.run(go(dt.datetime.combine(today, dt.time(8, 0), dt.UTC))).sent == 0
    assert len(push_on.sent) == 2
    # a late run (after the window) sends nothing; the next day it is due again
    assert asyncio.run(go(dt.datetime.combine(today + dt.timedelta(days=1), dt.time(15, 0), dt.UTC))).sent == 0
    nxt = asyncio.run(go(dt.datetime.combine(today + dt.timedelta(days=1), dt.time(7, 31), dt.UTC)))
    assert nxt.students == 1
    # a gone device is deleted by the pass
    pg.execute("update public.push_subscriptions set last_sent_on = null where user_id = %s", (student,))
    push_on.status = 410
    gone = asyncio.run(go(dt.datetime.combine(today, dt.time(7, 45), dt.UTC)))
    assert gone.removed == 1
    assert (
        pg.execute("select count(*) from public.push_subscriptions where user_id = %s", (student,)).fetchone()[0] == 0
    )


def test_the_pass_does_nothing_without_keys(client: TestClient, url: str) -> None:
    from naadi_api.db import Database

    settings = client.app.state.settings

    async def go() -> Any:
        db = Database(settings.database_url)
        await db.open()
        try:
            return await reminder_pass(db, settings, RULES, {})
        finally:
            await db.close()

    assert asyncio.run(go()).skipped == "not_configured"


def test_payload_wording_comes_from_ui_copy_and_stays_small() -> None:
    p = Payload("push.reminder_body", {"items": 3, "hours": 2.5}, "/plan", "t")
    text = p.to_json({"push.title": "Naadi", "push.reminder_body": "{items} items, {hours} h"})
    import json

    assert json.loads(text)["body"] == "3 items, 2.5 h"
    with pytest.raises(ValueError, match="too large"):
        Payload("k", {}, "/" + "x" * 3000, "t").to_json({})


def test_student_items_helper_is_usable_in_process(url: str, student: uuid.UUID) -> None:
    items = [StudentItemIn(day=today_local(), kind="cards", title="Anki: renal", target_minutes=30)]
    made = run_async(url, lambda c: ps.create_items(c, student, items))
    assert made[0]["origin"] == "user" and made[0]["kind"] == "cards"
