"""Fixtures for the Cards db tests: a real Postgres, the real HTTP path (token -> JWKS -> user_tx / service role),
and a small world: questions with a graded, a flag-only and an ungraded attempt, a published and a private section,
shared cards linked to each question, the student's own card and another student's private card."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from naadi_api.auth import TokenVerifier
from naadi_api.db import Database
from naadi_api.main import create_app
from tests.auth.accounts import signed_in
from tests.auth.jwt_helpers import SUPABASE_URL, JwksServer, SigningKey
from tests.conftest import fake_service, make_settings

GATED_SECRET = "GATED-BACK-RESTATES-ANSWER-"
PRIVATE_SECRET = "OTHER-STUDENT-PRIVATE-"
CARDS_AGE = timedelta(days=30)  # the world's cards were made this long ago


@dataclass
class World:
    tag: str
    owner: uuid.UUID
    other: uuid.UUID
    q_graded: str  # owner has a graded attempt
    q_flagged: str  # owner has only a flag-only row (chosen and correct null)
    q_ungraded: str  # owner has chosen but not graded (correct null)
    q_untouched: str
    q_draft: str
    chapter: str
    section: str
    private_chapter: str  # other's private chapter
    private_section: str
    shared_plain: uuid.UUID  # shared, unlinked: always visible
    shared_graded: uuid.UUID  # shared, linked to q_graded: visible to owner
    shared_flagged: uuid.UUID  # shared, linked to q_flagged: hidden
    shared_ungraded: uuid.UUID  # shared, linked to q_ungraded: hidden
    shared_untouched: uuid.UUID  # shared, linked to q_untouched: hidden
    own: uuid.UUID  # owner's own user card
    others: uuid.UUID  # other's private card

    @property
    def hidden(self) -> set[uuid.UUID]:
        return {self.shared_flagged, self.shared_ungraded, self.shared_untouched, self.others}

    @property
    def visible(self) -> set[uuid.UUID]:
        return {self.shared_plain, self.shared_graded, self.own}


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
    owner, other = create_auth_user(pg), create_auth_user(pg)
    q = [f"t{tag}-cq{i}" for i in range(1, 6)]
    w = World(
        tag=tag,
        owner=owner,
        other=other,
        q_graded=q[0],
        q_flagged=q[1],
        q_ungraded=q[2],
        q_untouched=q[3],
        q_draft=q[4],
        chapter=f"t{tag}-cch",
        section=f"t{tag}-cch-s1",
        private_chapter=f"t{tag}-cpriv",
        private_section=f"t{tag}-cpriv-s1",
        shared_plain=uuid.uuid4(),
        shared_graded=uuid.uuid4(),
        shared_flagged=uuid.uuid4(),
        shared_ungraded=uuid.uuid4(),
        shared_untouched=uuid.uuid4(),
        own=uuid.uuid4(),
        others=uuid.uuid4(),
    )
    src = f"t{tag}-csrc"
    insert(pg, "sources", {"id": src, "name": "Naadi original", "license": "proprietary-naadi"})
    insert(
        pg,
        "chapters",
        {"id": w.chapter, "exam": "step1", "kind": "chapter", "title": "Cards chapter", "sort": 950, "source_id": src},
    )
    insert(pg, "chapter_sections", {"id": w.section, "chapter_id": w.chapter, "heading": "S1", "sort": 1})
    insert(
        pg,
        "chapters",
        {
            "id": w.private_chapter,
            "exam": "step1",
            "kind": "user_upload",
            "title": "Mine",
            "owner_user_id": other,
            "source_id": src,
        },
    )
    insert(
        pg, "chapter_sections", {"id": w.private_section, "chapter_id": w.private_chapter, "heading": "P", "sort": 1}
    )
    for i, qid in enumerate(q):
        insert(
            pg,
            "questions",
            {
                "id": qid,
                "exam": "step1",
                "stem_html": f"<p>Stem {i}</p>",
                "options": psycopg.types.json.Jsonb([{"label": "A", "html": "a"}, {"label": "B", "html": "b"}]),
                "answer": "B",
                "source_id": src,
                "status": "draft" if qid == w.q_draft else "published",
            },
        )
    # The API's own writes (clients cannot insert attempts): graded, flag-only, chosen-but-ungraded.
    insert(pg, "attempts", {"user_id": owner, "question_id": w.q_graded, "chosen": "B", "correct": True})
    insert(pg, "attempts", {"user_id": owner, "question_id": w.q_flagged, "flagged": True})
    insert(pg, "attempts", {"user_id": owner, "question_id": w.q_ungraded, "chosen": "A", "mode": "exam"})
    # another student's graded attempt on q_untouched opens nothing for the owner
    insert(pg, "attempts", {"user_id": other, "question_id": w.q_untouched, "chosen": "B", "correct": True})
    made = datetime.now(UTC) - CARDS_AGE  # reviews may be dated back to the card, never before it
    for cid, qid in (
        (w.shared_plain, None),
        (w.shared_graded, w.q_graded),
        (w.shared_flagged, w.q_flagged),
        (w.shared_ungraded, w.q_ungraded),
        (w.shared_untouched, w.q_untouched),
    ):
        insert(
            pg,
            "flashcards",
            {
                "id": cid,
                "deck": f"t{tag}",
                "front_html": f"<p>Front {cid}</p>",
                "back_html": f"<p>{GATED_SECRET}{cid}</p>",
                "origin": "seed",
                "question_id": qid,
                "created_at": made,
            },
        )
    insert(
        pg,
        "flashcards",
        {
            "id": w.own,
            "owner_user_id": owner,
            "deck": f"t{tag}",
            "front_html": "<p>Mine</p>",
            "back_html": "<p>A</p>",
            "created_at": made,
        },
    )
    insert(
        pg,
        "flashcards",
        {
            "id": w.others,
            "owner_user_id": other,
            "deck": f"t{tag}",
            "front_html": "<p>x</p>",
            "back_html": f"<p>{PRIVATE_SECRET}</p>",
            "created_at": made,
        },
    )
    yield w
    pg.execute("delete from public.flashcards where deck = %s or owner_user_id = any(%s)", (f"t{tag}", [owner, other]))
    pg.execute("delete from public.attempts where question_id = any(%s)", (q,))
    pg.execute("delete from public.questions where id = any(%s)", (q,))
    pg.execute("delete from public.chapters where id = any(%s)", ([w.chapter, w.private_chapter],))
    pg.execute("delete from public.sources where id = %s", (src,))
    pg.execute("delete from auth.users where id = any(%s)", ([owner, other],))


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
