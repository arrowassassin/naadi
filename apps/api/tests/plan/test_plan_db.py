"""Integration (real Postgres, the real HTTP path): onboarding → plan → Today → coach actions, with the numbers
Today shows checked against direct SQL on the fixture rows."""

from __future__ import annotations

import csv
import datetime as dt
import io
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from naadi_api.plan.generator import PlanInputs, generate, to_csv
from naadi_api.plan.template import template_days
from tests.auth.jwt_helpers import SigningKey
from tests.qbank.conftest import auth, build_client

from .helpers import template

pytestmark = [pytest.mark.integration, pytest.mark.db]

TZ = "Asia/Kolkata"


@pytest.fixture(scope="module")
def url() -> str:
    from tests.db.rls import database_url, ensure_migrated, require_local

    u = database_url()
    require_local(u)
    ensure_migrated(u)
    return u


@pytest.fixture
def pg(url: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(url, autocommit=True) as conn:
        yield conn


@pytest.fixture(scope="module")
def seeded_template(url: str) -> None:
    """The step1-60d template, as the seed importer writes it (left alone if the database is already seeded)."""
    with psycopg.connect(url, autocommit=True) as pg:
        pg.execute(
            "insert into public.sources (id, name, license) values ('naadi-original-v1', 'Naadi original',"
            " 'proprietary-naadi') on conflict (id) do nothing"
        )
        pg.execute(
            "insert into public.plan_templates (id, exam, title, days) values ('step1-60d', 'step1',"
            " 'Step 1 in 60 days', 60) on conflict (id) do nothing"
        )
        for t in template():
            pg.execute(
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


@pytest.fixture
def key() -> SigningKey:
    return SigningKey()


@pytest.fixture
def client(url: str, key: SigningKey, static_dir: Path, seeded_template: None) -> Iterator[TestClient]:
    with build_client(url, key, static_dir) as c:
        yield c


@pytest.fixture
def users(pg: psycopg.Connection[Any]) -> Iterator[tuple[uuid.UUID, uuid.UUID]]:
    from tests.db.rls import create_auth_user

    a, b = create_auth_user(pg), create_auth_user(pg)
    yield a, b
    pg.execute("delete from auth.users where id = any(%s)", ([a, b],))


def today_local() -> dt.date:
    return dt.datetime.now(dt.UTC).astimezone(ZoneInfo(TZ)).date()


def onboard(client: TestClient, key: SigningKey, uid: uuid.UUID, **over: Any) -> Any:
    body = {
        "exam": "step1",
        "exam_date": (today_local() + dt.timedelta(days=60)).isoformat(),
        "medical_school": "Test Medical College",
        "daily_hours": 10,
        "study_days": [1, 2, 3, 4, 5, 6, 7],
        "materials": ["qbank", "anki"],
        **over,
    }
    return client.put("/api/onboarding", json=body, headers=auth(key, uid))


def test_endpoints_require_sign_in(client: TestClient) -> None:
    for method, path in (
        ("get", "/api/onboarding"),
        ("put", "/api/onboarding"),
        ("get", "/api/plan"),
        ("get", "/api/plan/export.csv"),
        ("get", "/api/plan/preview?exam=step1&exam_date=2027-01-15&daily_hours=8&study_days=1"),
        ("post", f"/api/plan/items/{uuid.uuid4()}/status"),
        ("post", "/api/diagnostic"),
        ("get", "/api/today"),
        ("post", f"/api/coach/suggestions/{uuid.uuid4()}/accept"),
        ("post", f"/api/coach/suggestions/{uuid.uuid4()}/dismiss"),
    ):
        assert getattr(client, method)(path).status_code == 401, path


def test_onboarding_state_offers_only_exams_with_a_template(
    client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    res = client.get("/api/onboarding", headers=auth(key, users[0]))
    assert res.status_code == 200, res.text
    body = res.json()
    assert [e["id"] for e in body["exams"]] == ["step1"]
    assert body["completed_at"] is None
    assert body["materials"] == ["qbank", "review_book", "anki", "video_course", "own_pdfs", "nothing"]
    assert body["today"] == today_local().isoformat()
    assert body["diagnostic"]["questions"] == 40


def test_onboarding_generates_the_plan_and_exports_it(
    client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID], pg: psycopg.Connection[Any]
) -> None:
    a, b = users
    res = onboard(client, key, a)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["summary"]["study_days"] == 60
    assert out["summary"]["days_per_week"] == 7
    assert out["summary"]["assessments"] == 10
    prof = pg.execute(
        "select exam_date, daily_hours::float8, study_days::int[], settings->'onboarding' from public.profiles"
        " where user_id = %s",
        (a,),
    ).fetchone()
    assert prof is not None
    assert prof[0] == today_local() + dt.timedelta(days=60)
    assert prof[1] == 10 and prof[2] == [1, 2, 3, 4, 5, 6, 7]
    assert prof[3]["medical_school"] == "Test Medical College" and prof[3]["materials"] == ["qbank", "anki"]
    assert prof[3]["completed_at"]

    plan = client.get("/api/plan", headers=auth(key, a)).json()
    assert len(plan["days"]) == 60
    assert plan["days"][0]["date"] == today_local().isoformat()
    assert plan["days"][-1]["topic"].startswith("REST")
    assert all(d["items"] for d in plan["days"])
    assert plan["summary"]["assessments"] == 10

    csv_res = client.get("/api/plan/export.csv", headers=auth(key, a))
    assert csv_res.status_code == 200
    assert csv_res.headers["content-type"].startswith("text/csv")
    inputs = PlanInputs(today_local(), today_local() + dt.timedelta(days=60), frozenset(range(1, 8)), 10)
    assert csv_res.content == to_csv(generate(template(), inputs))
    rows = list(csv.reader(io.StringIO(csv_res.content.decode())))
    assert rows[0][0] == "Day" and len(rows) == 61

    # Another student sees none of it.
    assert client.get("/api/plan", headers=auth(key, b)).status_code == 404
    assert client.get("/api/plan/export.csv", headers=auth(key, b)).status_code == 404


def test_a_second_onboarding_replaces_the_active_plan(
    client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID], pg: psycopg.Connection[Any]
) -> None:
    a, _ = users
    assert onboard(client, key, a).status_code == 200
    assert onboard(client, key, a, study_days=[1, 2, 3, 4, 5, 6], daily_hours=6).status_code == 200
    plans = pg.execute(
        "select active, count(*) from public.study_plans where user_id = %s group by active order by active", (a,)
    ).fetchall()
    assert plans == [(False, 1), (True, 1)]
    dupes = pg.execute(
        "select day, sort, count(*) from public.plan_items where user_id = %s and origin = 'generator'"
        " group by 1, 2 having count(*) > 1",
        (a,),
    ).fetchall()
    assert dupes == []
    plan = client.get("/api/plan", headers=auth(key, a)).json()
    assert plan["summary"]["days_per_week"] == 6
    assert all(dt.date.fromisoformat(d["date"]).isoweekday() != 7 for d in plan["days"][:-1])
    hours = {d["target_hours"] for d in plan["days"][:-1]}
    assert max(hours) <= 6


def test_too_few_days_is_422_and_keeps_the_answers(
    client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID], pg: psycopg.Connection[Any]
) -> None:
    a, _ = users
    when = today_local() + dt.timedelta(days=30)
    res = onboard(client, key, a, exam_date=when.isoformat(), study_days=[6, 7])
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "too_few_study_days"
    row = pg.execute(
        "select exam_date, settings->'onboarding' from public.profiles where user_id = %s", (a,)
    ).fetchone()
    assert row is not None and row[0] == when
    assert "completed_at" not in row[1]
    assert client.get("/api/plan", headers=auth(key, a)).status_code == 404


def test_preview_is_structured_and_saves_nothing(
    client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID], pg: psycopg.Connection[Any]
) -> None:
    a, _ = users
    when = (today_local() + dt.timedelta(days=77)).isoformat()
    q = f"/api/plan/preview?exam=step1&exam_date={when}&daily_hours=6" + "".join(
        f"&study_days={d}" for d in range(1, 7)
    )
    res = client.get(q, headers=auth(key, a))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True and body["summary"]["days_per_week"] == 6
    assert body["summary"]["weeks"] == 11
    short = client.get(
        f"/api/plan/preview?exam=step1&exam_date={when}&daily_hours=6&study_days=7", headers=auth(key, a)
    ).json()
    assert short["ok"] is False and short["problem"] == "too_few_study_days" and short["available_days"] == 11
    assert pg.execute("select count(*) from public.study_plans where user_id = %s", (a,)).fetchone() == (0,)


def test_item_status_only_for_own_items(
    client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = users
    assert onboard(client, key, a).status_code == 200
    today = client.get("/api/today", headers=auth(key, a)).json()
    first = today["plan"]["items"][0]
    assert today["plan"]["now_item"] == first["id"]
    assert (
        client.post(f"/api/plan/items/{first['id']}/status", json={"status": "done"}, headers=auth(key, b)).status_code
        == 404
    )
    done = client.post(f"/api/plan/items/{first['id']}/status", json={"status": "done"}, headers=auth(key, a))
    assert done.status_code == 200 and done.json()["status"] == "done"
    again = client.get("/api/today", headers=auth(key, a)).json()
    assert again["plan"]["now_item"] == today["plan"]["items"][1]["id"]


def test_diagnostic_is_unavailable_until_timed_blocks_ship(
    client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    res = client.post("/api/diagnostic", headers=auth(key, users[0]))
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "diagnostic_unavailable"
    assert client.get("/api/onboarding", headers=auth(key, users[0])).json()["diagnostic"]["available"] is False


# ── Today's numbers against direct SQL ──


@pytest.fixture
def practice(pg: psycopg.Connection[Any], users: tuple[uuid.UUID, uuid.UUID]) -> Iterator[dict[str, Any]]:
    """Two weighted systems and graded attempts: alpha 3/8 right (weak), beta 4/4 (not enough data)."""
    tag = uuid.uuid4().hex[:8]
    a, _ = users
    src = f"t{tag}-src"
    sys_a, sys_b = f"t{tag}.sys.a", f"t{tag}.sys.b"
    pg.execute("insert into public.sources (id, name, license) values (%s, 'T', 'proprietary-naadi')", (src,))
    for i, (sid, lo, hi) in enumerate(((sys_a, 10, 14), (sys_b, 4, 8))):
        pg.execute(
            "insert into public.taxonomy_nodes (id, kind, label, exam, weight_min, weight_max, sort)"
            " values (%s, 'system', %s, '{step1}', %s, %s, %s)",
            (sid, f"System {tag} {i}", lo, hi, 950 + i),
        )
    qids = [f"t{tag}-q{i}" for i in range(12)]
    unanswered = f"t{tag}-q-open"
    for i, qid in enumerate([*qids, unanswered]):
        pg.execute(
            "insert into public.questions (id, exam, stem_html, options, answer, taxonomy_ids, source_id)"
            " values (%s, 'step1', '<p>s</p>', %s, 'B', %s, %s)",
            (qid, Jsonb([{"label": "A", "html": "a"}, {"label": "B", "html": "b"}]), [sys_a if i < 8 else sys_b], src),
        )
    block = uuid.uuid4()
    pg.execute(
        "insert into public.blocks (id, user_id, config, question_ids, started_at) values (%s, %s, %s, %s, now())",
        (block, a, Jsonb({"exam": "step1", "mode": "tutor"}), [*qids, unanswered]),
    )
    now = dt.datetime.now(dt.UTC)
    for i, qid in enumerate(qids):
        right = i in (0, 1, 2) or i >= 8
        pg.execute(
            "insert into public.attempts (user_id, question_id, block_id, chosen, correct, mode, created_at)"
            " values (%s, %s, %s, %s, %s, 'tutor', %s)",
            (a, qid, block, "B" if right else "A", right, now - dt.timedelta(days=1)),
        )
    # a later attempt on q3 in another (finished) block: it counts in the 14-day bars, not as a first attempt
    block2 = uuid.uuid4()
    pg.execute(
        "insert into public.blocks (id, user_id, config, question_ids, started_at, finished_at)"
        " values (%s, %s, %s, %s, now(), now())",
        (block2, a, Jsonb({"exam": "step1", "mode": "tutor"}), [qids[3]]),
    )
    pg.execute(
        "insert into public.attempts (user_id, question_id, block_id, chosen, correct, mode, created_at)"
        " values (%s, %s, %s, 'B', true, 'tutor', %s)",
        (a, qids[3], block2, now - dt.timedelta(hours=1)),
    )
    qids_all = [*qids, unanswered]
    yield {"sys_a": sys_a, "sys_b": sys_b, "qids": qids, "block": block}
    pg.execute("delete from public.attempts where question_id = any(%s)", (qids_all,))
    pg.execute("delete from public.blocks where id = any(%s)", ([block, block2],))
    pg.execute("delete from public.questions where id = any(%s)", (qids_all,))
    pg.execute("delete from public.taxonomy_nodes where id = any(%s)", ([sys_a, sys_b],))
    pg.execute("delete from public.sources where id = %s", (src,))


def test_today_numbers_equal_direct_sql(
    client: TestClient,
    key: SigningKey,
    users: tuple[uuid.UUID, uuid.UUID],
    pg: psycopg.Connection[Any],
    practice: dict[str, Any],
) -> None:
    a, b = users
    assert onboard(client, key, a).status_code == 200
    res = client.get("/api/today", headers=auth(key, a))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["onboarding_complete"] is True
    assert body["days_left"] == 60
    by_id = {s["id"]: s for s in body["systems"]["items"]}
    for sid in (practice["sys_a"], practice["sys_b"]):
        n, c = pg.execute(
            "select count(*), count(*) filter (where a.correct) from public.attempts a"
            " join public.questions q on q.id = a.question_id where a.user_id = %s and a.correct is not null"
            " and %s = any(q.taxonomy_ids) and a.created_at >= now() - interval '14 days'",
            (a, sid),
        ).fetchone() or (0, 0)
        assert (by_id[sid]["attempts"], by_id[sid]["correct"]) == (n, c)
    assert by_id[practice["sys_a"]]["state"] == "weak" and by_id[practice["sys_a"]]["percent"] == round(100 * 4 / 9)
    assert by_id[practice["sys_b"]]["state"] == "insufficient" and by_id[practice["sys_b"]]["percent"] is None
    # Readiness: 12 first attempts < 40 → withheld, with the count and coverage reported.
    firsts = pg.execute(
        "select count(distinct question_id) from public.attempts where user_id = %s and correct is not null", (a,)
    ).fetchone()
    assert body["readiness"]["answered"] == (firsts[0] if firsts else -1) == 12
    assert body["readiness"]["score"] is None
    # The open block is the resume target (no answers shown: only counts).
    assert body["resume"]["kind"] == "block" and body["resume"]["block_id"] == str(practice["block"])
    assert (body["resume"]["answered"], body["resume"]["total"], body["resume"]["next_n"]) == (12, 13, 13)
    # B sees none of A's numbers.
    other = client.get("/api/today", headers=auth(key, b)).json()
    other_ids = {s["id"]: s for s in other["systems"]["items"]}
    assert other_ids[practice["sys_a"]]["attempts"] == 0
    assert other["plan"] is None and other["resume"] is None and other["onboarding_complete"] is False


# ── Coach suggestions ──


def _suggestion(pg: psycopg.Connection[Any], uid: uuid.UUID, **over: Any) -> uuid.UUID:
    sid = uuid.uuid4()
    row = {
        "id": sid,
        "user_id": uid,
        "for_date": today_local(),
        "kind": "catch_up_block",
        "facts": Jsonb({"system": "Renal", "percent": 40, "attempts": 10}),
        "action": Jsonb(
            {
                "add_items": [
                    {
                        "kind": "questions",
                        "title": "Renal",
                        "ref_type": "weak_spot",
                        "ref_id": "step1",
                        "target_count": 20,
                        "target_minutes": 60,
                    }
                ]
            }
        ),
        "source": "rules",
        **over,
    }
    cols = list(row)
    pg.execute(
        f"insert into public.coach_suggestions ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))})",  # noqa: S608
        [row[c] for c in cols],
    )
    return sid


def test_accept_adds_items_once_and_dismiss_hides(
    client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID], pg: psycopg.Connection[Any]
) -> None:
    a, b = users
    assert onboard(client, key, a).status_code == 200
    s1 = _suggestion(pg, a)
    s2 = _suggestion(pg, a, kind="cards_catchup", action=Jsonb({}))
    today = client.get("/api/today", headers=auth(key, a)).json()
    assert {c["id"] for c in today["coach"]} == {str(s1), str(s2)}
    assert client.post(f"/api/coach/suggestions/{s1}/accept", headers=auth(key, b)).status_code == 404
    res = client.post(f"/api/coach/suggestions/{s1}/accept", headers=auth(key, a))
    assert res.status_code == 200, res.text
    added = res.json()["added_items"]
    assert len(added) == 1 and added[0]["ref_type"] == "weak_spot" and added[0]["target_count"] == 20
    assert added[0]["origin"] == "assistant"
    again = client.post(f"/api/coach/suggestions/{s1}/accept", headers=auth(key, a)).json()
    assert again["added_items"] == []
    n = pg.execute(
        "select count(*) from public.plan_items where user_id = %s and origin = 'assistant'", (a,)
    ).fetchone()
    assert n == (1,)
    assert client.post(f"/api/coach/suggestions/{s2}/dismiss", headers=auth(key, a)).json()["status"] == "dismissed"
    after = client.get("/api/today", headers=auth(key, a)).json()
    assert after["coach"] == []
    assert any(i["id"] == added[0]["id"] for i in after["plan"]["items"])


def test_template_rows_round_trip_through_the_database(pg: psycopg.Connection[Any], seeded_template: None) -> None:
    with pg.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute("select * from public.plan_template_days where template_id = 'step1-60d' order by day")
        db_rows = cur.fetchall()
    inputs = PlanInputs(dt.date(2026, 11, 16), dt.date(2027, 1, 15), frozenset(range(1, 8)), 10)
    assert (
        to_csv(generate(template_days(db_rows), inputs))
        == (Path(__file__).resolve().parents[4] / "data" / "study_plan_step1_60d.csv").read_bytes()
    )
