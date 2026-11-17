"""The human review queue over HTTP against the real database: admin-only, one approval publishes behind the three
gates, the optional second reviewer is blind, disagreement is adjudicated, seed items go through the same queue,
and nothing in review is visible to a student."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from naadi_api.config_api.schemas import AuthoringRules
from tests.auth.jwt_helpers import SigningKey
from tests.db.rls import DB_MARKS

from .conftest import GOOD_STEM, Make, World, head, review_body, run_as, solver_json

pytestmark = DB_MARKS
P = "/api/admin/review"
NEGATIVE_STEM = GOOD_STEM.replace(
    "Which of the following is the most likely causal organism?", "Which of the following is NOT a causal organism?"
)


def post_review(client: TestClient, key: SigningKey, w: World, who: str, qid: str, **kw: Any) -> Any:
    return client.post(f"{P}/items/{qid}/reviews", json=review_body(**kw), headers=head(key, w, who))


def status_of(pg: psycopg.Connection[Any], qid: str) -> str:
    row = pg.execute("select status from public.questions where id = %s", (qid,)).fetchone()
    assert row is not None
    return str(row[0])


def two_approvals(monkeypatch: pytest.MonkeyPatch, n: int = 2) -> None:
    async def rules(request: Any) -> AuthoringRules:
        return AuthoringRules(required_approvals=n)

    monkeypatch.setattr("naadi_api.authoring.routes._rules", rules)


# ── access ──


def test_only_admins_reach_the_review_api(
    client: TestClient, key: SigningKey, world: World, make_item: Make, pg: psycopg.Connection[Any]
) -> None:
    qid = make_item()
    probes = [
        ("get", f"{P}/summary", None),
        ("get", f"{P}/items", None),
        ("get", f"{P}/items/{qid}", None),
        ("post", f"{P}/items/{qid}/reviews", review_body()),
        ("post", f"{P}/items/{qid}/checks", {"solver": False}),
        ("post", f"{P}/items/{qid}/resubmit", None),
        ("post", f"{P}/items/{qid}/psychometrics", {"action": "keep"}),
        ("get", f"{P}/gaps", None),
        ("get", f"{P}/taxonomy?exam=step1", None),
        ("post", f"{P}/runs", {"exam": "step1", "topic_id": world.topic}),
        ("get", f"{P}/runs", None),
        ("post", f"{P}/psychometrics/run", None),
    ]
    for method, path, body in probes:
        anon = client.request(method, path, json=body)
        student = client.request(method, path, json=body, headers=head(key, world, "student"))
        assert anon.status_code == 401, (method, path)
        assert student.status_code == 403, (method, path)
    assert status_of(pg, qid) == "in_review"  # nothing happened


# ── detail ──


def test_the_item_detail_carries_the_key_findings_gates_and_provenance(
    client: TestClient, key: SigningKey, world: World, make_item: Make
) -> None:
    qid = make_item()
    r = client.get(f"{P}/items/{qid}", headers=head(key, world, "a"))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["answer"] == "A" and d["origin"] == "ai" and d["status"] == "in_review"
    assert d["author"]["model"] == "nvidia/nemotron-test" and d["author"]["excerpt_ids"] == ["s#0"]
    assert d["solver"]["independent"] is True and d["solver"]["agrees"] is True
    assert d["gates"] == {
        "lint_ok": True,
        "source_ok": True,
        "cross_check_ok": True,
        "independent": True,
        "blockers": [],
    }
    assert d["resolution"] == {"state": "awaiting", "needed": 1, "submitted": 0, "round": 1}
    assert d["can_review"] == {"allowed": True, "reason": None}
    assert {t["kind"] for t in d["taxonomy"]} == {"system", "discipline", "competency", "topic"}
    assert d["reviews"] == [] and d["hidden_reviews"] == 0 and d["required_approvals"] == 1
    assert client.get(f"{P}/items/nope", headers=head(key, world, "a")).json()["error"]["code"] == "item_not_found"


def test_the_queue_filters(client: TestClient, key: SigningKey, world: World, make_item: Make) -> None:
    ai = make_item("ai")
    needs = make_item("needs", status="auto_checked", solver=solver_json(independent=False))
    seed = make_item("seed", status="published", ai=False)
    done = make_item("done", status="published", ai=False, review={"humanExpertReviewed": True})
    flagged = make_item(
        "flag",
        status="published",
        ai=False,
        review={"humanExpertReviewed": True},
        stats={"n": 30, "flags": ["p_too_low"], "computedAt": "t"},
    )

    def ids(flt: str) -> set[str]:
        r = client.get(f"{P}/items?filter={flt}&limit=200", headers=head(key, world, "a"))
        assert r.status_code == 200, r.text
        return {i["id"] for i in r.json()["items"]}

    assert {ai, needs} <= ids("ai_drafts") and not ({seed, done, flagged} & ids("ai_drafts"))
    assert needs in ids("needs_check") and ai not in ids("needs_check")
    assert seed in ids("seed_spot_check") and not ({ai, done, flagged} & ids("seed_spot_check"))
    assert flagged in ids("psychometric_flags") and done not in ids("psychometric_flags")
    assert {ai, needs, seed, flagged} <= ids("all") and done not in ids("all")
    s = client.get(f"{P}/summary", headers=head(key, world, "a")).json()
    assert (
        s["ai_drafts"] >= 2
        and s["seed_spot_check"] >= 1
        and s["psychometric_flags"] >= 1
        and s["required_approvals"] == 1
    )
    row = next(
        i
        for i in client.get(f"{P}/items?filter=all&limit=200", headers=head(key, world, "a")).json()["items"]
        if i["id"] == ai
    )
    assert row["origin"] == "ai" and row["state"] == "awaiting" and row["mine_submitted"] is False
    assert "Escherichia" not in row["stem_preview"] or len(row["stem_preview"]) <= 160


# ── one approval publishes ──


def test_one_admin_approval_publishes_and_re_embeds(
    client: TestClient, key: SigningKey, world: World, make_item: Make, pg: psycopg.Connection[Any]
) -> None:
    qid = make_item()
    r = post_review(client, key, world, "a", qid)
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "published" and r.json()["item"]["status"] == "published"
    row = pg.execute("select status, review from public.questions where id = %s", (qid,)).fetchone()
    assert row is not None and row[0] == "published"
    review = row[1]
    assert review["humanExpertReviewed"] is True and review["status"] == "ai-authored, human-reviewed"
    assert [h["reviewerId"] for h in review["humanReviewers"]] == [str(world.users["a"])]
    assert review["humanReviewers"][0]["decision"] == "approve" and review["publishedAt"]
    saved = pg.execute(
        "select decision, round, ratings->>'accuracy' from public.question_reviews where question_id = %s", (qid,)
    ).fetchall()
    assert saved == [("approve", 1, "correct")]
    # the round is closed: nobody reviews it again
    assert post_review(client, key, world, "b", qid).status_code == 409


def test_every_part_c_rating_is_required_to_review(
    client: TestClient, key: SigningKey, world: World, make_item: Make
) -> None:
    qid = make_item()
    for missing in ("accuracy", "single_best_answer", "cognitive_level", "difficulty", "taxonomy_ok"):
        body = review_body()
        del body["ratings"][missing]
        r = client.post(f"{P}/items/{qid}/reviews", json=body, headers=head(key, world, "a"))
        assert r.status_code == 422, missing
    bad = client.post(
        f"{P}/items/{qid}/reviews", json=review_body(ratings={"flaws": ["made_up"]}), headers=head(key, world, "a")
    )
    assert bad.status_code == 422
    assert (
        client.post(f"{P}/items/{qid}/reviews", json={"decision": "approve"}, headers=head(key, world, "a")).status_code
        == 422
    )


def test_an_approval_that_contradicts_the_ratings_is_refused(
    client: TestClient, key: SigningKey, world: World, make_item: Make
) -> None:
    qid = make_item()
    r = post_review(client, key, world, "a", qid, ratings={"accuracy": "incorrect"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "approve_contradicts_ratings"
    r = post_review(client, key, world, "a", qid, ratings={"single_best_answer": False})
    assert r.status_code == 422


def test_each_gate_blocks_publication_with_its_own_code(
    client: TestClient, key: SigningKey, world: World, make_item: Make, pg: psycopg.Connection[Any]
) -> None:
    def blockers(r: Any) -> list[str]:
        assert r.status_code == 409 and r.json()["error"]["code"] == "publish_blocked", r.text
        return [d["msg"] for d in r.json()["error"]["details"]]

    lint = make_item("lint", stem=NEGATIVE_STEM)
    assert blockers(post_review(client, key, world, "a", lint)) == ["lint_errors"]
    disagrees = make_item("dis", solver=solver_json(agrees=False))
    assert blockers(post_review(client, key, world, "a", disagrees)) == ["model_cross_check_failed"]
    same_family = make_item("fam", status="auto_checked", solver=solver_json(independent=False))
    assert blockers(post_review(client, key, world, "a", same_family)) == ["review_not_independent"]
    no_source = make_item("src")
    pg.execute("update public.sources set allow_ai_ingest = false where id = %s", (world.source,))
    try:
        assert "source_check_failed" in blockers(post_review(client, key, world, "a", no_source))
    finally:
        pg.execute("update public.sources set allow_ai_ingest = true where id = %s", (world.source,))
    assert {status_of(pg, q) for q in (lint, disagrees, same_family, no_source)} == {"in_review", "auto_checked"}
    # revise and reject do not need the gates: a reviewer can always send an item back
    assert post_review(client, key, world, "a", disagrees, decision="revise").json()["outcome"] == "revise"


# ── revise, reject, resubmit ──


def test_revise_returns_the_item_to_draft_and_resubmit_opens_a_new_round(
    client: TestClient, key: SigningKey, world: World, make_item: Make, pg: psycopg.Connection[Any]
) -> None:
    qid = make_item(stats={"n": 5})
    r = post_review(
        client, key, world, "a", qid, decision="revise", ratings={"flaws": ["logical_cue"]}, note="Tighten the stem"
    )
    assert r.json()["outcome"] == "revise" and status_of(pg, qid) == "draft"
    assert post_review(client, key, world, "b", qid).status_code == 409  # a draft is not reviewable
    again = client.post(f"{P}/items/{qid}/resubmit", headers=head(key, world, "a"))
    assert again.status_code == 200, again.text
    d = again.json()
    assert d["status"] == "in_review" and d["resolution"]["round"] == 2 and d["resolution"]["state"] == "awaiting"
    assert pg.execute("select stats from public.questions where id = %s", (qid,)).fetchone() == ({},)  # stats reset
    assert [(x["round"], x["decision"], x["ratings"]["flaws"]) for x in d["reviews"]] == [
        (1, "revise", ["logical_cue"])
    ]  # history
    assert post_review(client, key, world, "a", qid).json()["outcome"] == "published"  # same admin, new round


def test_resubmit_needs_a_draft_that_passes_the_linter(
    client: TestClient, key: SigningKey, world: World, make_item: Make
) -> None:
    ok = make_item("ok", status="in_review")
    assert client.post(f"{P}/items/{ok}/resubmit", headers=head(key, world, "a")).status_code == 409
    bad = make_item("bad", status="draft", stem=NEGATIVE_STEM)
    r = client.post(f"{P}/items/{bad}/resubmit", headers=head(key, world, "a"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "publish_blocked"


def test_reject_retires_the_item(
    client: TestClient, key: SigningKey, world: World, make_item: Make, pg: psycopg.Connection[Any]
) -> None:
    qid = make_item()
    r = post_review(
        client, key, world, "a", qid, decision="reject", ratings={"accuracy": "incorrect", "single_best_answer": False}
    )
    assert r.json()["outcome"] == "rejected" and status_of(pg, qid) == "retired"


# ── the optional second reviewer ──


def test_a_reviewer_can_ask_for_a_second_review_and_the_two_are_blind(
    client: TestClient, key: SigningKey, world: World, make_item: Make, pg: psycopg.Connection[Any]
) -> None:
    qid = make_item()
    first = post_review(client, key, world, "a", qid, request_second=True)
    assert first.json()["outcome"] == "recorded" and status_of(pg, qid) == "in_review"
    d_a = client.get(f"{P}/items/{qid}", headers=head(key, world, "a")).json()
    assert d_a["resolution"] == {"state": "awaiting", "needed": 2, "submitted": 1, "round": 1}
    assert [r["is_mine"] for r in d_a["reviews"]] == [True] and d_a["can_review"] == {
        "allowed": False,
        "reason": "already_reviewed_by_you",
    }
    d_b = client.get(f"{P}/items/{qid}", headers=head(key, world, "b")).json()
    assert d_b["reviews"] == [] and d_b["hidden_reviews"] == 1 and d_b["can_review"]["allowed"] is True  # blind
    assert post_review(client, key, world, "a", qid).status_code == 409  # not twice
    second = post_review(client, key, world, "b", qid)
    assert second.json()["outcome"] == "published" and status_of(pg, qid) == "published"
    humans = pg.execute("select review->'humanReviewers' from public.questions where id = %s", (qid,)).fetchone()
    assert humans is not None and len(humans[0]) == 2
    both = client.get(f"{P}/items/{qid}", headers=head(key, world, "b")).json()
    assert len(both["reviews"]) == 2 and both["hidden_reviews"] == 0


def test_two_approvals_are_required_when_configured_and_disagreement_is_adjudicated(
    client: TestClient,
    key: SigningKey,
    world: World,
    make_item: Make,
    pg: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    two_approvals(monkeypatch)
    qid = make_item()
    assert post_review(client, key, world, "a", qid).json()["outcome"] == "recorded"
    assert status_of(pg, qid) == "in_review"
    split = post_review(client, key, world, "b", qid, decision="reject", ratings={"accuracy": "incorrect"})
    assert split.json()["outcome"] == "adjudication" and status_of(pg, qid) == "in_review"
    detail = client.get(f"{P}/items/{qid}", headers=head(key, world, "c")).json()
    assert detail["resolution"]["state"] == "adjudication" and len(detail["reviews"]) == 2  # the adjudicator sees both
    for who in ("a", "b"):  # neither reviewer may adjudicate their own item
        assert post_review(client, key, world, who, qid).status_code == 409
    final = post_review(client, key, world, "c", qid)
    assert final.json()["outcome"] == "published" and status_of(pg, qid) == "published"
    rows = pg.execute(
        "select adjudication from public.question_reviews where question_id = %s order by id", (qid,)
    ).fetchall()
    assert rows == [(False,), (False,), (True,)]


def test_two_admins_agreeing_on_reject_retire_the_item(
    client: TestClient,
    key: SigningKey,
    world: World,
    make_item: Make,
    pg: psycopg.Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    two_approvals(monkeypatch)
    qid = make_item()
    bad = {"accuracy": "incorrect"}
    post_review(client, key, world, "a", qid, decision="reject", ratings=bad)
    assert post_review(client, key, world, "b", qid, decision="reject", ratings=bad).json()["outcome"] == "rejected"
    assert status_of(pg, qid) == "retired"


# ── seed items ──


def test_seed_items_enter_the_same_queue_and_need_the_cross_check(
    client: TestClient, key: SigningKey, world: World, make_item: Make, pg: psycopg.Connection[Any]
) -> None:
    qid = make_item("seed", status="published", ai=False, objective=None)
    d = client.get(f"{P}/items/{qid}", headers=head(key, world, "a")).json()
    assert d["origin"] == "seed" and d["status"] == "published" and d["gates"]["cross_check_ok"] is False
    assert d["can_review"]["allowed"] is True
    assert not any(
        f["rule"] == "S03" and f["severity"] == "error" for f in d["lint"]
    )  # no objective: a warning for seeds
    blocked = post_review(client, key, world, "a", qid)
    assert blocked.status_code == 409 and "model_cross_check_failed" in [
        x["msg"] for x in blocked.json()["error"]["details"]
    ]
    world.gateway.queue_agreeing("A")
    checked = client.post(f"{P}/items/{qid}/checks", json={"solver": True}, headers=head(key, world, "a"))
    assert checked.status_code == 200, checked.text
    assert (
        checked.json()["gates"]["blockers"] == [] and checked.json()["solver"]["model"] == "particle-ai/particle-test"
    )
    approved = post_review(client, key, world, "a", qid)
    assert approved.json()["outcome"] == "published"
    review = pg.execute("select review from public.questions where id = %s", (qid,)).fetchone()
    assert (
        review is not None
        and review[0]["humanExpertReviewed"] is True
        and review[0]["status"] == "seed, human-reviewed"
    )
    assert (
        client.get(f"{P}/items/{qid}", headers=head(key, world, "a")).json()["can_review"]["reason"]
        == "already_reviewed"
    )


def test_rejecting_a_seed_item_retires_it_from_the_pool(
    client: TestClient, key: SigningKey, world: World, make_item: Make, pg: psycopg.Connection[Any]
) -> None:
    qid = make_item("seed2", status="published", ai=False)
    r = post_review(client, key, world, "a", qid, decision="reject", ratings={"accuracy": "incorrect"})
    assert r.json()["outcome"] == "rejected" and status_of(pg, qid) == "retired"


def test_a_solver_that_disagrees_keeps_an_item_unpublishable(
    client: TestClient, key: SigningKey, world: World, make_item: Make
) -> None:
    qid = make_item("seed3", status="published", ai=False)
    world.gateway.replies.append('{"choice": "C", "confidence": 0.5, "defensibility": {}}')
    r = client.post(f"{P}/items/{qid}/checks", json={"solver": True}, headers=head(key, world, "a"))
    assert r.json()["solver"]["agrees"] is False and r.json()["gates"]["blockers"] == ["model_cross_check_failed"]
    assert r.json()["solver_findings"][0]["rule"] == "B01"


def test_a_same_family_recheck_cannot_unblock_an_item(
    client: TestClient, key: SigningKey, world: World, make_item: Make
) -> None:
    qid = make_item("fam", status="auto_checked", solver=solver_json(independent=False))
    world.gateway.queue_agreeing()
    world.gateway.model = "nvidia/another-nemotron"  # the same family as the author
    r = client.post(f"{P}/items/{qid}/checks", json={"solver": True}, headers=head(key, world, "a"))
    assert r.json()["status"] == "auto_checked" and r.json()["gates"]["blockers"] == ["review_not_independent"]
    world.gateway.model = "particle-ai/particle-test"
    world.gateway.queue_agreeing()
    r = client.post(f"{P}/items/{qid}/checks", json={"solver": True}, headers=head(key, world, "a"))
    assert r.json()["status"] == "in_review" and r.json()["gates"]["blockers"] == []  # an independent check advances it


# ── students never see items under review ──


def test_students_cannot_see_drafts_or_items_in_review_through_any_endpoint(
    client: TestClient, key: SigningKey, world: World, make_item: Make, pg: psycopg.Connection[Any]
) -> None:
    ids = {s: make_item(s, status=s) for s in ("draft", "auto_checked", "in_review", "retired")}
    h = head(key, world, "student")
    for qid in ids.values():
        assert client.post("/api/blocks/single", json={"question_id": qid}, headers=h).status_code == 404, qid
    pool = client.get(f"/api/qbank/pool?exam=step1&systems={world.system}", headers=h)
    assert pool.status_code == 200 and pool.json()["total"] == 0  # nothing in the Builder's counts
    build = client.post("/api/blocks", json={"exam": "step1", "count": 5, "systems": [world.system]}, headers=h)
    assert build.status_code in (404, 422), build.text  # nothing to build a block from
    with pg.transaction():
        pg.execute("set local role authenticated")
        pg.execute(
            "select set_config('request.jwt.claims', %s, true)",
            (f'{{"sub": "{world.student}", "role": "authenticated"}}',),
        )
        seen = pg.execute("select id from public.questions where id = any(%s)", (list(ids.values()),)).fetchall()
        pg.execute("reset role")
    assert seen == []
    published = make_item("pub", status="published", ai=False, review={"humanExpertReviewed": True})
    r = client.post("/api/blocks/single", json={"question_id": published}, headers=h)
    assert r.status_code == 201, r.text
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        run_as(pg, "authenticated", "select review, stats from public.questions limit 1")  # hidden columns


def test_publishing_through_review_puts_the_item_in_the_pool(
    client: TestClient, key: SigningKey, world: World, make_item: Make
) -> None:
    qid = make_item()
    h = head(key, world, "student")
    pool = f"/api/qbank/pool?exam=step1&systems={world.system}"
    assert client.post("/api/blocks/single", json={"question_id": qid}, headers=h).status_code == 404
    assert client.get(pool, headers=h).json()["total"] == 0
    assert post_review(client, key, world, "a", qid).json()["outcome"] == "published"
    assert client.get(pool, headers=h).json()["total"] == 1
    after = client.post("/api/blocks/single", json={"question_id": qid}, headers=h)
    assert after.status_code == 201, after.text
    assert '"answer"' not in after.text and "explanation" not in after.text  # still no key before an attempt
