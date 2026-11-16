"""Integration (real Postgres, migrated schema): pool counts, block building, weak-spot, tutor-mode questions,
server-side grading, idempotent attempts, flags, lab values, and the proof that no answer or explanation reaches a
client before the student has a graded attempt. The full HTTP path runs: bearer token -> JWKS -> user_tx -> RLS."""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.auth.jwt_helpers import SigningKey

from .conftest import (
    SECRET_ANSWER_HTML,
    SECRET_EXPLANATION,
    SECRET_OBJECTIVE,
    SECRET_WHY_NOT,
    World,
    auth,
)

pytestmark = [pytest.mark.integration, pytest.mark.db]

SECRETS = (SECRET_ANSWER_HTML, SECRET_EXPLANATION, SECRET_OBJECTIVE, SECRET_WHY_NOT)
LEAK_KEYS = ("answer_html", "explanation_html", "why_not_html", "educational_objective")


def both(w: World) -> list[str]:
    return [w.sys_a, w.sys_b]


def make_block(client: TestClient, key: SigningKey, w: World, **over: Any) -> dict[str, Any]:
    body = {"exam": "step1", "systems": both(w), "count": 10, "mode": "tutor", "seed": 11, **over}
    res = client.post("/api/blocks", json=body, headers=auth(key, w.owner))
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


def question(client: TestClient, key: SigningKey, w: World, block_id: str, n: int, who: uuid.UUID | None = None) -> Any:
    return client.get(f"/api/blocks/{block_id}/questions/{n}", headers=auth(key, who or w.owner))


def assert_no_secrets(text: str) -> None:
    assert not any(s in text for s in SECRETS), "an answer or explanation leaked"
    payload = json.loads(text)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in LEAK_KEYS and k != "answer", f"leaked key {k}"
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)


def answer(client: TestClient, key: SigningKey, w: World, block_id: str, qid: str, chosen: str, **over: Any) -> Any:
    body = {"question_id": qid, "chosen": chosen, "seconds": 12, **over}
    return client.post(f"/api/blocks/{block_id}/attempts", json=body, headers=auth(key, w.owner))


# ── Auth and the pool ──


def test_every_endpoint_requires_sign_in(client: TestClient) -> None:
    for method, path in (
        ("get", "/api/qbank/pool"),
        ("get", "/api/blocks"),
        ("post", "/api/blocks"),
        ("post", "/api/blocks/weak-spot"),
        ("get", f"/api/blocks/{uuid.uuid4()}"),
        ("get", f"/api/blocks/{uuid.uuid4()}/questions/1"),
        ("post", f"/api/blocks/{uuid.uuid4()}/attempts"),
        ("patch", f"/api/blocks/{uuid.uuid4()}/attempts/q1"),
        ("get", "/api/lab-values"),
    ):
        assert getattr(client, method)(path).status_code == 401, path


def test_pool_counts_follow_filters_history_and_taxonomy(client: TestClient, key: SigningKey, world: World) -> None:
    w = world
    res = client.get("/api/qbank/pool", params={"exam": "step1", "systems": both(w)}, headers=auth(key, w.owner))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 12
    sys_counts = {s["id"]: s["count"] for s in body["systems"]}
    assert sys_counts[w.sys_a] == 8 and sys_counts[w.sys_b] == 4
    assert {s["id"]: s["label"] for s in body["systems"]}[w.sys_a] == "Alpha system"
    assert {d["id"]: d["count"] for d in body["disciplines"]}[w.disc] == 6
    assert body["status"]["unused"] == 12 and body["status"]["incorrect"] == 0
    assert body["difficulty"]["all"] == 12 and sum(body["difficulty"][d] for d in ("easy", "medium", "hard")) == 12
    assert body["limits"] == {"min_questions": 1, "max_questions": 40, "default_questions": 40}
    assert [m["id"] for m in body["modes"]] == ["tutor", "timed", "exam"]
    assert [m["available"] for m in body["modes"]] == [True, False, False]  # S09 screens are not on yet
    assert {e["id"] for e in body["exams"]} == {"step1", "step2ck"}

    only_a = client.get(
        "/api/qbank/pool",
        params={"exam": "step1", "systems": [w.sys_a], "disciplines": [w.disc]},
        headers=auth(key, w.owner),
    ).json()
    assert only_a["total"] == 4  # q2, q4, q6, q8
    # the other system chip still shows what choosing it would give with the discipline applied
    assert {s["id"]: s["count"] for s in only_a["systems"]}[w.sys_b] == 2

    # Answer one wrong and one right: the counts move for this student only.
    block = make_block(client, key, w, count=4, systems=[w.sys_a])
    first, second = block["questions"][0], block["questions"][1]
    assert first["n"] == 1
    ids = [question(client, key, w, block["id"], n).json()["question"]["id"] for n in (1, 2)]
    assert answer(client, key, w, block["id"], ids[0], "A").status_code == 200  # wrong
    assert answer(client, key, w, block["id"], ids[1], "B", flagged=True).status_code == 200  # right
    after = client.get(
        "/api/qbank/pool", params={"exam": "step1", "systems": both(w)}, headers=auth(key, w.owner)
    ).json()
    assert after["status"]["unused"] == 10
    assert after["status"]["incorrect"] == 1 and after["status"]["correct"] == 1 and after["status"]["flagged"] == 1
    incorrect = client.get(
        "/api/qbank/pool",
        params={"exam": "step1", "systems": both(w), "status": "incorrect"},
        headers=auth(key, w.owner),
    ).json()
    assert incorrect["total"] == 1
    theirs = client.get(
        "/api/qbank/pool", params={"exam": "step1", "systems": both(w)}, headers=auth(key, w.other)
    ).json()
    assert theirs["status"]["unused"] == 12  # another student's history is not visible
    assert second["answered"] is False


def test_pool_rejects_bad_input(client: TestClient, key: SigningKey, world: World) -> None:
    h = auth(key, world.owner)
    assert client.get("/api/qbank/pool", params={"exam": "Step 1!"}, headers=h).status_code == 422
    assert client.get("/api/qbank/pool", params={"exam": "step3"}, headers=h).status_code == 422  # not in exam_rules
    assert client.get("/api/qbank/pool", params={"status": "bogus"}, headers=h).status_code == 422
    assert client.get("/api/qbank/pool", params={"systems": "bad id"}, headers=h).status_code == 422


# ── Building blocks ──


def test_block_is_deterministic_for_the_seed_without_duplicates(
    client: TestClient, key: SigningKey, world: World
) -> None:
    w = world
    a = make_block(client, key, w, count=8, seed=5)
    b = make_block(client, key, w, count=8, seed=5)
    c = make_block(client, key, w, count=8, seed=6)

    def ids(block: dict[str, Any]) -> list[str]:
        return [question(client, key, w, block["id"], q["n"]).json()["question"]["id"] for q in block["questions"]]

    assert ids(a) == ids(b)
    assert ids(a) != ids(c)
    assert len(set(ids(a))) == 8 and set(ids(a)) <= set(w.questions)
    assert a["config"]["seed"] == 5 and a["config"]["mode"] == "tutor" and a["config"]["weak_spot"] is False
    assert a["total"] == 8 and a["answered"] == 0 and a["next_n"] == 1


def test_block_respects_filters(client: TestClient, key: SigningKey, world: World) -> None:
    w = world
    block = make_block(client, key, w, systems=[w.sys_b], count=4)
    ids = {question(client, key, w, block["id"], q["n"]).json()["question"]["id"] for q in block["questions"]}
    assert ids == set(w.questions[8:])
    hard = make_block(client, key, w, count=1, difficulty="hard")
    q = question(client, key, w, hard["id"], 1).json()["question"]
    assert q["difficulty"] == "hard"
    with_disc = make_block(client, key, w, systems=[w.sys_a], disciplines=[w.disc], count=4)
    ids = {question(client, key, w, with_disc["id"], x["n"]).json()["question"]["id"] for x in with_disc["questions"]}
    assert ids == {w.questions[i] for i in (1, 3, 5, 7)}


def test_status_filter_builds_from_the_callers_own_history(client: TestClient, key: SigningKey, world: World) -> None:
    w = world
    block = make_block(client, key, w, systems=[w.sys_b], count=2)
    ids = [question(client, key, w, block["id"], n).json()["question"]["id"] for n in (1, 2)]
    answer(client, key, w, block["id"], ids[0], "A")  # wrong
    answer(client, key, w, block["id"], ids[1], "B")  # right
    redo = make_block(client, key, w, systems=[w.sys_b], status="incorrect", count=1)
    assert question(client, key, w, redo["id"], 1).json()["question"]["id"] == ids[0]
    fresh = make_block(client, key, w, systems=[w.sys_b], status="unused", count=2)
    got = {question(client, key, w, fresh["id"], n).json()["question"]["id"] for n in (1, 2)}
    assert got.isdisjoint(ids)
    too_many = client.post(
        "/api/blocks",
        json={"exam": "step1", "systems": [w.sys_b], "status": "unused", "count": 3, "mode": "tutor"},
        headers=auth(key, w.owner),
    )
    assert too_many.status_code == 422
    assert "Only 2 questions match" in too_many.json()["error"]["message"]


def test_block_validation(client: TestClient, key: SigningKey, world: World) -> None:
    w, h = world, auth(key, world.owner)
    base = {"exam": "step1", "systems": both(w), "mode": "tutor"}
    assert client.post("/api/blocks", json={**base, "count": 0}, headers=h).status_code == 422
    over = client.post("/api/blocks", json={**base, "count": 41}, headers=h)
    assert over.status_code == 422
    small = client.post("/api/blocks", json={**base, "count": 50}, headers=h)
    assert small.status_code == 422 and small.json()["error"]["message"] == "A block has 1 to 40 questions."
    big = client.post("/api/blocks", json={**base, "count": 13}, headers=h)
    assert big.status_code == 422 and "Only 12 questions match" in big.json()["error"]["message"]
    assert client.post("/api/blocks", json={**base, "exam": "step3", "count": 5}, headers=h).status_code == 422
    assert client.post("/api/blocks", json={**base, "count": 5, "correct": True}, headers=h).status_code == 422
    timed = client.post("/api/blocks", json={**base, "count": 5, "mode": "timed"}, headers=h)
    assert timed.status_code == 422 and "not available yet" in timed.json()["error"]["message"]
    assert client.post("/api/blocks", json={**base, "count": 5, "mode": "exam"}, headers=h).status_code == 422


def test_weak_spot_needs_history_then_targets_the_weakest_cell(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    """Fixed questions and a fixed seed: three misses and one hit, all in system A and in no discipline, so the
    weak cells are exactly {A} and the three missed questions come first."""
    w, h = world, auth(key, world.owner)
    none = client.post("/api/blocks/weak-spot", json={"exam": "step1"}, headers=h)
    assert none.status_code == 422 and "practice history" in none.json()["error"]["message"]

    odd = [w.questions[i] for i in (0, 2, 4, 6)]  # q1 q3 q5 q7: system A, not the discipline (even ones carry it)
    row = pg.execute(
        "insert into public.blocks (user_id, config, question_ids, started_at) values (%s, %s::jsonb, %s, now())"
        " returning id",
        (
            w.owner,
            json.dumps(
                {
                    "exam": "step1",
                    "mode": "tutor",
                    "status": "all",
                    "difficulty": None,
                    "systems": [],
                    "disciplines": [],
                    "seed": 1,
                    "weak_spot": False,
                }
            ),
            odd,
        ),
    ).fetchone()
    assert row is not None
    bid = str(row[0])
    for qid in odd[:3]:
        assert answer(client, key, w, bid, qid, "A").status_code == 200  # wrong: the answer is B
    assert answer(client, key, w, bid, odd[3], "B").status_code == 200

    # a graded attempt on an exam-only question never counts toward a weak spot
    pg.execute(
        "insert into public.attempts (user_id, question_id, chosen, correct, mode)"
        " values (%s, %s, 'A', false, 'tutor')",
        (w.owner, w.mock),
    )
    res = client.post("/api/blocks/weak-spot", json={"exam": "step1", "count": 5, "seed": 3}, headers=h)
    assert res.status_code == 201, res.text
    out = res.json()
    assert [c["id"] for c in out["cells"]] == [w.sys_a]
    assert out["cells"][0]["label"] == "Alpha system"
    assert out["cells"][0]["attempts"] == 4 and out["cells"][0]["correct"] == 1
    assert out["block"]["config"]["weak_spot"] is True and out["block"]["config"]["systems"] == [w.sys_a]
    assert out["block"]["total"] == 5
    picked = [question(client, key, w, out["block"]["id"], n).json()["question"]["id"] for n in range(1, 6)]
    assert set(picked[:3]) == set(odd[:3])  # the missed questions come first
    assert len(set(picked)) == 5 and w.mock not in picked
    other = client.post("/api/blocks/weak-spot", json={"exam": "step1"}, headers=auth(key, w.other))
    assert other.status_code == 422  # nothing about another student's history


def test_exam_only_questions_never_enter_tutor_blocks(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    w, h = world, auth(key, world.owner)
    pool = client.get("/api/qbank/pool", params={"exam": "step1", "systems": [w.sys_a]}, headers=h).json()
    assert pool["total"] == 8  # the mock question is also tagged system A but is not counted
    for seed in range(1, 6):
        block = make_block(client, key, w, systems=[w.sys_a], count=8, seed=seed)
        ids = {question(client, key, w, block["id"], q["n"]).json()["question"]["id"] for q in block["questions"]}
        assert w.mock not in ids
    refused = client.post("/api/blocks/single", json={"question_id": w.mock}, headers=h)
    assert refused.status_code == 404 and refused.json()["error"]["code"] == "exam_only"
    assert_no_secrets(refused.text)


def test_flagging_alone_does_not_open_the_flashcard_gate(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    w = world
    block = make_block(client, key, w, systems=[w.sys_a], count=8, seed=2)
    ids = [question(client, key, w, block["id"], n).json()["question"]["id"] for n in range(1, 9)]
    first = w.questions[0]  # the question a shared card restates
    assert first in ids

    def cards() -> int:
        with pg.transaction():
            pg.execute("set local role authenticated")
            pg.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps({"sub": str(w.owner)}),))
            pg.execute("select set_config('request.jwt.claim.sub', %s, true)", (str(w.owner),))
            row = pg.execute("select count(*) from public.flashcards where question_id = %s", (first,)).fetchone()
        assert row is not None
        return int(row[0])

    flag = client.patch(
        f"/api/blocks/{block['id']}/attempts/{first}", json={"flagged": True}, headers=auth(key, w.owner)
    )
    assert flag.status_code == 200
    assert cards() == 0, "a flag opened the card gate"
    assert answer(client, key, w, block["id"], first, "B").status_code == 200
    assert cards() == 1


def test_single_question_blocks_are_reused_while_open(client: TestClient, key: SigningKey, world: World) -> None:
    w, h = world, auth(key, world.owner)
    a = client.post("/api/blocks/single", json={"question_id": w.questions[2]}, headers=h)
    b = client.post("/api/blocks/single", json={"question_id": w.questions[2]}, headers=h)
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] == b.json()["id"]
    # answered (finished) blocks are history: asking again starts a fresh one
    assert answer(client, key, w, a.json()["id"], w.questions[2], "B").json()["finished"] is True
    c = client.post("/api/blocks/single", json={"question_id": w.questions[2]}, headers=h)
    assert c.json()["id"] != a.json()["id"]
    other = client.post("/api/blocks/single", json={"question_id": w.questions[2]}, headers=auth(key, w.other))
    assert other.json()["id"] not in (a.json()["id"], c.json()["id"])


def test_single_question_block_for_search_hits(client: TestClient, key: SigningKey, world: World) -> None:
    w, h = world, auth(key, world.owner)
    res = client.post("/api/blocks/single", json={"question_id": w.questions[3]}, headers=h)
    assert res.status_code == 201, res.text
    block = res.json()
    assert block["total"] == 1 and block["config"]["mode"] == "tutor"
    view = question(client, key, w, block["id"], 1)
    assert view.json()["question"]["id"] == w.questions[3]
    assert_no_secrets(view.text)  # opening it through search never shows the answer
    assert client.post("/api/blocks/single", json={"question_id": "nope"}, headers=h).status_code == 404
    assert client.post("/api/blocks/single", json={"question_id": "x", "mode": "exam"}, headers=h).status_code == 422


def test_an_empty_pool_has_its_own_message(client: TestClient, key: SigningKey, world: World) -> None:
    res = client.post(
        "/api/blocks",
        json={"exam": "step1", "systems": [world.sys_a], "status": "flagged", "count": 3, "mode": "tutor"},
        headers=auth(key, world.owner),
    )
    assert res.status_code == 422
    assert res.json()["error"]["message"].startswith("No questions match these filters.")


# ── The answer-leak proof ──


def test_question_payload_never_carries_answers_before_an_attempt(
    client: TestClient, key: SigningKey, world: World
) -> None:
    w = world
    block = make_block(client, key, w, count=3)
    for n in (1, 2, 3):
        res = question(client, key, w, block["id"], n)
        assert res.status_code == 200
        assert_no_secrets(res.text)
        body = res.json()
        assert body["explanation"] is None and body["attempt"] is None and body["mode"] == "tutor"
        assert {o["label"] for o in body["question"]["options"]} == set("ABCDE")
    # block endpoints carry no question content at all
    for path in (f"/api/blocks/{block['id']}", "/api/blocks"):
        assert_no_secrets(client.get(path, headers=auth(key, w.owner)).text)
    # the course reader's embedded questions never include them either
    assert_no_secrets(client.get("/api/qbank/pool", params={"exam": "step1"}, headers=auth(key, w.owner)).text)


def test_the_explanation_appears_only_after_a_graded_attempt_on_that_question(
    client: TestClient, key: SigningKey, world: World
) -> None:
    w = world
    block = make_block(client, key, w, count=3)
    ids = [question(client, key, w, block["id"], n).json()["question"]["id"] for n in (1, 2, 3)]
    res = answer(client, key, w, block["id"], ids[0], "A")
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["correct"] is False and out["chosen"] == "A"
    exp = out["explanation"]
    assert (
        exp["answer"] == "B" and SECRET_EXPLANATION in exp["explanation_html"] and SECRET_WHY_NOT in exp["why_not_html"]
    )
    assert out["answered"] == 1 and out["total"] == 3 and out["finished"] is False
    # question 1 now explains; questions 2 and 3 still do not
    got = question(client, key, w, block["id"], 1).json()
    assert got["explanation"]["answer"] == "B" and got["attempt"] == {"chosen": "A", "seconds": 12, "correct": False}
    for n in (2, 3):
        assert_no_secrets(question(client, key, w, block["id"], n).text)
    # another student cannot see this block or its explanations at all
    assert question(client, key, w, block["id"], 1, who=w.other).status_code == 404
    assert client.get(f"/api/blocks/{block['id']}", headers=auth(key, w.other)).status_code == 404


def test_a_flag_or_a_client_inserted_row_does_not_unlock_the_explanation(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    w = world
    block = make_block(client, key, w, count=2)
    ids = [question(client, key, w, block["id"], n).json()["question"]["id"] for n in (1, 2)]
    flag = client.patch(
        f"/api/blocks/{block['id']}/attempts/{ids[0]}", json={"flagged": True}, headers=auth(key, w.owner)
    )
    assert flag.status_code == 200 and flag.json() == {"question_id": ids[0], "n": 1, "flagged": True}
    flagged = question(client, key, w, block["id"], 1)
    assert flagged.json()["flagged"] is True and flagged.json()["explanation"] is None
    assert_no_secrets(flagged.text)
    # a row a client wrote itself (PostgREST can insert: chosen set, correct not) is not a graded attempt
    pg.execute(
        "insert into public.attempts (user_id, question_id, block_id, chosen, mode) values (%s, %s, %s, 'A', 'tutor')",
        (w.owner, ids[1], block["id"]),
    )
    sneaky = question(client, key, w, block["id"], 2)
    assert sneaky.json()["explanation"] is None and sneaky.json()["attempt"] is None
    assert_no_secrets(sneaky.text)
    # answering grades the existing row with the choice it already had (the first answer stands)
    graded = answer(client, key, w, block["id"], ids[1], "B").json()
    assert graded["chosen"] == "A" and graded["correct"] is False and graded["explanation"]["answer"] == "B"
    # the flag-only row becomes the answer's row (one row per question and block)
    assert answer(client, key, w, block["id"], ids[0], "B").json()["flagged"] is True
    rows = pg.execute("select count(*) from public.attempts where block_id = %s", (block["id"],)).fetchone()
    assert rows == (2,)


def test_timed_and_exam_blocks_never_explain_during_the_block(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    w = world
    qids = w.questions[:2]
    for mode in ("timed", "exam"):
        cfg = json.dumps(
            {
                "exam": "step1",
                "mode": mode,
                "status": "all",
                "difficulty": None,
                "systems": [],
                "disciplines": [],
                "seed": 1,
                "weak_spot": False,
            }
        )
        row = pg.execute(
            "insert into public.blocks (user_id, config, question_ids, started_at)"
            " values (%s, %s::jsonb, %s, now()) returning id",
            (w.owner, cfg, qids),
        ).fetchone()
        assert row is not None
        bid = str(row[0])
        res = answer(client, key, w, bid, qids[0], "A")
        assert res.status_code == 200
        out = res.json()
        assert out["explanation"] is None and out["correct"] is None and out["finished"] is False
        again = question(client, key, w, bid, 1)
        assert_no_secrets(again.text)
        assert again.json()["attempt"] == {"chosen": "A", "seconds": 12, "correct": None}
        detail = client.get(f"/api/blocks/{bid}", headers=auth(key, w.owner)).json()
        assert detail["correct"] is None and all(q["correct"] is None for q in detail["questions"])
        listed = client.get("/api/blocks", headers=auth(key, w.owner)).json()["blocks"]
        assert next(b for b in listed if b["id"] == bid)["correct"] is None
        # even answering every question never finishes it here: timed/exam results are S09's flow
        assert answer(client, key, w, bid, qids[1], "B").json()["finished"] is False


def test_flashcards_linked_to_a_question_are_attempt_gated_by_rls(pg: psycopg.Connection[Any], world: World) -> None:
    w = world

    def visible_cards() -> int:
        with pg.transaction():
            pg.execute("set local role authenticated")
            pg.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps({"sub": str(w.owner)}),))
            pg.execute("select set_config('request.jwt.claim.sub', %s, true)", (str(w.owner),))
            row = pg.execute(
                "select count(*) from public.flashcards where question_id = %s", (w.questions[0],)
            ).fetchone()
        assert row is not None
        return int(row[0])

    assert visible_cards() == 0  # not before an attempt
    pg.execute(
        "insert into public.attempts (user_id, question_id, chosen, correct, mode) values (%s, %s, 'B', true, 'tutor')",
        (w.owner, w.questions[0]),
    )
    assert visible_cards() == 1


# ── Attempts ──


def test_the_server_grades_and_ignores_client_claims(client: TestClient, key: SigningKey, world: World) -> None:
    w = world
    block = make_block(client, key, w, count=2)
    qid = question(client, key, w, block["id"], 1).json()["question"]["id"]
    h = auth(key, w.owner)
    forged = client.post(
        f"/api/blocks/{block['id']}/attempts",
        json={"question_id": qid, "chosen": "A", "correct": True},
        headers=h,
    )
    assert forged.status_code == 422  # unknown fields are refused: a client cannot send `correct`
    assert answer(client, key, w, block["id"], qid, "Z").status_code == 422
    assert answer(client, key, w, block["id"], qid, "J").status_code == 422  # not an option of this question
    assert answer(client, key, w, block["id"], qid, "B", seconds=-1).status_code == 422
    right = answer(client, key, w, block["id"], qid, "B").json()
    assert right["correct"] is True and right["explanation"]["answer"] == "B"
    stored = client.get(f"/api/blocks/{block['id']}", headers=h).json()
    assert stored["questions"][0] == {"n": 1, "answered": True, "flagged": False, "correct": True}


def test_answering_is_idempotent_per_block_and_question(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    w = world
    block = make_block(client, key, w, count=2)
    qid = question(client, key, w, block["id"], 1).json()["question"]["id"]
    first = answer(client, key, w, block["id"], qid, "A", seconds=30).json()
    second = answer(client, key, w, block["id"], qid, "B", seconds=99).json()  # a replay with another choice
    assert first == second and second["chosen"] == "A" and second["correct"] is False and second["seconds"] == 30
    assert second["answered"] == 1
    count = pg.execute("select count(*) from public.attempts where block_id = %s", (block["id"],)).fetchone()
    assert count == (1,)
    # the database refuses a second row too, whoever writes it
    with pytest.raises(psycopg.errors.UniqueViolation):
        pg.execute(
            "insert into public.attempts (user_id, question_id, block_id, chosen, mode)"
            " values (%s, %s, %s, 'C', 'tutor')",
            (w.owner, qid, block["id"]),
        )


def test_attempt_for_a_question_outside_the_block_or_another_users_block(
    client: TestClient, key: SigningKey, world: World
) -> None:
    w = world
    block = make_block(client, key, w, count=2, systems=[w.sys_a])
    inside = {question(client, key, w, block["id"], n).json()["question"]["id"] for n in (1, 2)}
    outside = next(q for q in w.questions if q not in inside)
    assert answer(client, key, w, block["id"], outside, "B").status_code == 404
    mine = next(iter(inside))
    stolen = client.post(
        f"/api/blocks/{block['id']}/attempts", json={"question_id": mine, "chosen": "B"}, headers=auth(key, w.other)
    )
    assert stolen.status_code == 404
    assert (
        client.patch(
            f"/api/blocks/{block['id']}/attempts/{mine}", json={"flagged": True}, headers=auth(key, w.other)
        ).status_code
        == 404
    )
    assert question(client, key, w, block["id"], 3).status_code == 404  # no such position
    assert question(client, key, w, block["id"], 0).status_code == 422


def test_a_tutor_block_finishes_with_its_last_answer(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    w = world
    block = make_block(client, key, w, count=2, systems=[w.sys_b])
    ids = [question(client, key, w, block["id"], n).json()["question"]["id"] for n in (1, 2)]
    assert answer(client, key, w, block["id"], ids[0], "B").json()["finished"] is False
    last = answer(client, key, w, block["id"], ids[1], "A").json()
    assert last["finished"] is True and last["answered"] == 2
    detail = client.get(f"/api/blocks/{block['id']}", headers=auth(key, w.owner)).json()
    assert detail["score"] == 50.0 and detail["correct"] == 1 and detail["finished_at"]
    row = pg.execute(
        "select score, finished_at is not null from public.blocks where id = %s", (block["id"],)
    ).fetchone()
    assert row is not None and float(row[0]) == 50.0 and row[1] is True


def test_flagging_toggles_and_lists(client: TestClient, key: SigningKey, world: World) -> None:
    w, h = world, auth(key, world.owner)
    block = make_block(client, key, w, count=2)
    ids = [question(client, key, w, block["id"], n).json()["question"]["id"] for n in (1, 2)]
    url = f"/api/blocks/{block['id']}/attempts/{ids[1]}"
    assert client.patch(url, json={"flagged": True}, headers=h).json()["flagged"] is True
    state = client.get(f"/api/blocks/{block['id']}", headers=h).json()["questions"]
    assert [q["flagged"] for q in state] == [False, True] and [q["answered"] for q in state] == [False, False]
    assert client.patch(url, json={"flagged": False}, headers=h).json()["flagged"] is False
    assert client.patch(url, json={"flagged": True, "chosen": "B"}, headers=h).status_code == 422  # flag only
    assert client.patch(url, json={}, headers=h).status_code == 422


def test_past_blocks_newest_first_with_progress(client: TestClient, key: SigningKey, world: World) -> None:
    w, h = world, auth(key, world.owner)
    first = make_block(client, key, w, count=2, seed=1)
    second = make_block(client, key, w, count=3, seed=2)
    qid = question(client, key, w, second["id"], 1).json()["question"]["id"]
    answer(client, key, w, second["id"], qid, "B")
    blocks = client.get("/api/blocks", headers=h).json()["blocks"]
    ids = [b["id"] for b in blocks]
    assert ids.index(second["id"]) < ids.index(first["id"])
    s = next(b for b in blocks if b["id"] == second["id"])
    assert s["total"] == 3 and s["answered"] == 1 and s["correct"] == 1 and s["config"]["mode"] == "tutor"
    other = client.get("/api/blocks", headers=auth(key, w.other)).json()["blocks"]
    assert first["id"] not in [b["id"] for b in other]
    assert client.get("/api/blocks", params={"limit": 0}, headers=h).status_code == 422


def test_question_view_links_the_lesson_and_topics(client: TestClient, key: SigningKey, world: World) -> None:
    w = world
    block = make_block(client, key, w, systems=[w.sys_a], count=8, seed=1)
    seen = {}
    for q in block["questions"]:
        body = question(client, key, w, block["id"], q["n"]).json()
        seen[body["question"]["id"]] = body
    first = seen[w.questions[0]]
    assert first["lesson"] == {"chapter_id": w.chapter, "title": "Alpha chapter"}
    assert "Alpha system" in first["question"]["topics"]
    assert seen[w.questions[1]]["lesson"] is None
    assert "Pathology" in seen[w.questions[1]]["question"]["topics"]


# ── Lab values ──


def test_lab_values_come_from_app_config_with_their_sources(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    added = [
        r[0]
        for r in pg.execute(
            "insert into public.sources (id, name, license, url) values"
            " ('naadi-original-v1', 'Naadi original', 'proprietary-naadi', null),"
            " ('nbme-lab-values', 'NBME Laboratory Values', 'link-only', 'https://www.nbme.org/?p=217124')"
            " on conflict (id) do nothing returning id"
        ).fetchall()
    ]
    try:
        res = client.get("/api/lab-values", headers=auth(key, world.owner))
        assert res.status_code == 200, res.text
        body = res.json()
        sodium = next(r for g in body["groups"] for r in g["rows"] if r["analyte"] == "Sodium")
        assert sodium["range"] == "136-145 mEq/L" and sodium["si"] == "136-145 mmol/L"
        assert body["official"]["id"] == "nbme-lab-values" and body["official"]["url"].startswith("https://")
        assert body["source"]["id"] == "naadi-original-v1"
        assert len(body["groups"]) >= 5 and all(g["rows"] for g in body["groups"])
    finally:
        if added:
            pg.execute("delete from public.sources where id = any(%s)", (added,))


# ── No other code path reads the answer columns ──

SECRET_COLUMN = re.compile(r"\b(answer_html|explanation_html|why_not_html|educational_objective)\b")
ALLOWED = {"qbank/routes.py", "qbank/models.py", "seed", "agent"}


def test_only_the_qbank_and_the_seed_read_answer_columns() -> None:
    """A static guard: any new module that selects the secret columns has to be added here on purpose (and reviewed
    against HANDOFF 6.3). Search indexes stem and options only; flashcards are attempt-gated by RLS."""
    root = Path(__file__).resolve().parents[2] / "naadi_api"
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if any(rel == a or rel.startswith(a + "/") for a in ALLOWED):
            continue
        if SECRET_COLUMN.search(path.read_text(encoding="utf-8")):
            offenders.append(rel)
    assert offenders == [], f"modules that mention answer columns: {offenders}"
