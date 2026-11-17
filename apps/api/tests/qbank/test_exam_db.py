"""Integration (real Postgres, the full HTTP path): timed and exam blocks with a server-authoritative clock that the
test controls (`app.state.now`). Section start/pause/resume/end, breaks and auto-start, deadlines and grace,
changed answers, grading at the end of the whole session, Results, the active-exam lookup, and the proof that no
answer or explanation reaches the client (through any endpoint or the flashcard gate) before the block is finished.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import errors
from psycopg.types.json import Jsonb

from tests.auth.jwt_helpers import SigningKey
from tests.db.rls import as_user

from .conftest import World, auth, insert
from .test_qbank_db import answer, assert_no_secrets, both, question

pytestmark = [pytest.mark.integration, pytest.mark.db]

T0 = dt.datetime(2026, 11, 16, 9, 0, tzinfo=dt.UTC)


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> dt.datetime:
        return self.t

    def to(self, seconds: float) -> None:
        self.t = T0 + dt.timedelta(seconds=seconds)


@pytest.fixture
def now(client: TestClient) -> Iterator[Clock]:
    c = Clock()
    client.app.state.now = c  # type: ignore[attr-defined]
    yield c
    client.app.state.now = None  # type: ignore[attr-defined]


def timed_block(
    client: TestClient, key: SigningKey, w: World, *, mode: str = "exam", count: int = 3, sections: int = 1
):
    body = {"exam": "step1", "systems": both(w), "count": count, "mode": mode, "seed": 5, "sections": sections}
    res = client.post("/api/blocks", json=body, headers=auth(key, w.owner))
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


def post(client: TestClient, key: SigningKey, w: World, path: str) -> Any:
    return client.post(path, headers=auth(key, w.owner))


def code_of(res: Any) -> str:
    body: dict[str, Any] = res.json()
    return str(body["error"]["code"])


def ids_of(pg: psycopg.Connection[Any], block_id: str) -> list[str]:
    row = pg.execute("select question_ids from public.blocks where id = %s", (block_id,)).fetchone()
    assert row is not None
    return list(row[0])


# ── Clock and sections ──


def test_timed_block_runs_on_the_server_clock(
    client: TestClient, key: SigningKey, world: World, now: Clock, pg: psycopg.Connection[Any]
) -> None:
    w = world
    b = timed_block(client, key, w, count=3)
    bid = b["id"]
    assert b["config"]["sections"] == [{"n": 1, "first_index": 0, "count": 3, "time_limit_s": 270}]
    assert b["clock"]["state"] == "not_started" and b["clock"]["remaining_ms"] == 270_000
    assert b["started_at"] is None and b["clock"]["pausable"] is False and b["clock"]["grace_ms"] == 2000
    assert code_of(question(client, key, w, bid, 1)) == "not_started"

    now.to(10)
    started = post(client, key, w, f"/api/blocks/{bid}/sections/1/start")
    assert started.status_code == 200, started.text
    c = started.json()["clock"]
    assert c["state"] == "running" and c["deadline_at"] == "2026-11-16T09:04:40Z" and c["remaining_ms"] == 270_000

    now.to(70.25)
    clk = client.get(f"/api/blocks/{bid}/clock", headers=auth(key, w.owner)).json()
    assert clk["remaining_ms"] == 209_750 and clk["server_now"] == "2026-11-16T09:01:10.250000Z"
    view = question(client, key, w, bid, 1)
    assert view.status_code == 200 and view.json()["clock"]["state"] == "running" and view.json()["section_n"] == 1
    assert_no_secrets(view.text)


def test_answers_change_until_the_deadline_and_are_graded_only_at_the_end(
    client: TestClient, key: SigningKey, world: World, now: Clock, pg: psycopg.Connection[Any]
) -> None:
    w = world
    bid = timed_block(client, key, w, count=3)["id"]
    ids = ids_of(pg, bid)
    post(client, key, w, f"/api/blocks/{bid}/sections/1/start")

    now.to(20)
    first = answer(client, key, w, bid, ids[0], "B", seconds=7000)
    assert first.status_code == 200, first.text
    out = first.json()
    assert out["correct"] is None and out["explanation"] is None and out["finished"] is False
    assert out["seconds"] == 20  # clamped to the section's elapsed time
    assert_no_secrets(first.text)
    now.to(40)
    answer(client, key, w, bid, ids[0], "A", seconds=30)
    now.to(50)
    answer(client, key, w, bid, ids[1], "B", seconds=10)
    row = pg.execute(
        "select chosen, first_chosen, changes, correct, answered_at from public.attempts"
        " where block_id = %s and question_id = %s",
        (bid, ids[0]),
    ).fetchone()
    assert row is not None and row[:4] == ("A", "B", 1, None) and row[4] == T0 + dt.timedelta(seconds=40)

    # Mid-block nothing is graded anywhere: detail, list, the question view, the flashcard gate.
    detail = client.get(f"/api/blocks/{bid}", headers=auth(key, w.owner))
    d = detail.json()
    assert d["answered"] == 2 and d["correct"] is None and all(q["correct"] is None for q in d["questions"])
    assert_no_secrets(detail.text)
    listed = client.get("/api/blocks", headers=auth(key, w.owner))
    assert next(x for x in listed.json()["blocks"] if x["id"] == bid)["answered"] == 2
    assert_no_secrets(listed.text)
    v = question(client, key, w, bid, 1).json()
    assert v["attempt"] == {"chosen": "A", "seconds": 30, "correct": None} and v["explanation"] is None
    assert code_of(client.get(f"/api/blocks/{bid}/results", headers=auth(key, w.owner))) == "not_finished"

    # 1.5 s after the deadline an in-flight answer still counts; 3 s after, nothing changes any more.
    now.to(270 + 1.5)
    assert answer(client, key, w, bid, ids[2], "B", seconds=5).status_code == 200
    now.to(273)
    late = answer(client, key, w, bid, ids[2], "C")
    assert late.status_code == 409 and code_of(late) in ("section_ended", "finished")

    # The deadline ended the only section: the block is finished and graded with the server's time.
    clk = client.get(f"/api/blocks/{bid}/clock", headers=auth(key, w.owner)).json()
    assert clk["state"] == "finished"
    block = pg.execute("select finished_at, score from public.blocks where id = %s", (bid,)).fetchone()
    assert block is not None and block[0] == T0 + dt.timedelta(seconds=270) and float(block[1]) == 66.7
    graded = dict(pg.execute("select question_id, correct from public.attempts where block_id = %s", (bid,)).fetchall())
    assert graded == {ids[0]: False, ids[1]: True, ids[2]: True}
    review = question(client, key, w, bid, 1).json()
    assert review["explanation"]["answer"] == "B" and review["attempt"]["correct"] is False


def test_unanswered_questions_are_omitted_misses_and_reviewable(
    client: TestClient, key: SigningKey, world: World, now: Clock, pg: psycopg.Connection[Any]
) -> None:
    w = world
    bid = timed_block(client, key, w, count=3)["id"]
    ids = ids_of(pg, bid)
    post(client, key, w, f"/api/blocks/{bid}/sections/1/start")
    now.to(5)
    client.patch(f"/api/blocks/{bid}/attempts/{ids[1]}", json={"flagged": True}, headers=auth(key, w.owner))
    answer(client, key, w, bid, ids[0], "B")
    now.to(30)
    fin = post(client, key, w, f"/api/blocks/{bid}/finish")
    assert fin.status_code == 200 and fin.json()["clock"]["state"] == "finished" and fin.json()["score"] == 33.3
    rows = pg.execute(
        "select question_id, chosen, correct, flagged from public.attempts where block_id = %s order by question_id",
        (bid,),
    ).fetchall()
    assert {r[0]: r[1:] for r in rows} == {
        ids[0]: ("B", True, False),
        ids[1]: (None, False, True),
        ids[2]: (None, False, False),
    }
    omitted = question(client, key, w, bid, 3).json()
    assert omitted["attempt"] == {"chosen": None, "seconds": None, "correct": False}
    assert omitted["explanation"]["answer"] == "B"
    assert post(client, key, w, f"/api/blocks/{bid}/finish").json()["score"] == 33.3  # idempotent


def test_pause_is_refused_in_exam_mode_and_freezes_timed_mode(
    client: TestClient, key: SigningKey, world: World, now: Clock, pg: psycopg.Connection[Any]
) -> None:
    w = world
    ex = timed_block(client, key, w, mode="exam")["id"]
    post(client, key, w, f"/api/blocks/{ex}/sections/1/start")
    refused = post(client, key, w, f"/api/blocks/{ex}/sections/1/pause")
    assert refused.status_code == 409 and code_of(refused) == "pause_exam_mode"
    assert refused.json()["error"]["message"] == "pause_exam_mode"  # a code only: the client words it
    post(client, key, w, f"/api/blocks/{ex}/finish")  # one session at a time

    tb = timed_block(client, key, w, mode="timed", count=2)
    t = tb["id"]
    assert tb["clock"]["pausable"] is True
    post(client, key, w, f"/api/blocks/{t}/sections/1/start")
    now.to(60)
    paused = post(client, key, w, f"/api/blocks/{t}/sections/1/pause").json()["clock"]
    assert paused["state"] == "paused" and paused["remaining_ms"] == 120_000
    assert code_of(question(client, key, w, t, 1)) == "paused"
    assert code_of(answer(client, key, w, t, ids_of(pg, t)[0], "B")) == "paused"
    now.to(3600)
    assert client.get(f"/api/blocks/{t}/clock", headers=auth(key, w.owner)).json()["remaining_ms"] == 120_000
    resumed = post(client, key, w, f"/api/blocks/{t}/sections/1/resume").json()["clock"]
    assert resumed["state"] == "running" and resumed["deadline_at"] == "2026-11-16T10:02:00Z"


def test_two_sections_with_a_break_and_no_going_back(
    client: TestClient, key: SigningKey, world: World, now: Clock, pg: psycopg.Connection[Any]
) -> None:
    w = world
    b = timed_block(client, key, w, count=2, sections=2)
    bid = b["id"]
    assert b["config"]["break_s"] == 45 * 60 and [s["first_index"] for s in b["config"]["sections"]] == [0, 2]
    assert [q["section_n"] for q in b["questions"]] == [1, 1, 2, 2]
    ids = ids_of(pg, bid)
    assert code_of(post(client, key, w, f"/api/blocks/{bid}/sections/2/start")) == "not_current"
    post(client, key, w, f"/api/blocks/{bid}/sections/1/start")
    assert code_of(question(client, key, w, bid, 3)) == "not_current"  # only the running section is served
    answer(client, key, w, bid, ids[0], "B")
    now.to(100)
    ended = post(client, key, w, f"/api/blocks/{bid}/sections/1/end").json()["clock"]
    assert ended["state"] == "break" and ended["section_n"] == 2 and ended["break_remaining_ms"] == 2_700_000
    assert b["clock"]["break_left_ms"] == 2_700_000
    assert code_of(question(client, key, w, bid, 1)) == "section_ended"
    assert code_of(question(client, key, w, bid, 3)) == "on_break"
    assert code_of(answer(client, key, w, bid, ids[1], "B")) == "section_ended"
    flag = client.patch(f"/api/blocks/{bid}/attempts/{ids[1]}", json={"flagged": True}, headers=auth(key, w.owner))
    assert flag.status_code == 409

    # Nobody comes back: the pool runs out and section 2 starts by itself at that instant.
    now.to(100 + 2700 + 30)
    clk = client.get(f"/api/blocks/{bid}/clock", headers=auth(key, w.owner)).json()
    assert clk["state"] == "running" and clk["section_n"] == 2 and clk["remaining_ms"] == 180_000 - 30_000
    assert question(client, key, w, bid, 3).status_code == 200
    row = pg.execute("select break_used_ms from public.blocks where id = %s", (bid,)).fetchone()
    assert row == (2_700_000,)


def test_a_missing_section_row_is_expired_not_fresh_time(
    client: TestClient, key: SigningKey, world: World, now: Clock, pg: psycopg.Connection[Any]
) -> None:
    w = world
    bid = timed_block(client, key, w, count=2)["id"]
    post(client, key, w, f"/api/blocks/{bid}/sections/1/start")
    pg.execute("delete from public.block_sections where block_id = %s", (bid,))
    now.to(5)
    assert client.get(f"/api/blocks/{bid}/clock", headers=auth(key, w.owner)).json()["state"] == "finished"
    row = pg.execute("select ended_reason from public.block_sections where block_id = %s", (bid,)).fetchone()
    assert row == ("expired",)


def test_timed_modes_validate_sections(client: TestClient, key: SigningKey, world: World) -> None:
    w = world
    h = auth(key, w.owner)
    base = {"exam": "step1", "systems": both(w), "count": 2, "seed": 1}
    assert client.post("/api/blocks", json={**base, "mode": "tutor", "sections": 2}, headers=h).status_code == 422
    assert client.post("/api/blocks", json={**base, "mode": "exam", "sections": 8}, headers=h).status_code == 422
    # 2 x 8 = 16 questions, the world has 12
    too_many = client.post("/api/blocks", json={**base, "count": 8, "mode": "exam", "sections": 2}, headers=h)
    assert too_many.status_code == 422


def test_transitions_on_other_users_and_tutor_blocks(
    client: TestClient, key: SigningKey, world: World, now: Clock
) -> None:
    w = world
    bid = timed_block(client, key, w)["id"]
    other = client.post(f"/api/blocks/{bid}/sections/1/start", headers=auth(key, w.other))
    assert other.status_code == 404
    assert client.get(f"/api/blocks/{bid}/clock", headers=auth(key, w.other)).status_code == 404
    tutor = client.post(
        "/api/blocks", json={"exam": "step1", "systems": both(w), "count": 2, "seed": 2}, headers=auth(key, w.owner)
    ).json()
    assert tutor["clock"] is None
    assert code_of(post(client, key, w, f"/api/blocks/{tutor['id']}/sections/1/start")) == "not_timed"
    assert client.post(f"/api/blocks/{bid}/sections/9/start", headers=auth(key, w.owner)).status_code == 409


def test_active_exam_follows_the_clock(client: TestClient, key: SigningKey, world: World, now: Clock) -> None:
    w = world
    h = auth(key, w.owner)
    bid = timed_block(client, key, w)["id"]
    assert client.get("/api/exam/active", headers=h).json() == {"active": None}  # not started
    post(client, key, w, f"/api/blocks/{bid}/sections/1/start")
    active = client.get("/api/exam/active", headers=h).json()["active"]
    assert active["block_id"] == bid and active["mode"] == "exam" and active["clock"]["state"] == "running"
    assert client.get("/api/exam/active", headers=auth(key, w.other)).json() == {"active": None}
    now.to(10_000)
    assert client.get("/api/exam/active", headers=h).json() == {"active": None}  # expired while nobody looked


# ── Results ──


def test_results_aggregate_the_finished_block(
    client: TestClient, key: SigningKey, world: World, now: Clock, pg: psycopg.Connection[Any]
) -> None:
    w = world
    bid = timed_block(client, key, w, count=4)["id"]
    ids = ids_of(pg, bid)
    post(client, key, w, f"/api/blocks/{bid}/sections/1/start")
    now.to(30)
    answer(client, key, w, bid, ids[0], "B", seconds=30)  # right
    now.to(60)
    answer(client, key, w, bid, ids[1], "B", seconds=20)  # right -> wrong
    answer(client, key, w, bid, ids[1], "C", seconds=25)
    now.to(90)
    answer(client, key, w, bid, ids[2], "A", seconds=30, flagged=True)  # wrong -> right
    answer(client, key, w, bid, ids[2], "B", seconds=35)
    now.to(100)
    post(client, key, w, f"/api/blocks/{bid}/finish")
    res = client.get(f"/api/blocks/{bid}/results", headers=auth(key, w.owner))
    assert res.status_code == 200, res.text
    r = res.json()
    assert (r["total"], r["correct"], r["omitted"], r["percent"]) == (4, 2, 1, 50.0)
    assert r["mode"] == "exam" and r["mock"] is None
    assert r["time"] == {"mean_s": 30.0, "median_s": 30.0, "target_s": 90, "total_s": 90}
    assert r["changes"] == {"changed": 2, "right_to_wrong": 1, "wrong_to_right": 1, "wrong_to_wrong": 0}
    assert r["flags"] == {"flagged": 1, "flagged_correct": 1}
    assert [q["outcome"] for q in r["questions"]] == ["correct", "incorrect", "correct", "omitted"]
    assert [q["change"] for q in r["questions"]] == [None, "right_to_wrong", "wrong_to_right", None]
    assert {s["id"] for s in r["systems"]} <= {w.sys_a, w.sys_b}
    assert all(s["weak"] == (s["percent"] < 60) for s in r["systems"])
    assert r["trend"][-1]["block_id"] == bid and r["trend"][-1]["current"] is True
    assert all(isinstance(i["kind"], str) and isinstance(i["vars"], dict) for i in r["insights"])
    # Results never carry answer text or explanations (review goes through the question view).
    assert_no_secrets(res.text)


# ── Leaks and client privileges ──


def test_no_answer_field_reaches_the_client_before_the_block_finishes(
    client: TestClient, key: SigningKey, world: World, now: Clock, pg: psycopg.Connection[Any]
) -> None:
    w = world
    h = auth(key, w.owner)
    b = timed_block(client, key, w, mode="timed", count=3)
    bid = b["id"]
    assert_no_secrets(
        client.post(
            "/api/blocks", json={"exam": "step1", "systems": both(w), "count": 2, "mode": "exam"}, headers=h
        ).text
    )
    ids = ids_of(pg, bid)
    texts = [
        post(client, key, w, f"/api/blocks/{bid}/sections/1/start").text,
        answer(client, key, w, bid, ids[0], "B").text,
        answer(client, key, w, bid, ids[1], "A").text,
        client.get(f"/api/blocks/{bid}", headers=h).text,
        client.get("/api/blocks", headers=h).text,
        client.get(f"/api/blocks/{bid}/clock", headers=h).text,
        client.get("/api/exam/active", headers=h).text,
        client.get(f"/api/blocks/{bid}/results", headers=h).text,
        post(client, key, w, f"/api/blocks/{bid}/sections/1/pause").text,
        post(client, key, w, f"/api/blocks/{bid}/sections/1/resume").text,
        client.patch(f"/api/blocks/{bid}/attempts/{ids[2]}", json={"flagged": True}, headers=h).text,
        *[question(client, key, w, bid, n).text for n in (1, 2, 3)],
    ]
    for t in texts:
        assert_no_secrets(t)
    # The client's own reads cannot see grading either: `correct` is not selectable by students (0010), and the
    # API leaves it NULL until the whole block is finished.
    with as_user(pg, w.owner) as conn:
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("select correct from public.attempts where block_id = %s", (bid,))
        assert conn.execute(
            "select count(*) from public.attempts where block_id = %s and chosen is not null", (bid,)
        ).fetchone() == (2,)
    graded = pg.execute("select count(*) from public.attempts where block_id = %s and correct is not null", (bid,))
    assert graded.fetchone() == (0,)


def test_the_flashcard_gate_opens_only_once_the_block_is_graded(
    client: TestClient, key: SigningKey, world: World, now: Clock, pg: psycopg.Connection[Any]
) -> None:
    """The world's shared card restates question 1's answer. A mid-exam choice or a flag must not reveal it
    (the gate needs a graded attempt with a choice, migration 0010); grading at the finish does."""
    w = world
    h = auth(key, w.owner)
    body = {"exam": "step1", "systems": [w.sys_a], "count": 8, "mode": "exam", "seed": 3}
    bid = client.post("/api/blocks", json=body, headers=h).json()["id"]
    q1 = w.questions[0]
    assert q1 in ids_of(pg, bid)
    post(client, key, w, f"/api/blocks/{bid}/sections/1/start")

    def cards() -> Any:
        with as_user(pg, w.owner) as conn:
            return conn.execute("select count(*) from public.flashcards where question_id = %s", (q1,)).fetchone()

    client.patch(f"/api/blocks/{bid}/attempts/{q1}", json={"flagged": True}, headers=h)
    assert cards() == (0,)
    answer(client, key, w, bid, q1, "B")
    assert cards() == (0,)
    now.to(60)
    post(client, key, w, f"/api/blocks/{bid}/finish")
    assert cards() == (1,)


def test_clients_cannot_write_timing_or_grading(
    pg: psycopg.Connection[Any], client: TestClient, key: SigningKey, world: World, now: Clock
) -> None:
    w = world
    bid = timed_block(client, key, w)["id"]
    post(client, key, w, f"/api/blocks/{bid}/sections/1/start")
    with as_user(pg, w.owner) as conn:
        assert conn.execute("select count(*) from public.block_sections where block_id = %s", (bid,)).fetchone() == (1,)
        for stmt, params in (
            ("update public.block_sections set deadline_at = now() + interval '9 hours'", ()),
            ("update public.block_sections set ended_at = null, ended_reason = null", ()),
            ("delete from public.block_sections", ()),
            (
                "insert into public.block_sections (block_id, user_id, n, first_index, count, time_limit_s)"
                " values (%s, %s, 9, 0, 1, 99999)",
                (bid, w.owner),
            ),
            ("update public.blocks set break_used_ms = 0", ()),
            ("update public.attempts set first_chosen = 'B'", ()),
            ("update public.attempts set changes = 0", ()),
            ("update public.attempts set answered_at = now()", ()),
            ("update public.attempts set correct = true", ()),
        ):
            with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
                conn.execute(stmt, params)
    with as_user(pg, w.other) as conn:  # and nobody sees another student's timing
        assert conn.execute("select count(*) from public.block_sections where block_id = %s", (bid,)).fetchone() == (0,)


# ── Mock exams ──


@pytest.fixture
def mock_a(pg: psycopg.Connection[Any]) -> Iterator[tuple[list[str], list[int]]]:
    """Mock A as seeded (data/ imported), or a small stand-in with the same shape when the seed is absent."""
    seeded = pg.execute(
        "select id, block from public.questions where chapter_id = 'mock1' and block is not null order by block, id"
    ).fetchall()
    if seeded:
        yield [r[0] for r in seeded], [int(r[1]) for r in seeded]
        return
    tag = uuid.uuid4().hex[:6]
    src = f"t{tag}-src"
    insert(pg, "sources", {"id": src, "name": "Naadi original", "license": "proprietary-naadi"})
    insert(pg, "chapters", {"id": "mock1", "exam": "step1", "kind": "mock_exam", "title": "Mock A", "source_id": src})
    ids, blocks = [], []
    for i in range(1, 7):
        qid = f"mock1-t{tag}-{i:02d}"
        insert(
            pg,
            "questions",
            {
                "id": qid,
                "chapter_id": "mock1",
                "exam": "step1",
                "block": 1 if i <= 3 else 2,
                "stem_html": f"<p>Mock stem {i}</p>",
                "options": Jsonb([{"label": x, "html": x} for x in "ABCDE"]),
                "answer": "C",
                "explanation_html": f"<p>SECRET-EXPLANATION-mock{i}</p>",
                "source_id": src,
            },
        )
        ids.append(qid)
        blocks.append(1 if i <= 3 else 2)
    yield ids, blocks
    pg.execute("delete from public.blocks where config->>'mock' = 'mock1' and question_ids && %s", (ids,))
    pg.execute("delete from public.attempts where question_id = any(%s)", (ids,))
    pg.execute("delete from public.questions where id = any(%s)", (ids,))
    pg.execute("delete from public.chapters where id = 'mock1'")
    pg.execute("delete from public.sources where id = %s", (src,))


def test_mock_exam_runs_as_a_two_section_exam_in_its_fixed_order(
    client: TestClient,
    key: SigningKey,
    world: World,
    now: Clock,
    pg: psycopg.Connection[Any],
    mock_a: tuple[list[str], list[int]],
) -> None:
    w = world
    h = auth(key, w.owner)
    ids, blocks = mock_a
    res = client.post("/api/blocks/mock", json={"chapter_id": "mock1"}, headers=h)
    assert res.status_code == 201, res.text
    b = res.json()
    bid = b["id"]
    assert b["config"]["mode"] == "exam" and b["config"]["mock"] == "mock1" and b["total"] == len(ids)
    assert ids_of(pg, bid) == ids
    per = blocks.count(1)
    assert [(s["n"], s["count"], s["time_limit_s"]) for s in b["config"]["sections"]] == [
        (1, per, 3600),
        (2, len(ids) - per, 3600),
    ]
    assert b["clock"]["state"] == "not_started" and b["clock"]["sections"] == 2
    assert_no_secrets(res.text)
    assert client.post("/api/blocks/mock", json={"chapter_id": "ch01"}, headers=h).status_code == 422

    post(client, key, w, f"/api/blocks/{bid}/sections/1/start")
    for i in range(per):
        assert answer(client, key, w, bid, ids[i], "C").status_code == 200
    now.to(1200)
    assert post(client, key, w, f"/api/blocks/{bid}/sections/1/end").json()["clock"]["state"] == "break"
    now.to(1500)
    s2 = post(client, key, w, f"/api/blocks/{bid}/sections/2/start").json()["clock"]
    assert s2["state"] == "running" and s2["section_n"] == 2 and s2["remaining_ms"] == 3_600_000
    view = question(client, key, w, bid, per + 1)
    assert view.status_code == 200 and view.json()["explanation"] is None
    assert_no_secrets(view.text)
    answer(client, key, w, bid, ids[per], "A")
    # the deadline of section 2 ends the session; the whole block is graded then
    now.to(1500 + 3600 + 5)
    r = client.get(f"/api/blocks/{bid}/results", headers=h).json()
    assert r["mock"] == "mock1" and r["total"] == len(ids) and r["omitted"] == len(ids) - per - 1
    assert r["correct"] == sum(
        1
        for i in range(per)
        if pg.execute("select answer from public.questions where id = %s", (ids[i],)).fetchone() == ("C",)
    )
    explained = question(client, key, w, bid, 1).json()
    assert explained["explanation"] is not None


def test_timed_modes_are_refused_while_the_flag_is_off(
    client: TestClient, key: SigningKey, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from naadi_api.qbank import exam_routes, routes

    async def off(*_: Any) -> bool:
        return False

    monkeypatch.setattr(routes, "_timed_available", off)
    monkeypatch.setattr(exam_routes, "_timed_available", off)
    w, h = world, auth(key, world.owner)
    base = {"exam": "step1", "systems": both(w), "count": 2}
    timed = client.post("/api/blocks", json={**base, "mode": "timed"}, headers=h)
    assert timed.status_code == 422 and code_of(timed) == "timed_unavailable"
    assert client.post("/api/blocks", json={**base, "mode": "exam"}, headers=h).status_code == 422
    mock = client.post("/api/blocks/mock", json={"chapter_id": "mock1"}, headers=h)
    assert mock.status_code == 422 and code_of(mock) == "timed_unavailable"


def test_create_timed_block_for_the_diagnostic(
    client: TestClient, key: SigningKey, world: World, url: str, now: Clock
) -> None:
    """S11's onboarding diagnostic: chosen questions in one timed section, run in the timed player."""
    import asyncio

    from pydantic import SecretStr

    from naadi_api.auth import CurrentUser
    from naadi_api.db import Database
    from naadi_api.qbank.models import BlockConfig
    from naadi_api.qbank.timed_blocks import create_timed_block

    w = world
    rules = {
        "exams": {"step1": {"blocks": 7, "minutes_per_block": 60}},
        "modes": {"timed": {"seconds_per_question": 90}, "exam": {"seconds_per_question": 90}},
        "break_minutes": 45,
        "expiry_grace_seconds": 2,
    }
    cfg = BlockConfig(
        exam="step1",
        mode="timed",
        status="all",
        difficulty=None,
        systems=[],
        disciplines=[],
        seed=7,
        weak_spot=False,
        purpose="diagnostic",
    )

    async def make() -> Any:
        db = Database(SecretStr(url))
        try:
            user = CurrentUser(user_id=w.owner, email=None, email_verified=True, is_admin=False)
            return await create_timed_block(db, user, cfg, w.questions[:4], rules=rules, now=T0)
        finally:
            await db.close()

    block = asyncio.run(make())
    assert block.config.purpose == "diagnostic" and block.config.mode == "timed"
    assert [(s.count, s.time_limit_s) for s in block.config.sections or []] == [(4, 360)]
    assert block.clock is not None and block.clock.state == "not_started" and block.clock.pausable
    detail = client.get(f"/api/blocks/{block.id}", headers=auth(key, w.owner)).json()
    assert detail["config"]["purpose"] == "diagnostic" and detail["total"] == 4
    with pytest.raises(ValueError, match="timed or exam"):
        asyncio.run(
            create_timed_block(
                Database(SecretStr(url)), None, cfg.model_copy(update={"mode": "tutor"}), [], rules=rules
            )
        )  # type: ignore[arg-type]


# ── One session at a time; no tutor practice while it runs (S09 review 2 and 6) ──


def test_a_running_exam_blocks_every_tutor_path_and_a_second_session(
    client: TestClient, key: SigningKey, world: World, now: Clock, pg: psycopg.Connection[Any]
) -> None:
    w = world
    h = auth(key, w.owner)
    tutor = client.post(
        "/api/blocks", json={"exam": "step1", "systems": both(w), "count": 2, "seed": 4}, headers=h
    ).json()
    bid = timed_block(client, key, w, count=3)["id"]
    ids = ids_of(pg, bid)
    # a session that has not started is not active: tutor practice still works
    assert client.post("/api/blocks/single", json={"question_id": ids[0]}, headers=h).status_code in (200, 201)
    post(client, key, w, f"/api/blocks/{bid}/sections/1/start")
    now.to(5)

    def refused(res: Any) -> None:
        assert res.status_code == 409, res.text
        assert code_of(res) == "exam_active"
        assert_no_secrets(res.text)

    refused(client.post("/api/blocks/single", json={"question_id": ids[1]}, headers=h))
    refused(client.post("/api/blocks", json={"exam": "step1", "systems": both(w), "count": 2, "seed": 9}, headers=h))
    refused(client.post("/api/blocks/weak-spot", json={"exam": "step1"}, headers=h))
    tq = question(client, key, w, tutor["id"], 1).json()["question"]["id"]
    refused(answer(client, key, w, tutor["id"], tq, "B"))
    # a second timed/exam session cannot be created or started
    refused(
        client.post("/api/blocks", json={"exam": "step1", "systems": both(w), "count": 2, "mode": "timed"}, headers=h)
    )
    # the same refusal while paused (timed) or on a break would apply; after the finish everything opens again
    now.to(30)
    post(client, key, w, f"/api/blocks/{bid}/finish")
    assert client.post("/api/blocks/single", json={"question_id": ids[1]}, headers=h).status_code in (200, 201)
    assert timed_block(client, key, w, count=2, mode="timed")["clock"]["state"] == "not_started"


def test_a_second_session_cannot_start_while_one_runs(
    client: TestClient, key: SigningKey, world: World, now: Clock
) -> None:
    w = world
    first = timed_block(client, key, w, count=2)["id"]
    second = timed_block(client, key, w, count=2)["id"]  # created before either started: allowed
    post(client, key, w, f"/api/blocks/{first}/sections/1/start")
    res = post(client, key, w, f"/api/blocks/{second}/sections/1/start")
    assert res.status_code == 409 and code_of(res) == "exam_active"
    assert post(client, key, w, f"/api/blocks/{first}/sections/1/start").status_code == 200  # its own: idempotent


def test_no_study_hints_mid_session(
    client: TestClient, key: SigningKey, world: World, now: Clock, pg: psycopg.Connection[Any]
) -> None:
    w = world
    bid = timed_block(client, key, w, count=12)["id"]  # the whole world: question 1 links a lesson
    n = ids_of(pg, bid).index(w.questions[0]) + 1
    post(client, key, w, f"/api/blocks/{bid}/sections/1/start")
    mid = question(client, key, w, bid, n).json()
    assert mid["lesson"] is None and mid["question"]["topics"] == []
    now.to(20)
    post(client, key, w, f"/api/blocks/{bid}/finish")
    after = question(client, key, w, bid, n).json()
    assert after["lesson"] is not None and after["question"]["topics"]
