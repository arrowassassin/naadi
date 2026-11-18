"""Fixtures for the plan / Today / coach db tests: the real HTTP path and the step1-60d template."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from tests.auth.jwt_helpers import SigningKey
from tests.qbank.conftest import auth, build_client

from .helpers import template

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
