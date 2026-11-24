"""Fixtures for the ink db tests: a real Postgres, the real HTTP path (token -> JWKS -> user_tx -> RLS), and one
surface of every type, visible ones and hidden ones."""

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

from tests.auth.jwt_helpers import SigningKey
from tests.qbank.conftest import auth, build_client


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


@pytest.fixture
def key() -> SigningKey:
    return SigningKey()


@pytest.fixture
def client(url: str, key: SigningKey, static_dir: Path, ink: Ink) -> Iterator[TestClient]:
    # Depends on `ink` so the app shuts down (draining the note reindex jobs a write schedules) before `ink`
    # deletes its users; otherwise a still-running embeddings upsert can deadlock with that cascade delete.
    del ink
    with build_client(url, key, static_dir) as c:
        yield c


@dataclass
class Ink:
    tag: str
    owner: uuid.UUID
    other: uuid.UUID
    chapter: str
    section: str
    draft_section: str  # in a draft chapter: invisible to students
    question: str
    draft_question: str
    card: str  # shared, not question-linked
    private_card: str  # other's own card
    note: str  # owner's note
    other_note: str
    block: str  # owner's block with 2 questions
    upload: str  # owner's pdf

    def h(self, key: SigningKey, who: uuid.UUID | None = None) -> dict[str, str]:
        return auth(key, who or self.owner)


def _insert(pg: psycopg.Connection[Any], table: str, row: dict[str, Any]) -> None:
    from tests.db.fixtures import insert_row

    insert_row(pg, table, row)


@pytest.fixture
def ink(pg: psycopg.Connection[Any]) -> Iterator[Ink]:
    from tests.db.rls import create_auth_user

    tag = uuid.uuid4().hex[:8]
    w = Ink(
        tag=tag,
        owner=create_auth_user(pg),
        other=create_auth_user(pg),
        chapter=f"t{tag}-ch",
        section=f"t{tag}-ch-s01",
        draft_section=f"t{tag}-dr-s01",
        question=f"t{tag}-q1",
        draft_question=f"t{tag}-q2",
        card=str(uuid.uuid4()),
        private_card=str(uuid.uuid4()),
        note=str(uuid.uuid4()),
        other_note=str(uuid.uuid4()),
        block=str(uuid.uuid4()),
        upload=str(uuid.uuid4()),
    )
    src = f"t{tag}-src"
    _insert(pg, "sources", {"id": src, "name": "Naadi original", "license": "proprietary-naadi"})
    for cid, status in ((w.chapter, "published"), (f"t{tag}-dr", "draft")):
        _insert(
            pg,
            "chapters",
            {"id": cid, "exam": "step1", "kind": "chapter", "title": "T", "status": status, "source_id": src},
        )
    body = Jsonb({"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "One"}]}]})
    _insert(pg, "chapter_sections", {"id": w.section, "chapter_id": w.chapter, "sort": 1, "heading": "H", "body": body})
    _insert(pg, "chapter_sections", {"id": w.draft_section, "chapter_id": f"t{tag}-dr", "sort": 1, "heading": "D"})
    q = {
        "exam": "step1",
        "stem_html": "<p>stem</p>",
        "options": Jsonb([{"label": "A", "html": "a"}, {"label": "B", "html": "b"}]),
        "answer": "B",
        "explanation_html": "<p>because</p>",
        "source_id": src,
    }
    _insert(pg, "questions", {"id": w.question, **q})
    _insert(pg, "questions", {"id": w.draft_question, **q, "status": "draft"})
    _insert(pg, "flashcards", {"id": w.card, "front_html": "front", "back_html": "back", "origin": "seed"})
    _insert(
        pg,
        "flashcards",
        {"id": w.private_card, "owner_user_id": w.other, "front_html": "f", "back_html": "b", "origin": "user"},
    )
    _insert(pg, "notes", {"id": w.note, "user_id": w.owner, "anchor_type": "free"})
    _insert(pg, "notes", {"id": w.other_note, "user_id": w.other, "anchor_type": "free"})
    _insert(
        pg,
        "blocks",
        {"id": w.block, "user_id": w.owner, "config": Jsonb({"count": 2}), "question_ids": [w.question, w.question]},
    )
    _insert(pg, "uploads", {"id": w.upload, "user_id": w.owner, "kind": "pdf", "original_name": "a.pdf", "bytes": 1})
    yield w
    pg.execute("delete from auth.users where id = any(%s)", ([w.owner, w.other],))
    pg.execute("delete from public.flashcards where id = any(%s::uuid[])", ([w.card, w.private_card],))
    pg.execute("delete from public.questions where id = any(%s)", ([w.question, w.draft_question],))
    pg.execute("delete from public.chapters where id = any(%s)", ([w.chapter, f"t{tag}-dr"],))
    pg.execute("delete from public.sources where id = %s", (src,))
