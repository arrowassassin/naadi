"""Integration (real Postgres, migrated schema): due lists, the attempt gate through every endpoint, create / edit /
delete, server-scheduled reviews (idempotent, logged, ordered), stats across time zones. The full HTTP path runs:
bearer token -> JWKS -> account check -> user_tx (RLS) or the service role with the RLS-equivalent filter."""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from naadi_api.cards.scheduler import FsrsParams, Memory, review
from naadi_api.config_api.defaults import file_values
from naadi_api.settings import REPO_ROOT
from tests.auth.jwt_helpers import SigningKey
from tests.db.rls import as_user

from .conftest import GATED_SECRET, PRIVATE_SECRET, World, auth, build_client

pytestmark = [pytest.mark.integration, pytest.mark.db]

DEFAULTS = FsrsParams.from_config(file_values(REPO_ROOT / "config")["fsrs"])


def ids(cards: list[dict[str, Any]]) -> set[uuid.UUID]:
    return {uuid.UUID(c["id"]) for c in cards}


def mine(w: World, cards: list[dict[str, Any]]) -> set[uuid.UUID]:
    """Only this world's cards (other tests' shared cards may exist at the same time)."""
    return ids(cards) & (w.visible | w.hidden)


def due(client: TestClient, key: SigningKey, uid: uuid.UUID, **q: Any) -> dict[str, Any]:
    res = client.get("/api/cards/due", params={"limit": 200, **q}, headers=auth(key, uid))
    assert res.status_code == 200, res.text
    out: dict[str, Any] = res.json()
    return out


def rate(client: TestClient, key: SigningKey, uid: uuid.UUID, card: uuid.UUID, rating: int, **body: Any) -> Any:
    payload = {"client_review_id": str(body.pop("rid", uuid.uuid4())), "rating": rating, **body}
    return client.post(f"/api/cards/{card}/review", json=payload, headers=auth(key, uid))


def no_secrets(text: str) -> None:
    assert GATED_SECRET not in text and PRIVATE_SECRET not in text, "a gated or private card leaked"


# ── visibility ──


def test_due_list_applies_the_graded_attempt_gate(client: TestClient, key: SigningKey, world: World) -> None:
    body = due(client, key, world.owner)
    assert mine(world, body["cards"]) == world.visible
    for c in body["cards"]:
        if uuid.UUID(c["id"]) == world.shared_graded:
            assert GATED_SECRET in json.dumps(c["back"])  # visible after the graded attempt, with its answer
            assert c["source"] == {"kind": "question", "id": world.q_graded, "chapter_id": None}
    others = [c for c in body["cards"] if uuid.UUID(c["id"]) not in (world.shared_graded, world.shared_plain)]
    no_secrets(json.dumps(others))
    # the other student: only the shared unlinked card and their own (their graded attempt opens q_untouched's)
    other = due(client, key, world.other)
    assert mine(world, other["cards"]) == {world.shared_plain, world.shared_untouched, world.others}


def test_service_filter_equals_rls_for_graded_attempts(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    """The service-role filter and the flashcards RLS policy agree wherever attempts are graded; flag-only and
    ungraded attempts never open a card through the API (S08's 0010 makes RLS require a graded attempt too)."""
    for uid in (world.owner, world.other):
        api = mine(world, due(client, key, uid)["cards"])
        with as_user(pg, uid) as conn:
            rows = conn.execute("select id from public.flashcards where deck = %s", (f"t{world.tag}",)).fetchall()
        rls = {r[0] for r in rows}
        assert api <= rls
        assert rls - api <= {world.shared_flagged, world.shared_ungraded}


@pytest.mark.parametrize("attr", ["shared_flagged", "shared_ungraded", "shared_untouched", "others"])
def test_hidden_cards_are_unreachable_through_every_endpoint(
    client: TestClient, key: SigningKey, world: World, attr: str
) -> None:
    card = getattr(world, attr)
    h = auth(key, world.owner)
    responses = [
        client.get(f"/api/cards/{card}", headers=h),
        rate(client, key, world.owner, card, 3),
        client.patch(f"/api/cards/{card}", json={"front_text": "x"}, headers=h),
        client.delete(f"/api/cards/{card}", headers=h),
    ]
    for res in responses:
        assert res.status_code == 404, res.text
        assert res.json()["error"]["code"] == "card_not_found"
        no_secrets(res.text)
    stats = client.get("/api/cards/stats", params={"tz": "UTC"}, headers=h)
    assert stats.status_code == 200
    no_secrets(stats.text)


def test_unauthenticated_requests_are_refused(client: TestClient, world: World) -> None:
    for res in (
        client.get("/api/cards/due"),
        client.get("/api/cards/stats"),
        client.post("/api/cards", json={"front_text": "a", "back_text": "b"}),
        client.post(f"/api/cards/{world.own}/review", json={"client_review_id": str(uuid.uuid4()), "rating": 3}),
    ):
        assert res.status_code == 401


# ── create / edit / delete ──


def test_create_from_text(client: TestClient, key: SigningKey, world: World) -> None:
    res = client.post(
        "/api/cards",
        json={"front_text": "Which RTA\ncauses hyperkalemia?", "back_text": "<b>Type 4</b>", "deck": f"t{world.tag}"},
        headers=auth(key, world.owner),
    )
    assert res.status_code == 201, res.text
    c = res.json()
    assert c["editable"] is True and c["origin"] == "user" and c["deck"] == f"t{world.tag}"
    assert c["front"]["content"][0]["content"][1] == {"type": "hardBreak"}
    assert c["back_text"] == "<b>Type 4</b>"  # text, never markup
    assert c["schedule"]["state"] == 0 and c["schedule"]["due"] is None
    assert uuid.UUID(c["id"]) in ids(due(client, key, world.owner)["cards"])
    assert uuid.UUID(c["id"]) not in ids(due(client, key, world.other)["cards"])


@pytest.mark.parametrize(
    ("attr", "status", "code"),
    [
        ("q_graded", 201, None),
        ("q_flagged", 404, "card_question_not_attempted"),
        ("q_ungraded", 404, "card_question_not_attempted"),
        ("q_untouched", 404, "card_question_not_attempted"),
        ("q_draft", 404, "card_question_not_attempted"),
    ],
)
def test_create_from_a_question_needs_a_graded_attempt(
    client: TestClient, key: SigningKey, world: World, attr: str, status: int, code: str | None
) -> None:
    qid = getattr(world, attr)
    res = client.post(
        "/api/cards",
        json={"front_text": "Q", "back_text": "A", "question_id": qid, "deck": f"t{world.tag}"},
        headers=auth(key, world.owner),
    )
    assert res.status_code == status, res.text
    if code:
        assert res.json()["error"]["code"] == code
    else:
        assert res.json()["source"] == {"kind": "question", "id": qid, "chapter_id": None}


def test_create_from_a_section(client: TestClient, key: SigningKey, world: World) -> None:
    h = auth(key, world.owner)
    ok = client.post(
        "/api/cards",
        json={"front_text": "Q", "back_text": "A", "section_id": world.section, "deck": f"t{world.tag}"},
        headers=h,
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["source"] == {"kind": "section", "id": world.section, "chapter_id": world.chapter}
    for section in (world.private_section, "no-such-section"):
        res = client.post("/api/cards", json={"front_text": "Q", "back_text": "A", "section_id": section}, headers=h)
        assert res.status_code == 404, res.text
        assert res.json()["error"]["code"] == "card_section_not_found"


@pytest.mark.parametrize(
    "body",
    [
        {"front_text": "   ", "back_text": "A"},
        {"front_text": "Q", "back_text": ""},
        {"front_text": "Q" * 4001, "back_text": "A"},
        {"front_text": "Q", "back_text": "A", "origin": "seed"},
        {"front_text": "Q", "back_text": "A", "owner_user_id": str(uuid.uuid4())},
    ],
)
def test_create_validates(client: TestClient, key: SigningKey, world: World, body: dict[str, Any]) -> None:
    res = client.post("/api/cards", json=body, headers=auth(key, world.owner))
    assert res.status_code == 422, res.text


def test_edit_and_delete_own_cards_only(client: TestClient, key: SigningKey, world: World) -> None:
    h = auth(key, world.owner)
    rate(client, key, world.owner, world.own, 3)
    res = client.patch(f"/api/cards/{world.own}", json={"front_text": "Edited", "deck": f"t{world.tag}"}, headers=h)
    assert res.status_code == 200, res.text
    assert res.json()["front_text"] == "Edited" and res.json()["back_text"] == "A"
    assert res.json()["schedule"]["reps"] == 1  # editing keeps the schedule
    for shared in (world.shared_plain, world.shared_graded):
        for r in (
            client.patch(f"/api/cards/{shared}", json={"front_text": "x"}, headers=h),
            client.delete(f"/api/cards/{shared}", headers=h),
        ):
            assert r.status_code == 403 and r.json()["error"]["code"] == "card_not_editable"
    assert client.delete(f"/api/cards/{world.own}", headers=h).status_code == 204
    assert client.get(f"/api/cards/{world.own}", headers=h).status_code == 404


# ── review ──


def test_review_schedules_on_the_server_and_logs(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    rid = uuid.uuid4()
    start = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    res = rate(client, key, world.owner, world.own, 3, rid=rid, duration_ms=4200, reviewed_at=start)
    assert res.status_code == 200, res.text
    out = res.json()
    at = datetime.fromisoformat(out["reviewed_at"])
    expected = review(DEFAULTS, str(world.own), Memory(), 3, at).memory
    s = out["schedule"]
    assert out["replayed"] is False and out["scheduled_days"] == 0
    assert (s["state"], s["step"], s["reps"]) == (1, 1, 1)
    assert datetime.fromisoformat(s["due"]) == expected.due == at + timedelta(minutes=10)
    assert s["stability"] == expected.stability and s["difficulty"] == expected.difficulty
    row = pg.execute(
        "select rating, state, stability, difficulty, elapsed_days, last_elapsed_days, scheduled_days, learning_step,"
        " review, duration_ms, next_state, next_step, next_due, reps, lapses from public.card_review_logs"
        " where client_review_id = %s",
        (rid,),
    ).fetchone()
    assert row == (3, 0, None, None, 0, 0, 0, None, at, 4200, 1, 1, expected.due, 1, 0)
    state = pg.execute(
        "select state, step, due, reps from public.card_reviews where user_id = %s and card_id = %s",
        (world.owner, world.own),
    ).fetchone()
    assert state == (1, 1, expected.due, 1)

    # 10 minutes later: Good graduates (scheduled by the server from the stored state)
    later = (at + timedelta(minutes=11)).isoformat()
    res2 = rate(client, key, world.owner, world.own, 3, reviewed_at=later)
    m2 = review(DEFAULTS, str(world.own), expected, 3, at + timedelta(minutes=11)).memory
    assert res2.json()["schedule"]["state"] == 2 == m2.state
    assert datetime.fromisoformat(res2.json()["schedule"]["due"]) == m2.due
    log2 = pg.execute(
        "select state, learning_step, elapsed_days, last_elapsed_days from public.card_review_logs"
        " where user_id = %s and card_id = %s order by review desc limit 1",
        (world.owner, world.own),
    ).fetchone()
    assert log2 == (1, 1, 0, 0)  # the state before this review, as ts-fsrs logs it


def test_review_is_idempotent_on_the_client_review_id(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    rid = uuid.uuid4()
    first = rate(client, key, world.owner, world.own, 4, rid=rid).json()
    again = rate(client, key, world.owner, world.own, 1, rid=rid).json()  # a retry, even with another rating
    assert again["replayed"] is True
    assert {k: v for k, v in again.items() if k != "replayed"} == {k: v for k, v in first.items() if k != "replayed"}
    n = pg.execute("select count(*) from public.card_review_logs where client_review_id = %s", (rid,)).fetchone()
    assert n == (1,)
    reused = rate(client, key, world.owner, world.shared_plain, 3, rid=rid)
    assert reused.status_code == 409 and reused.json()["error"]["code"] == "review_id_reused"


def test_concurrent_retries_apply_once(
    url: str, key: SigningKey, static_dir: Path, world: World, pg: psycopg.Connection[Any]
) -> None:
    rid = uuid.uuid4()
    with (
        build_client(url, key, static_dir) as a,
        build_client(url, key, static_dir) as b,
        ThreadPoolExecutor(4) as pool,
    ):
        results = list(pool.map(lambda c: rate(c, key, world.owner, world.own, 3, rid=rid), [a, b, a, b]))
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert sum(not r.json()["replayed"] for r in results) == 1
    assert pg.execute(
        "select count(*) from public.card_review_logs where client_review_id = %s", (rid,)
    ).fetchone() == (1,)
    assert pg.execute(
        "select reps from public.card_reviews where user_id = %s and card_id = %s", (world.owner, world.own)
    ).fetchone() == (1,)


def test_review_time_rules(client: TestClient, key: SigningKey, world: World) -> None:
    now = datetime.now(UTC)
    old = rate(client, key, world.owner, world.own, 3, reviewed_at=(now - timedelta(days=40)).isoformat())
    assert old.status_code == 422 and old.json()["error"]["code"] == "review_too_old"
    naive = rate(client, key, world.owner, world.own, 3, reviewed_at="2026-11-16T10:00:00")
    assert naive.status_code == 422
    future = rate(client, key, world.owner, world.own, 3, reviewed_at=(now + timedelta(days=3)).isoformat())
    assert future.status_code == 200
    assert datetime.fromisoformat(future.json()["reviewed_at"]) <= datetime.now(UTC)  # clamped to the server clock
    earlier = rate(client, key, world.owner, world.own, 3, reviewed_at=(now - timedelta(hours=2)).isoformat())
    assert earlier.status_code == 409 and earlier.json()["error"]["code"] == "review_out_of_order"
    bad = rate(client, key, world.owner, world.own, 5)
    assert bad.status_code == 422


def test_concurrent_reuse_of_a_review_id_across_cards_is_refused(
    url: str, key: SigningKey, static_dir: Path, world: World, pg: psycopg.Connection[Any]
) -> None:
    """The same client_review_id sent for two cards at once: one is scheduled, the other is told the id is taken
    (never a 500 from the unique index), and only one card changes."""
    for _ in range(5):
        rid = str(uuid.uuid4())
        with (
            build_client(url, key, static_dir) as a,
            build_client(url, key, static_dir) as b,
            ThreadPoolExecutor(4) as pool,
        ):
            calls = [(a, world.own), (b, world.shared_plain), (b, world.own), (a, world.shared_plain)]
            results = list(pool.map(lambda c, r=rid: rate(c[0], key, world.owner, c[1], 3, rid=r), calls))
        # the winner's card: scheduled once, then replayed; the other card: refused both times
        codes = sorted(r.status_code for r in results)
        assert codes == [200, 200, 409, 409], [r.text for r in results]
        refused = [r.json()["error"]["code"] for r in results if r.status_code == 409]
        assert set(refused) == {"review_id_reused"}
        ok = [r.json() for r in results if r.status_code == 200]
        assert len({o["card_id"] for o in ok}) == 1 and sum(not o["replayed"] for o in ok) == 1
        logged = pg.execute(
            "select card_id from public.card_review_logs where client_review_id = %s", (rid,)
        ).fetchall()
        assert logged == [(uuid.UUID(ok[0]["card_id"]),)]


def test_a_review_cannot_predate_its_card(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    now = datetime.now(UTC)
    made = client.post(
        "/api/cards", json={"front_text": "New", "back_text": "Card"}, headers=auth(key, world.owner)
    ).json()
    card = uuid.UUID(made["id"])
    for back in (timedelta(days=20), timedelta(hours=2), timedelta(seconds=30)):
        res = rate(client, key, world.owner, card, 4, reviewed_at=(now - back).isoformat())
        assert res.status_code == 409 and res.json()["error"]["code"] == "review_before_card", res.text
    assert pg.execute("select count(*) from public.card_review_logs where card_id = %s", (card,)).fetchone() == (0,)
    assert pg.execute("select count(*) from public.card_reviews where card_id = %s", (card,)).fetchone() == (0,)
    # from its creation on, a rating is accepted
    assert rate(client, key, world.owner, card, 3).status_code == 200


def test_backdated_reviews_count_when_received(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    """A batch claiming days of past reviews (an old card, so the claims pass every other check) adds to today,
    not to the streak: a claim more than a day old counts at the time the server received it."""
    now = datetime.now(UTC)
    for days in (6, 5, 4, 3, 2):
        res = rate(client, key, world.owner, world.own, 3, reviewed_at=(now - timedelta(days=days)).isoformat())
        assert res.status_code == 200, res.text
    s = client.get("/api/cards/stats", params={"tz": "UTC"}, headers=auth(key, world.owner)).json()
    assert (s["streak_days"], s["reviewed_today"]) == (1, 5)
    # a claim within the day counts when the student rated (2 h ago can be yesterday, just after midnight)
    hours = rate(client, key, world.owner, world.own, 3, reviewed_at=(now - timedelta(hours=2)).isoformat())
    assert hours.status_code == 200, hours.text
    s = client.get("/api/cards/stats", params={"tz": "UTC"}, headers=auth(key, world.owner)).json()
    yesterday = (now - timedelta(hours=2)).date() < now.date()
    assert (s["streak_days"], s["reviewed_today"]) == ((2, 5) if yesterday else (1, 6))
    rows = pg.execute(
        "select review from public.card_review_logs where card_id = %s order by review", (world.own,)
    ).fetchall()
    assert rows[0][0] < now - timedelta(days=5)  # the schedule still uses the time the student rated


def test_offline_reviews_replay_in_order(client: TestClient, key: SigningKey, world: World) -> None:
    """An outbox sends ratings made offline with their own times; the server schedules each from the last."""
    t0 = datetime.now(UTC) - timedelta(days=3)
    m = Memory()
    for minutes, rating in ((0, 3), (11, 3), (60 * 24 * 2, 2)):
        at = t0 + timedelta(minutes=minutes)
        res = rate(client, key, world.owner, world.own, rating, reviewed_at=at.isoformat())
        assert res.status_code == 200, res.text
        m = review(DEFAULTS, str(world.own), m, rating, at).memory
        assert m.due is not None
        assert datetime.fromisoformat(res.json()["schedule"]["due"]) == m.due


def test_reviewed_card_leaves_the_due_list_until_due(client: TestClient, key: SigningKey, world: World) -> None:
    before = due(client, key, world.owner)
    assert world.own in ids(before["cards"])
    rate(client, key, world.owner, world.own, 4)  # Easy: days away
    after = due(client, key, world.owner)
    assert world.own not in ids(after["cards"])
    assert after["due_count"] == before["due_count"] - 1
    assert after["next_due"] is not None
    assert after["scheduler"] == file_values(REPO_ROOT / "config")["fsrs"]


def test_due_list_orders_due_reviews_before_new_cards(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    past = datetime.now(UTC) - timedelta(days=1)
    pg.execute(
        "insert into public.card_reviews (user_id, card_id, due, stability, difficulty, reps, lapses, state,"
        " last_review) values (%s, %s, %s, 3.0, 5.0, 2, 0, 2, %s)",
        (world.owner, world.shared_plain, past, past - timedelta(days=3)),
    )
    order = [uuid.UUID(c["id"]) for c in due(client, key, world.owner)["cards"] if uuid.UUID(c["id"]) in world.visible]
    assert order[0] == world.shared_plain
    limited = client.get("/api/cards/due", params={"limit": 1}, headers=auth(key, world.owner)).json()
    assert len(limited["cards"]) == 1 and limited["due_count"] >= 3


# ── stats ──


def test_stats_count_in_the_students_zone(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    h = auth(key, world.owner)
    now = datetime.now(UTC)
    rate(client, key, world.owner, world.own, 3)  # learning: due in 10 minutes (today in any zone... or tomorrow)
    pg.execute(
        "insert into public.card_reviews (user_id, card_id, due, stability, difficulty, reps, lapses, state,"
        " last_review) values (%s, %s, %s, 10, 5, 3, 0, 2, %s)",
        (world.owner, world.shared_graded, now + timedelta(days=1, hours=1), now - timedelta(days=9)),
    )
    s = client.get("/api/cards/stats", params={"tz": "UTC"}, headers=h).json()
    tomorrow_utc = (now + timedelta(days=1, hours=1)).date() == (now + timedelta(days=1)).date()
    assert s["zone"] == "UTC" and s["reviewed_today"] >= 1 and s["streak_days"] == 1
    base = {world.shared_plain}  # new, so due today
    assert s["new"] >= len(base)
    if tomorrow_utc:
        assert s["due_tomorrow"] >= 1
    bad = client.get("/api/cards/stats", params={"tz": "Mars/Olympus"}, headers=h)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "card_zone_unknown"
    # the profile's zone is the default
    pg.execute(
        "insert into public.profiles (user_id, timezone) values (%s, 'America/St_Johns')"
        " on conflict (user_id) do update set timezone = excluded.timezone",
        (world.owner,),
    )
    assert client.get("/api/cards/stats", headers=h).json()["zone"] == "America/St_Johns"


def test_stats_retention_and_streak_from_logs(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    now = datetime.now(UTC)
    rows = []
    # three days in a row ending today (UTC), reviews of cards in the review state: 3 recalled, 1 forgotten
    for days, rating in ((0, 3), (1, 1), (2, 4), (2, 2)):
        at = now - timedelta(days=days, minutes=1)
        rows.append(
            (
                world.owner,
                world.own,
                uuid.uuid4(),
                rating,
                2,
                at,
                0,
                0,
                1,
                at,
                2,
                at + timedelta(days=3),
                3.0,
                5.0,
                2,
                0,
            )
        )
    with pg.cursor() as cur:
        cur.executemany(
            "insert into public.card_review_logs (user_id, card_id, client_review_id, rating, state, due,"
            " elapsed_days, last_elapsed_days, scheduled_days, review, next_state, next_due, next_stability,"
            " next_difficulty, reps, lapses, created_at)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [(*r, r[9]) for r in rows],  # received when it was rated: history as it happened
        )
    s = client.get("/api/cards/stats", params={"tz": "UTC"}, headers=auth(key, world.owner)).json()
    assert s["retention"] == {"reviews": 4, "recalled": 3, "rate": 0.75}
    assert s["streak_days"] == 3
    other = client.get("/api/cards/stats", params={"tz": "UTC"}, headers=auth(key, world.other)).json()
    assert other["retention"]["reviews"] == 0 and other["streak_days"] == 0
