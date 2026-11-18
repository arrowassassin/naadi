"""Shared DB-test plumbing of segment 20 (Anki + YouTube): a local database, a small published course, signed-in
students and an admin, the real HTTP app, and an in-memory image store. Only ever a local container."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
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

ADMIN_EMAIL = "owner@example.com"


class FakeStore:
    """`ImageStore` in memory: paths are bucket-relative like Storage's."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, path: str, data: bytes) -> None:
        self.objects[path] = data

    async def get(self, path: str) -> bytes | None:
        return self.objects.get(path)


@dataclass
class World:
    tag: str
    a: uuid.UUID
    b: uuid.UUID
    admin: uuid.UUID
    chapter: str
    section: str
    section2: str


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


@pytest.fixture
def key() -> SigningKey:
    return SigningKey()


@pytest.fixture
def world(pg: psycopg.Connection[Any]) -> Iterator[World]:
    from tests.db.fixtures import insert_row
    from tests.db.rls import create_auth_user

    tag = uuid.uuid4().hex[:8]
    a, b, admin = create_auth_user(pg), create_auth_user(pg), create_auth_user(pg)
    w = World(tag, a, b, admin, f"t{tag}-mch", f"t{tag}-mch-s1", f"t{tag}-mch-s2")
    pg.execute(
        "insert into public.sources (id, name, license) values ('youtube', 'YouTube', 'youtube-embed-only')"
        " on conflict (id) do nothing"
    )
    insert_row(pg, "sources", {"id": f"t{tag}-msrc", "name": "Naadi original", "license": "proprietary-naadi"})
    insert_row(
        pg,
        "chapters",
        {
            "id": w.chapter,
            "exam": "step1",
            "kind": "chapter",
            "title": "Media chapter",
            "sort": 951,
            "source_id": f"t{tag}-msrc",
            "status": "published",
        },
    )
    insert_row(
        pg,
        "chapter_sections",
        {"id": w.section, "chapter_id": w.chapter, "heading": f"Cardiac {tag} failure", "sort": 1},
    )
    insert_row(
        pg,
        "chapter_sections",
        {"id": w.section2, "chapter_id": w.chapter, "heading": f"Nephron {tag} physiology", "sort": 2},
    )
    yield w
    pg.execute("delete from public.video_candidates where media_id like %s", (f"yt-{tag}%",))
    pg.execute("delete from public.section_media where section_id = any(%s)", ([w.section, w.section2],))
    pg.execute("delete from public.media_assets where id like %s or owner_user_id = any(%s)", (f"yt-{tag}%", [a, b]))
    pg.execute("delete from public.flashcards where owner_user_id = any(%s)", ([a, b],))
    pg.execute("delete from public.chapters where id = %s", (w.chapter,))
    pg.execute("delete from public.sources where id = %s", (f"t{tag}-msrc",))
    pg.execute("delete from auth.users where id = any(%s)", ([a, b, admin],))


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def client(url: str, key: SigningKey, static_dir: Path, store: FakeStore) -> Iterator[TestClient]:
    s = make_settings(
        static_dir=static_dir,
        supabase_url=SUPABASE_URL,
        database_url=SecretStr(url),
        admin_emails=ADMIN_EMAIL,
        app_env="prod",
    )
    app = create_app(
        s,
        health=fake_service(),
        db=Database(s.database_url),
        verifier=TokenVerifier(SUPABASE_URL, transport=JwksServer(key).transport()),
    )
    app.state.anki_image_store = store
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def student(key: SigningKey, uid: uuid.UUID) -> dict[str, str]:
    return signed_in(key, uid)


def admin_headers(key: SigningKey, uid: uuid.UUID) -> dict[str, str]:
    return signed_in(key, uid, email=ADMIN_EMAIL)
