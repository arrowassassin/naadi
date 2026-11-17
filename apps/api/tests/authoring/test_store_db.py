"""The pipeline's database side and the admin trigger, against the real database: coverage gaps, grounding
excerpts (only AI-ingestible, published, shared sources), the recorded run, an admin run end to end, the nightly
statistics and the RLS of the new tables."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from naadi_api.authoring import psychometrics_job
from naadi_api.authoring.store import PgAuthoringStore, RunRecord
from naadi_api.config_api.schemas import AuthoringRules
from naadi_api.db import Database
from tests.auth.jwt_helpers import SigningKey
from tests.db.fixtures import insert_row
from tests.db.rls import DB_MARKS, create_auth_user

from .conftest import Make, World, head, run_as
from .test_pipeline import draft_json

pytestmark = DB_MARKS


@pytest.fixture
def store(url: str) -> PgAuthoringStore:
    return PgAuthoringStore(Database(SecretStr(url)))


async def test_gaps_list_topics_under_the_target_fewest_first(
    store: PgAuthoringStore, world: World, make_item: Make
) -> None:
    gaps = await store.gaps(["step1"], 3, 500)
    mine = [g for g in gaps if g.topic_id == world.topic]
    assert [(g.exam, g.published, g.pending) for g in mine] == [("step1", 0, 0)]
    make_item("p1", status="published", ai=False)
    make_item("pend", status="in_review")
    again = next(g for g in await store.gaps(["step1"], 3, 500) if g.topic_id == world.topic)
    assert (again.published, again.pending) == (1, 1)  # a pending draft counts toward not authoring it twice
    make_item("p2", status="published", ai=False)
    assert world.topic not in {g.topic_id for g in await store.gaps(["step1"], 3, 500)}  # 2 published + 1 pending
    assert world.topic in {g.topic_id for g in await store.gaps(["step1"], 4, 500)}
    assert world.topic not in {g.topic_id for g in await store.gaps(["step1"], 4, 500, skip_topics=[world.topic])}
    assert world.topic not in {g.topic_id for g in await store.gaps(["step2ck"], 4, 500)}  # the exam must match


async def test_common_second_is_the_most_used_discipline_of_the_topic(
    store: PgAuthoringStore, world: World, make_item: Make
) -> None:
    assert await store.common_second("step1", world.topic) is None
    make_item("p1", status="published", ai=False)
    assert await store.common_second("step1", world.topic) == world.discipline


@pytest.fixture
def library(pg: psycopg.Connection[Any], world: World) -> Iterator[dict[str, str]]:
    """Sections with an indexed chunk each: one grounding-eligible, and one for every way of not being."""
    owner = world.student
    bad_src = f"t{world.tag}-nosrc"
    insert_row(pg, "sources", {"id": bad_src, "name": "x", "license": "CC-BY-NC-ND-4.0", "allow_ai_ingest": False})
    ids = {}
    for key, status, kind, src, own in (
        ("ok", "published", "chapter", world.source, None),
        ("draft", "draft", "chapter", world.source, None),
        ("noai", "published", "chapter", bad_src, None),
        ("mock", "published", "mock_exam", world.source, None),
        ("private", "published", "user_upload", world.source, owner),
    ):
        cid = f"t{world.tag}-c-{key}"
        insert_row(
            pg,
            "chapters",
            {
                "id": cid,
                "exam": "step1",
                "kind": kind,
                "title": key,
                "status": status,
                "source_id": src,
                "owner_user_id": own,
            },
        )
        sid = f"{cid}-s1"
        insert_row(
            pg,
            "chapter_sections",
            {"id": sid, "chapter_id": cid, "sort": 1, "heading": key, "taxonomy_ids": [world.topic]},
        )
        insert_row(
            pg,
            "embeddings",
            {
                "owner_user_id": own,
                "ref_type": "section",
                "ref_id": sid,
                "chunk_index": 0,
                "content": f"zqxvcystitis zqxvlactose indole {key}",
            },
        )
        ids[key] = sid
    yield ids
    pg.execute("delete from public.embeddings where ref_id like %s", (f"t{world.tag}-%",))
    pg.execute("delete from public.chapters where id like %s", (f"t{world.tag}-c-%",))
    pg.execute("delete from public.sources where id = %s", (bad_src,))


async def test_excerpts_come_only_from_published_shared_ai_ingestible_sources(
    store: PgAuthoringStore, world: World, library: dict[str, str]
) -> None:
    ex = await store.excerpts(world.taxonomy_ids, "zqxvcystitis zqxvlactose", 10, 1200)
    assert [e.id for e in ex] == [f"{library['ok']}#0"]
    assert ex[0].source_id == world.source and ex[0].text == "zqxvcystitis zqxvlactose indole ok"
    assert await store.excerpts(world.taxonomy_ids, "zqxvcystitis", 10, 12) == [
        type(ex[0])(f"{library['ok']}#0", world.source, "zqxvcystitis", library["ok"])
    ]
    assert await store.excerpts([], "", 10, 100) == []


async def test_a_run_and_its_item_are_recorded(
    store: PgAuthoringStore, world: World, pg: psycopg.Connection[Any]
) -> None:
    row = {
        "exam": "step1",
        "stem_html": "<p>s?</p>",
        "options": [{"label": "A", "html": "a"}],
        "answer": "A",
        "answer_html": None,
        "explanation_html": "<p>e</p>",
        "why_not_html": "w",
        "educational_objective": "o",
        "difficulty": "easy",
        "taxonomy_ids": world.taxonomy_ids,
        "source_id": world.source,
    }
    qid = f"t{world.tag}-ins"
    await store.insert_item(qid, row, "in_review", {"authorModel": {"model": "m"}, "round": 1})
    got = pg.execute(
        "select status, review->'authorModel'->>'model', tags from public.questions where id = %s", (qid,)
    ).fetchone()
    assert got == ("in_review", "m", ["AI-authored"])
    run = RunRecord(
        "cron",
        {"topic": world.topic},
        "obj",
        "easy",
        llm_calls=4,
        outcome="in_review",
        question_id=qid,
        excerpt_ids=["x#0"],
    )
    rid = await store.record_run(run)
    saved = pg.execute(
        "select trigger, llm_calls, outcome, excerpt_ids, question_id from public.authoring_runs where id = %s", (rid,)
    ).fetchone()
    assert saved == ("cron", 4, "in_review", ["x#0"], qid)
    assert await store.pending_review_count() >= 1
    assert await store.calls_today("cron") >= 4
    for _ in range(3):
        await store.record_run(RunRecord("cron", {"topic": world.topic}, "o", "easy", outcome="discarded_lint"))
    assert world.topic in await store.failed_topics()


# ── the admin trigger, end to end ──


def wait_done(client: TestClient, key: SigningKey, world: World, job_id: str, timeout: float = 20) -> dict[str, Any]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        runs = client.get("/api/admin/review/runs", headers=head(key, world, "a")).json()
        job = next(j for j in runs["active"] if j["id"] == job_id)
        if job["status"] != "running":
            return job  # type: ignore[no-any-return]
        time.sleep(0.1)
    raise AssertionError("the run did not finish")


def test_an_admin_run_drafts_an_item_into_the_review_queue(
    client: TestClient, key: SigningKey, world: World, library: dict[str, str], pg: psycopg.Connection[Any]
) -> None:
    gw = world.gateway
    draft = json.loads(draft_json())
    draft["citations"] = [{"excerpt_id": f"{library['ok']}#0", "claim": "zqxvcystitis zqxvlactose indole ok"}]
    draft["explanation"] = "Zqxvcystitis zqxvlactose indole ok."
    gw.queue_draft(json.dumps(draft))
    gw.queue_agreeing("A")
    gw.delay = 0.4  # hold the first run open long enough to try a second
    body = {
        "exam": "step1",
        "topic_id": world.topic,
        "second_id": world.discipline,
        "competency_id": world.competency,
        "objective": "zqxvcystitis zqxvlactose",
        "difficulty": "medium",
        "count": 1,
    }
    r = client.post("/api/admin/review/runs", json=body, headers=head(key, world, "a"))
    assert r.status_code == 202, r.text
    job = r.json()
    again = client.post("/api/admin/review/runs", json=body, headers=head(key, world, "b"))
    assert again.status_code == 409 and again.json()["error"]["code"] == "run_active"
    done = wait_done(client, key, world, job["id"])
    assert done["status"] == "done" and done["outcomes"] == ["in_review"], done
    run = pg.execute(
        "select trigger, started_by, outcome, question_id, llm_calls, excerpt_ids from public.authoring_runs"
        " where cell->>'topic' = %s",
        (world.topic,),
    ).fetchone()
    assert (
        run is not None
        and run[:3] == ("admin", world.users["a"], "in_review")
        and run[4] == 4
        and run[5] == [f"{library['ok']}#0"]
    )
    qid = run[3]
    assert qid.startswith("ai-")
    pg.execute("update public.questions set id = id where id = %s", (qid,))
    queue = client.get("/api/admin/review/items?filter=ai_drafts&limit=200", headers=head(key, world, "a")).json()[
        "items"
    ]
    assert qid in {i["id"] for i in queue}
    detail = client.get(f"/api/admin/review/items/{qid}", headers=head(key, world, "a")).json()
    assert detail["status"] == "in_review" and detail["taxonomy"] and detail["gates"]["blockers"] == []
    assert detail["author"]["excerpt_ids"] == [f"{library['ok']}#0"]
    pg.execute("delete from public.questions where id = %s", (qid,))


def test_a_run_without_grounding_records_it_and_calls_no_model(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    body = {
        "exam": "step1",
        "topic_id": world.topic,
        "second_id": world.discipline,
        "competency_id": world.competency,
        "objective": "Zzyzx quux",  # words no seeded section contains
    }
    job = client.post("/api/admin/review/runs", json=body, headers=head(key, world, "a")).json()
    assert wait_done(client, key, world, job["id"])["outcomes"] == ["no_excerpts"]
    assert world.gateway.calls == 0


def test_a_cell_outside_the_taxonomy_is_refused(client: TestClient, key: SigningKey, world: World) -> None:
    r = client.post(
        "/api/admin/review/runs", json={"exam": "step1", "topic_id": "topic.nope"}, headers=head(key, world, "a")
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "cell_invalid"
    tax = client.get("/api/admin/review/taxonomy?exam=step1", headers=head(key, world, "a")).json()
    assert world.topic in {t["id"] for t in tax["topics"]} and world.system in {t["id"] for t in tax["systems"]}
    gaps = client.get("/api/admin/review/gaps", headers=head(key, world, "a")).json()
    assert gaps["min_published"] == 15 and any(g["topic_id"] == world.topic for g in gaps["items"])


# ── nightly statistics ──


def attempts_for(pg: psycopg.Connection[Any], qid: str, n: int, correct: int) -> list[uuid.UUID]:
    users = [create_auth_user(pg) for _ in range(n)]
    for i, u in enumerate(users):
        ok = i < correct
        pg.execute(
            "insert into public.attempts (user_id, question_id, chosen, correct, seconds, mode)"
            " values (%s, %s, %s, %s, %s, 'tutor')",
            (u, qid, "A" if ok else "B", ok, 45),
        )
        # a second, later attempt of the same question must not count (first attempts only)
        pg.execute(
            "insert into public.attempts (user_id, question_id, chosen, correct, seconds, mode)"
            " values (%s, %s, 'A', true, 5, 'tutor')",
            (u, qid),
        )
    return users


async def test_psychometrics_flags_at_n_30_but_never_below(
    store: PgAuthoringStore, world: World, make_item: Make, pg: psycopg.Connection[Any]
) -> None:
    low = make_item("low", status="published", ai=False, review={"humanExpertReviewed": True})
    few = make_item("few", status="published", ai=False, review={"humanExpertReviewed": True})
    users = attempts_for(pg, low, 30, 3) + attempts_for(pg, few, 29, 2)
    try:
        stats = await psychometrics_job.run(store.db, AuthoringRules())
        assert stats.items >= 2 and stats.flagged >= 1
        s_low = pg.execute("select stats from public.questions where id = %s", (low,)).fetchone()
        assert s_low is not None and s_low[0]["n"] == 30 and s_low[0]["pCorrect"] == pytest.approx(0.1)
        assert {"p_too_low", "distractor_over_key"} <= set(s_low[0]["flags"])
        assert s_low[0]["optionCounts"]["B"] == 27
        s_few = pg.execute("select stats from public.questions where id = %s", (few,)).fetchone()
        assert s_few is not None and s_few[0]["n"] == 29 and s_few[0]["flags"] == []  # N < 30: no flag
        again = await psychometrics_job.run(store.db, AuthoringRules())
        assert again.updated == 0  # unchanged statistics are not rewritten
    finally:
        pg.execute("delete from public.attempts where question_id = any(%s)", ([low, few],))
        pg.execute("delete from auth.users where id = any(%s)", (users,))


def test_flagged_items_are_acted_on_by_a_human_and_never_retired_below_n_100(
    client: TestClient, key: SigningKey, world: World, make_item: Make, pg: psycopg.Connection[Any]
) -> None:
    stats = {
        "n": 40,
        "pCorrect": 0.1,
        "flags": ["p_too_low"],
        "computedAt": "2026-11-17T00:00:00+00:00",
        "optionCounts": {"A": 4, "B": 36},
    }
    qid = make_item("f", status="published", ai=False, review={"humanExpertReviewed": True}, stats=stats)
    h = head(key, world, "a")
    detail = client.get(f"/api/admin/review/items/{qid}", headers=h).json()
    assert (
        detail["stats"]["flags"] == ["p_too_low"] and detail["stats"]["can_act"] is False and detail["stats"]["n"] == 40
    )
    retire = client.post(f"/api/admin/review/items/{qid}/psychometrics", json={"action": "retire"}, headers=h)
    assert retire.status_code == 409 and retire.json()["error"]["code"] == "too_few_responses"
    keep = client.post(f"/api/admin/review/items/{qid}/psychometrics", json={"action": "keep"}, headers=h)
    assert keep.status_code == 200 and keep.json()["stats"]["flags"] == []  # acknowledged
    assert (
        client.post(f"/api/admin/review/items/{qid}/psychometrics", json={"action": "keep"}, headers=h).status_code
        == 409
    )  # nothing flagged
    stats2 = {**stats, "n": 120, "computedAt": "2026-11-18T00:00:00+00:00"}
    pg.execute("update public.questions set stats = %s where id = %s", (Jsonb(stats2), qid))
    assert client.get(f"/api/admin/review/items/{qid}", headers=h).json()["stats"]["flags"] == [
        "p_too_low"
    ]  # new numbers, flagged again
    revise = client.post(f"/api/admin/review/items/{qid}/psychometrics", json={"action": "retire"}, headers=h)
    assert revise.status_code == 200 and revise.json()["status"] == "retired"


# ── RLS of the new tables ──


def test_review_tables_are_backend_only_and_export_jobs_are_own_rows(pg: psycopg.Connection[Any], world: World) -> None:
    other = create_auth_user(pg)
    try:
        pg.execute("insert into public.export_jobs (user_id, status) values (%s, 'ready')", (world.student,))
        pg.execute("insert into public.export_jobs (user_id, status) values (%s, 'ready')", (other,))
        for table in ("question_reviews", "authoring_runs"):
            for role in ("authenticated", "anon"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    run_as(pg, role, f"select * from public.{table}")  # noqa: S608
        rows = run_as(pg, "authenticated", "select user_id from public.export_jobs", sub=world.student)
        assert rows == [(world.student,)]  # only my own job
        for stmt in (
            "insert into public.export_jobs (user_id) values (%s)",
            "update public.export_jobs set status = 'ready'",
            "delete from public.export_jobs",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                run_as(pg, "authenticated", stmt, (world.student,) if "%s" in stmt else None, sub=world.student)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            run_as(pg, "anon", "select * from public.export_jobs")
    finally:
        pg.execute("delete from public.export_jobs where user_id = any(%s)", ([world.student, other],))
        pg.execute("delete from auth.users where id = %s", (other,))


def test_one_active_export_job_per_student(pg: psycopg.Connection[Any], world: World) -> None:
    pg.execute("insert into public.export_jobs (user_id, status) values (%s, 'running')", (world.student,))
    try:
        with pytest.raises(psycopg.errors.UniqueViolation):
            pg.execute("insert into public.export_jobs (user_id, status) values (%s, 'queued')", (world.student,))
        pg.execute(
            "insert into public.export_jobs (user_id, status) values (%s, 'ready')", (world.student,)
        )  # finished ones pile up
    finally:
        pg.execute("delete from public.export_jobs where user_id = %s", (world.student,))


def test_a_reviewer_cannot_hold_two_slots_of_one_round(
    pg: psycopg.Connection[Any], world: World, make_item: Make
) -> None:
    qid = make_item()
    q = (
        "insert into public.question_reviews (question_id, reviewer_user_id, round, ratings, decision)"
        " values (%s, %s, %s, '{}', 'approve')"
    )
    pg.execute(q, (qid, world.users["a"], 1))
    with pytest.raises(psycopg.errors.UniqueViolation):
        pg.execute(q, (qid, world.users["a"], 1))
    pg.execute(q, (qid, world.users["a"], 2))  # another round is fine
    pg.execute(q, (qid, world.users["b"], 1))


def test_agent_health_accepts_the_new_kinds_and_keeps_the_old_ones(pg: psycopg.Connection[Any]) -> None:
    for kind in ("ping", "tavily_usage", "coach", "ink_ocr", "author", "psychometrics"):
        pg.execute(
            "insert into public.agent_health (kind, trigger, ok, detail) values (%s, 'cron', true, '{}')", (kind,)
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute("insert into public.agent_health (kind, trigger, ok, detail) values ('nope', 'cron', true, '{}')")
    pg.execute("delete from public.agent_health where kind in ('author', 'psychometrics')")
