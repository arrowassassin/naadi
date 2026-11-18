"""Fixtures for the Qbank db tests: a real Postgres, the real HTTP path (token -> JWKS -> user_tx -> RLS), and a
small world of questions under fresh taxonomy ids (the exam is the seeded `step1`, so the API's exam rules apply,
but every filter in these tests names this world's own system ids and seeded content never changes a count)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from naadi_api.auth import TokenVerifier
from naadi_api.db import Database
from naadi_api.main import create_app
from tests.auth.accounts import signed_in
from tests.auth.jwt_helpers import SUPABASE_URL, JwksServer, SigningKey
from tests.conftest import fake_service, make_settings

SECRET_EXPLANATION = "SECRET-EXPLANATION-"
SECRET_WHY_NOT = "SECRET-WHYNOT-"
SECRET_OBJECTIVE = "SECRET-OBJECTIVE-"
SECRET_ANSWER_HTML = "SECRET-ANSWERHTML-"


@dataclass
class World:
    tag: str
    sys_a: str
    sys_b: str
    disc: str
    questions: list[str]  # 12 ids: 8 in sys_a (q1..q8), 4 in sys_b (q9..q12)
    chapter: str
    mock: str  # an exam-only (mock exam) question, tagged with sys_a
    owner: uuid.UUID
    other: uuid.UUID

    def answer_of(self, qid: str) -> str:
        return "B"

    def wrong_for(self, qid: str) -> str:
        return "A"


@pytest.fixture(scope="module")
def url() -> str:
    from tests.db.rls import database_url, ensure_migrated, require_local

    u = database_url()
    require_local(u)  # these tests insert and delete rows: never a hosted database
    ensure_migrated(u)
    return u


@pytest.fixture
def pg(url: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(url, autocommit=True) as conn:
        yield conn


def insert(pg: psycopg.Connection[Any], table: str, row: dict[str, Any]) -> None:
    from tests.db.fixtures import insert_row

    insert_row(pg, table, row)


@pytest.fixture
def world(pg: psycopg.Connection[Any]) -> Iterator[World]:
    from tests.db.rls import create_auth_user

    tag = uuid.uuid4().hex[:8]
    w = World(
        tag=tag,
        sys_a=f"t{tag}.sys.a",
        sys_b=f"t{tag}.sys.b",
        disc=f"t{tag}.disc.one",
        questions=[f"t{tag}-q{i}" for i in range(1, 13)],
        chapter=f"t{tag}-ch",
        mock=f"t{tag}-mock",
        owner=create_auth_user(pg),
        other=create_auth_user(pg),
    )
    src = f"t{tag}-src"
    insert(pg, "sources", {"id": src, "name": "Naadi original", "license": "proprietary-naadi"})
    for i, (nid, kind, label) in enumerate(
        ((w.sys_a, "system", "Alpha system"), (w.sys_b, "system", "Beta system"), (w.disc, "discipline", "Pathology"))
    ):
        insert(
            pg,
            "taxonomy_nodes",
            {"id": nid, "kind": kind, "label": label, "exam": ["step1"], "sort": 900 + i},
        )
    insert(
        pg,
        "chapters",
        {"id": w.chapter, "exam": "step1", "kind": "chapter", "title": "Alpha chapter", "sort": 900, "source_id": src},
    )
    opts = Jsonb([{"label": x, "html": f"<p>Option {x}</p>"} for x in "ABCDE"])
    for i, qid in enumerate(w.questions, start=1):
        tags = [w.sys_a if i <= 8 else w.sys_b]
        if i % 2 == 0:
            tags.append(w.disc)
        insert(
            pg,
            "questions",
            {
                "id": qid,
                "chapter_id": w.chapter if i == 1 else None,
                "exam": "step1",
                "stem_html": f"<p>Stem {i}</p>",
                "options": opts,
                "answer": "B",
                "answer_html": f"{SECRET_ANSWER_HTML}{i}",
                "explanation_html": f"<p>{SECRET_EXPLANATION}{i}</p>",
                "why_not_html": f"{SECRET_WHY_NOT}{i}",
                "educational_objective": f"{SECRET_OBJECTIVE}{i}",
                "difficulty": ("easy", "medium", "hard")[i % 3],
                "taxonomy_ids": tags,
                "source_id": src,
            },
        )
    insert(
        pg,
        "questions",
        {
            "id": w.mock,
            "exam": "step1",
            "block": 1,
            "stem_html": "<p>Mock stem</p>",
            "options": opts,
            "answer": "B",
            "explanation_html": f"<p>{SECRET_EXPLANATION}mock</p>",
            "taxonomy_ids": [w.sys_a],
            "source_id": src,
        },
    )
    # A shared flashcard that restates question 1's answer: only attempt-gated (RLS), never in the block payload.
    insert(pg, "flashcards", {"front_html": "f", "back_html": "b", "origin": "seed", "question_id": w.questions[0]})
    yield w
    qs = [*w.questions, w.mock]
    pg.execute("delete from public.flashcards where question_id = any(%s)", (qs,))
    pg.execute("delete from public.blocks where user_id = any(%s)", ([w.owner, w.other],))
    pg.execute("delete from public.attempts where question_id = any(%s)", (qs,))
    pg.execute("delete from public.questions where id = any(%s)", (qs,))
    pg.execute("delete from public.chapters where id = %s", (w.chapter,))
    pg.execute("delete from public.taxonomy_nodes where id = any(%s)", ([w.sys_a, w.sys_b, w.disc],))
    pg.execute("delete from public.sources where id = %s", (src,))
    pg.execute("delete from auth.users where id = any(%s)", ([w.owner, w.other],))


@pytest.fixture
def key() -> SigningKey:
    return SigningKey()


def build_client(url: str, key: SigningKey, static_dir: Path) -> TestClient:
    # Explicit None for every secret, so nothing from the environment or a .env file can reach this app.
    s = make_settings(
        static_dir=static_dir,
        supabase_url=SUPABASE_URL,
        database_url=SecretStr(url),
        supabase_anon_key=None,
        supabase_service_role_key=None,
        supabase_jwt_secret=None,
        openrouter_api_key=None,
        nvidia_api_key=None,
        tavily_api_key=None,
        admin_emails=None,
    )
    app = create_app(
        s,
        health=fake_service(),
        db=Database(s.database_url),
        verifier=TokenVerifier(SUPABASE_URL, transport=JwksServer(key).transport()),
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(url: str, key: SigningKey, static_dir: Path) -> Iterator[TestClient]:
    with build_client(url, key, static_dir) as c:
        yield c


def auth(key: SigningKey, uid: uuid.UUID) -> dict[str, str]:
    return signed_in(key, uid)
