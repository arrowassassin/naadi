"""review15 #1: a student's private questions (S15's private_questions placement: `owner_user_id` set, status
`published`) never reach an admin list or editor, coverage, the review queues, psychometrics, or another student's
assistant. The owner still reads their own. Static side: tests/test_question_scope.py.

    apps/api/tests/db/stack.sh up && DATABASE_URL=... uv run pytest tests/ingest -m db
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from naadi_api.assistant.questions import question_for_assistant
from naadi_api.auth import TokenVerifier
from naadi_api.authoring import psychometrics_job, review_store
from naadi_api.config_api.schemas import AuthoringRules
from naadi_api.db import Database
from naadi_api.main import create_app
from tests.auth.accounts import signed_in
from tests.auth.jwt_helpers import SUPABASE_URL, JwksServer, SigningKey
from tests.conftest import fake_service, make_settings

from ..db.rls import DB_MARKS, create_auth_user, database_url, ensure_migrated, require_local

pytestmark = DB_MARKS
ADMIN_EMAIL = f"admin-{uuid.uuid4().hex[:8]}@example.com"  # unique per run: no clash with other suites
MARK = "Zq7 private stem"


@pytest.fixture(scope="module")
def url() -> str:
    u = database_url()
    require_local(u)
    ensure_migrated(u)
    return u


@pytest.fixture
def world(url: str) -> Iterator[dict[str, Any]]:
    tag = uuid.uuid4().hex[:8]
    with psycopg.connect(url, autocommit=True) as c:
        admin, a, b = create_auth_user(c), create_auth_user(c), create_auth_user(c)
        qids = [f"u-{tag}-q{i}" for i in (1, 2)]
        for qid in qids:
            # As ingest/placements/builtin.py writes them: published, owned, the user-provided source.
            c.execute(
                "insert into public.questions (id, exam, stem_html, options, answer, explanation_html, taxonomy_ids,"
                " source_id, owner_user_id, status, tags, review)"
                " values (%s, 'step1', %s, %s, 'A', '<p>private explanation</p>', '{}', 'user-provided', %s,"
                " 'published', '{own}', %s)",
                (
                    qid,
                    f"<p>{MARK} {qid}</p>",
                    Jsonb([{"label": "A", "html": "x"}, {"label": "B", "html": "y"}]),
                    a,
                    Jsonb({"humanExpertReviewed": False}),
                ),
            )
            # Enough wrong first attempts by the owner that psychometrics would flag a shared item like this.
            for _ in range(3):
                c.execute(
                    "insert into public.attempts (user_id, question_id, chosen, correct, mode)"
                    " values (%s, %s, 'B', false, 'tutor')",
                    (a, qid),
                )
    yield {"admin": admin, "a": a, "b": b, "qids": qids}
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("delete from public.attempts where question_id = any(%s)", (qids,))
        c.execute("delete from public.questions where id = any(%s)", (qids,))
        c.execute("delete from public.admin_audit where actor_user_id = %s", (admin,))
        c.execute("delete from auth.users where id = any(%s)", ([admin, a, b],))


@pytest.fixture
def key() -> SigningKey:
    return SigningKey()


@pytest.fixture
def client(url: str, key: SigningKey, static_dir: Path) -> Iterator[TestClient]:
    s = make_settings(
        static_dir=static_dir,
        supabase_url=SUPABASE_URL,
        database_url=SecretStr(url),
        admin_emails=ADMIN_EMAIL,
        app_env="prod",
        supabase_service_role_key=None,
        supabase_jwt_secret=None,
        openrouter_api_key=None,
        nvidia_api_key=None,
        tavily_api_key=None,
    )
    app = create_app(
        s,
        health=fake_service(),
        db=Database(s.database_url),
        verifier=TokenVerifier(SUPABASE_URL, transport=JwksServer(key).transport()),
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
async def db(url: str) -> AsyncIterator[Database]:
    d = Database(SecretStr(url), max_size=2)
    await d.open()
    yield d
    await d.close()


def test_admins_never_list_open_or_change_a_students_private_questions(
    client: TestClient, key: SigningKey, world: dict[str, Any]
) -> None:
    h = signed_in(key, world["admin"], email=ADMIN_EMAIL, header="Authorization")
    qid = world["qids"][0]
    listed = client.get("/api/admin/questions", params={"q": "Zq7", "limit": 100}, headers=h)
    assert listed.status_code == 200, listed.text
    assert MARK not in listed.text and qid not in listed.text
    every = client.get("/api/admin/questions", params={"limit": 100, "status": "published"}, headers=h)
    assert every.status_code == 200 and qid not in every.text
    assert client.get(f"/api/admin/questions/{qid}", headers=h).status_code == 404
    r = client.post(f"/api/admin/questions/{qid}/status", json={"status": "retired"}, headers=h)
    assert r.status_code in (404, 422), r.text
    r = client.get(f"/api/admin/questions/{qid}/revisions", headers=h)
    assert r.status_code == 404 or qid not in r.text
    # The review queue (every filter) and the console's coverage never count or show them.
    for flt in ("ai_drafts", "seed_spot_check", "psychometric_flags", "needs_check", "adjudication"):
        q = client.get("/api/admin/review/items", params={"filter": flt, "limit": 200}, headers=h)
        assert q.status_code in (200, 422), q.text
        assert MARK not in q.text and qid not in q.text, flt
    with psycopg.connect(client.app.state.settings.database_url.get_secret_value()) as c:  # type: ignore[attr-defined]
        status = c.execute("select status from public.questions where id = %s", (qid,)).fetchone()
    assert status == ("published",)


def test_coverage_counts_shared_questions_only(
    client: TestClient, key: SigningKey, world: dict[str, Any], url: str
) -> None:
    h = signed_in(key, world["admin"], email=ADMIN_EMAIL, header="Authorization")
    topic = f"test-private-coverage-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(
            "insert into public.taxonomy_nodes (id, kind, label, exam) values (%s, 'topic', 'Test topic', '{step1}')",
            (topic,),
        )
        try:
            before = client.get("/api/admin/coverage", params={"exam": "step1"}, headers=h)
            assert before.status_code == 200, before.text
            assert any(t["id"] == topic and t["counts"]["questions"] == 0 for t in before.json()["topics"])
            c.execute("update public.questions set taxonomy_ids = %s where id = any(%s)", ([topic], world["qids"]))
            after = client.get("/api/admin/coverage", params={"exam": "step1"}, headers=h)
            assert after.status_code == 200, after.text
            assert after.json() == before.json()
        finally:
            c.execute("delete from public.taxonomy_nodes where id = %s", (topic,))


async def test_review_counts_queues_and_psychometrics_skip_private_questions(
    db: Database, world: dict[str, Any], url: str
) -> None:
    qids = world["qids"]
    assert db.pool is not None
    async with db.pool.connection() as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]
        assert await review_store.get_item(conn, qids[0]) is None
        assert await review_store.lock_item(conn, qids[0]) is None
        with_private = await review_store.counts(conn, 2)
        # The same counts with the private rows gone (rolled back): they never counted.
        async with conn.transaction(force_rollback=True):
            await conn.execute("delete from public.attempts where question_id = any(%s)", (qids,))
            await conn.execute("delete from public.questions where id = any(%s)", (qids,))
            without = await review_store.counts(conn, 2)
        assert with_private == without
    rules = AuthoringRules().model_copy(update={"psychometrics_min_n": 2, "act_min_n": 2})
    await psychometrics_job.run(db, rules)
    with psycopg.connect(url) as c:
        stats = c.execute("select stats from public.questions where id = any(%s)", (qids,)).fetchall()
    assert all(s[0] in ({}, None) for s in stats)


async def test_the_assistant_reads_a_private_question_for_its_owner_only(db: Database, world: dict[str, Any]) -> None:
    qid = world["qids"][0]
    mine = await question_for_assistant(db, world["a"], qid)
    assert mine is not None and MARK in mine["stem_html"]
    assert await question_for_assistant(db, world["b"], qid) is None
