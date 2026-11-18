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

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from naadi_api.plan.generator import PlanInputs, generate, to_csv
from naadi_api.plan.template import template_days
from tests.auth.jwt_helpers import SigningKey
from tests.qbank.conftest import auth

from .conftest import onboard, today_local
from .helpers import template

pytestmark = [pytest.mark.integration, pytest.mark.db]


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


@pytest.fixture
def pool(pg: psycopg.Connection[Any]) -> Iterator[list[str]]:
    """50 non-mock Step 1 questions over two weighted systems, plus a mock question that must never be chosen."""
    tag = uuid.uuid4().hex[:8]
    src, sys_a, sys_b = f"t{tag}-src", f"t{tag}.sys.a", f"t{tag}.sys.b"
    pg.execute("insert into public.sources (id, name, license) values (%s, 'T', 'proprietary-naadi')", (src,))
    for i, (sid, lo, hi) in enumerate(((sys_a, 10, 14), (sys_b, 4, 8))):
        pg.execute(
            "insert into public.taxonomy_nodes (id, kind, label, exam, weight_min, weight_max, sort)"
            " values (%s, 'system', %s, '{step1}', %s, %s, %s)",
            (sid, f"Diag {tag} {i}", lo, hi, 960 + i),
        )
    ids = [f"t{tag}-d{i:02d}" for i in range(50)]
    opts = Jsonb([{"label": "A", "html": "a"}, {"label": "B", "html": "b"}])
    for i, qid in enumerate(ids):
        pg.execute(
            "insert into public.questions (id, exam, stem_html, options, answer, difficulty, taxonomy_ids, source_id)"
            " values (%s, 'step1', '<p>s</p>', %s, 'B', %s, %s, %s)",
            (qid, opts, ("easy", "medium", "hard")[i % 3], [sys_a if i < 30 else sys_b], src),
        )
    mock = f"t{tag}-mock"
    pg.execute(
        "insert into public.questions (id, exam, block, stem_html, options, answer, taxonomy_ids, source_id)"
        " values (%s, 'step1', 1, '<p>m</p>', %s, 'B', %s, %s)",
        (mock, opts, [sys_a], src),
    )
    yield [*ids, mock]
    pg.execute("delete from public.blocks where %s && question_ids", ([*ids, mock],))
    pg.execute("delete from public.questions where id = any(%s)", ([*ids, mock],))
    pg.execute("delete from public.taxonomy_nodes where id = any(%s)", ([sys_a, sys_b],))
    pg.execute("delete from public.sources where id = %s", (src,))


def test_diagnostic_is_a_timed_40_question_block_and_idempotent(
    client: TestClient,
    key: SigningKey,
    users: tuple[uuid.UUID, uuid.UUID],
    pg: psycopg.Connection[Any],
    pool: list[str],
) -> None:
    a, b = users
    assert client.get("/api/onboarding", headers=auth(key, a)).json()["diagnostic"]["available"] is True
    res = client.post("/api/diagnostic", headers=auth(key, a))
    assert res.status_code == 201, res.text
    first = res.json()
    assert first["questions"] == 40 and first["created"] is True
    row = pg.execute(
        "select config, question_ids, user_id from public.blocks where id = %s", (first["block_id"],)
    ).fetchone()
    assert row is not None and row[2] == a
    cfg, qids = row[0], row[1]
    assert cfg["mode"] == "timed" and cfg["purpose"] == "diagnostic"
    assert len(qids) == len(set(qids)) == 40
    mock = pool[-1]
    assert mock not in qids
    # Idempotent: the open diagnostic comes back, nothing new is created.
    again = client.post("/api/diagnostic", headers=auth(key, a))
    assert again.status_code == 200 and again.json()["block_id"] == first["block_id"]
    assert again.json()["created"] is False
    assert (
        client.get("/api/onboarding", headers=auth(key, a)).json()["diagnostic"]["open_block_id"] == first["block_id"]
    )
    # Today offers to resume it (counts only, no answers) and B sees nothing of it.
    today = client.get("/api/today", headers=auth(key, a)).json()
    assert today["resume"]["kind"] == "block" and today["resume"]["diagnostic"] is True
    assert today["resume"]["block_id"] == first["block_id"] and today["resume"]["next_n"] == 1
    assert client.get("/api/today", headers=auth(key, b)).json()["resume"] is None
    # Deterministic per student: a second student gets a different but equally sized block.
    other = client.post("/api/diagnostic", headers=auth(key, b)).json()
    assert other["questions"] == 40 and other["block_id"] != first["block_id"]


# ── Today's numbers against direct SQL ──


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


def test_concurrent_first_requests_all_see_the_profile(
    client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """A new student's first page load sends /api/me, /api/today and /api/onboarding at once: none may 500."""
    from concurrent.futures import ThreadPoolExecutor

    a, _ = users
    paths = ["/api/me", "/api/today", "/api/onboarding"] * 4
    with ThreadPoolExecutor(max_workers=len(paths)) as pool:
        codes = list(pool.map(lambda p: client.get(p, headers=auth(key, a)).status_code, paths))
    assert codes == [200] * len(paths)


def test_today_cards_due_matches_the_cards_screen(
    client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID], pg: psycopg.Connection[Any]
) -> None:
    a, _ = users
    assert onboard(client, key, a).status_code == 200
    own = [uuid.uuid4() for _ in range(3)]
    for i, cid in enumerate(own):
        pg.execute(
            "insert into public.flashcards (id, owner_user_id, front_html, back_html) values (%s, %s, 'f', 'b')",
            (cid, a),
        )
        if i == 0:  # reviewed and not due yet
            pg.execute(
                "insert into public.card_reviews (user_id, card_id, due, reps, lapses, state)"
                " values (%s, %s, now() + interval '3 days', 1, 0, 2)",
                (a, cid),
            )
    try:
        stats = client.get("/api/cards/stats", headers=auth(key, a))
        assert stats.status_code == 200, stats.text
        today = client.get("/api/today", headers=auth(key, a)).json()
        assert today["cards"]["due"] == stats.json()["due_now"]
        assert today["cards"]["reviewable"] is True
    finally:
        pg.execute("delete from public.flashcards where id = any(%s)", (own,))


# ── S11 rework ──


def test_exam_at_the_day_limit_is_accepted_and_one_day_past_is_refused(
    client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID], pg: psycopg.Connection[Any]
) -> None:
    """`plan.max_study_days` (180) bounds the exam date: checked before any work, and nothing is written."""
    a, b = users
    state = client.get("/api/onboarding", headers=auth(key, a)).json()
    assert state["max_days"] == 180
    at, past = today_local() + dt.timedelta(days=180), today_local() + dt.timedelta(days=181)
    days = "".join(f"&study_days={d}" for d in range(1, 8))
    ok = client.get(f"/api/plan/preview?exam=step1&exam_date={at}&daily_hours=8{days}", headers=auth(key, a)).json()
    assert ok["ok"] is True and ok["max_days"] == 180
    far = client.get(f"/api/plan/preview?exam=step1&exam_date={past}&daily_hours=8{days}", headers=auth(key, a))
    assert far.status_code == 200
    assert far.json()["ok"] is False and far.json()["problem"] == "too_many_days"
    assert onboard(client, key, a, exam_date=at.isoformat()).status_code == 200
    res = onboard(client, key, b, exam_date=past.isoformat())
    assert res.status_code == 422 and res.json()["error"]["code"] == "too_many_days"
    detail = res.json()["error"]["details"][0]
    assert detail["loc"] == ["body", "exam_date"] and detail["msg"] == "days=181,allowed=180"
    row = pg.execute(
        "select exam_date, settings ? 'onboarding' from public.profiles where user_id = %s", (b,)
    ).fetchone()
    assert row is None or row == (None, False)  # not even the answers are kept: the date itself is invalid
    assert pg.execute("select count(*) from public.study_plans where user_id = %s", (b,)).fetchone() == (0,)


def test_first_onboarding_saves_the_device_time_zone_with_the_plan(
    client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID], pg: psycopg.Connection[Any]
) -> None:
    a, _ = users
    assert onboard(client, key, a, timezone="Not/AZone").status_code == 422
    res = onboard(client, key, a, timezone="America/Chicago")
    assert res.status_code == 200, res.text
    row = pg.execute(
        "select p.timezone, p.settings->'onboarding' ? 'completed_at', (select count(*) from public.study_plans s"
        " where s.user_id = p.user_id and s.active) from public.profiles p where p.user_id = %s",
        (a,),
    ).fetchone()
    assert row == ("America/Chicago", True, 1)


def test_concurrent_diagnostic_requests_create_one_block(
    client: TestClient,
    key: SigningKey,
    users: tuple[uuid.UUID, uuid.UUID],
    pg: psycopg.Connection[Any],
    pool: list[str],
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    a, _ = users
    with ThreadPoolExecutor(max_workers=6) as ex:
        out = list(ex.map(lambda _: client.post("/api/diagnostic", headers=auth(key, a)), range(6)))
    assert sorted(r.status_code for r in out) == [200] * 5 + [201], [r.text for r in out]
    assert len({r.json()["block_id"] for r in out}) == 1
    assert [r.json()["created"] for r in out].count(True) == 1
    assert pg.execute(
        "select count(*) from public.blocks where user_id = %s and config->>'purpose' = 'diagnostic'", (a,)
    ).fetchone() == (1,)


def test_a_suggestion_leaves_new_once_and_clients_may_only_dismiss(
    client: TestClient, key: SigningKey, users: tuple[uuid.UUID, uuid.UUID], pg: psycopg.Connection[Any]
) -> None:
    from tests.db.rls import as_user

    a, _ = users
    assert onboard(client, key, a).status_code == 200
    s1, s2 = _suggestion(pg, a), _suggestion(pg, a)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), as_user(pg, a) as conn:
        conn.execute("update public.coach_suggestions set status = 'accepted' where id = %s", (s1,))
    assert client.post(f"/api/coach/suggestions/{s1}/accept", headers=auth(key, a)).status_code == 200
    for back in ("new", "dismissed"):  # not even the API's own role can reopen or flip it
        with pytest.raises(psycopg.errors.CheckViolation):
            pg.execute("update public.coach_suggestions set status = %s where id = %s", (back, s1))
    with as_user(pg, a) as conn:  # dismissing is the client's one transition (rolled back by the helper)
        conn.execute("update public.coach_suggestions set status = 'dismissed' where id = %s", (s2,))
    pg.execute("update public.coach_suggestions set status = 'dismissed' where id = %s", (s2,))
    with pytest.raises(psycopg.errors.CheckViolation), as_user(pg, a) as conn:
        conn.execute("update public.coach_suggestions set status = 'new' where id = %s", (s2,))
    assert client.post(f"/api/coach/suggestions/{s1}/accept", headers=auth(key, a)).json()["added_items"] == []
    assert pg.execute(
        "select count(*) from public.plan_items where user_id = %s and origin = 'assistant'", (a,)
    ).fetchone() == (1,)
