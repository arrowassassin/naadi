"""Generation db tests: a real Postgres, the real service with a fake Space client and an in-memory store, and the
real HTTP path (token → JWKS → account check → service role with the caller's id) for the routes."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import SecretStr

from naadi_api.auth import TokenVerifier
from naadi_api.db import Database
from naadi_api.generation.client import FakeGenerationClient
from naadi_api.generation.service import GenerationService
from naadi_api.generation.store import MemoryMediaStore
from naadi_api.llm_config import GenerationSection
from naadi_api.main import create_app
from naadi_api.settings import REPO_ROOT
from tests.auth.jwt_helpers import SUPABASE_URL, JwksServer, SigningKey
from tests.conftest import fake_service, make_settings

ADMIN_EMAIL = "gen-admin@example.test"
PRIVATE, PUBLIC = "generated-media", "content-public"


@dataclass
class World:
    tag: str
    user_a: uuid.UUID
    user_b: uuid.UUID
    admin: uuid.UUID
    chapter: str
    section: str
    private_chapter: str
    private_section: str


def generation_config(**caps: int) -> GenerationSection:
    raw = yaml.safe_load((REPO_ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))["generation"]
    if "image" in caps:
        raw["image"]["max_per_user_per_day"] = caps["image"]
    # the shipped default is video OFF (0); the tests exercise it enabled unless they say otherwise
    raw["video"]["max_per_user_per_day"] = caps.get("video", 5)
    return GenerationSection.model_validate(raw)


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
def world(pg: psycopg.Connection[Any]) -> Iterator[World]:
    from tests.db.fixtures import insert_row
    from tests.db.rls import create_auth_user

    pg.execute("delete from public.generation_state")  # the global pause is shared state: start every test clear
    tag = uuid.uuid4().hex[:8]
    a, b, admin = create_auth_user(pg), create_auth_user(pg), create_auth_user(pg)
    w = World(tag, a, b, admin, f"t{tag}-gch", f"t{tag}-gch-s1", f"t{tag}-gpriv", f"t{tag}-gpriv-s1")
    pg.execute(
        "insert into public.sources (id, name, license, allow_ai_ingest) values"
        " ('naadi-ai-generated', 'Naadi AI-generated illustrations', 'proprietary-naadi', false)"
        " on conflict (id) do nothing"
    )
    src = f"t{tag}-gsrc"
    insert_row(pg, "sources", {"id": src, "name": "Naadi original", "license": "proprietary-naadi"})
    insert_row(
        pg,
        "chapters",
        {"id": w.chapter, "exam": "step1", "kind": "chapter", "title": "Gen chapter", "sort": 951, "source_id": src},
    )
    pg.execute(
        "insert into public.chapter_sections (id, chapter_id, heading, sort, body, body_html)"
        " values (%s, %s, 'S1', 1, %s, '<p>Hello</p>')",
        (
            w.section,
            w.chapter,
            psycopg.types.json.Jsonb(
                {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]}]}
            ),
        ),
    )
    insert_row(
        pg,
        "chapters",
        {
            "id": w.private_chapter,
            "exam": "step1",
            "kind": "user_upload",
            "title": "Mine",
            "owner_user_id": a,
            "source_id": src,
        },
    )
    insert_row(
        pg, "chapter_sections", {"id": w.private_section, "chapter_id": w.private_chapter, "heading": "P", "sort": 1}
    )
    yield w
    pg.execute("delete from public.generation_state")
    pg.execute("delete from public.generated_media where user_id = any(%s)", ([a, b, admin],))
    pg.execute(
        "delete from public.media_assets where id in (select media_id from public.section_media where section_id = %s)",
        (w.section,),
    )
    pg.execute("delete from public.chapters where id = any(%s)", ([w.chapter, w.private_chapter],))
    pg.execute("delete from public.sources where id = %s", (src,))
    pg.execute("delete from auth.users where id = any(%s)", ([a, b, admin],))


@dataclass
class Rig:
    service: GenerationService
    client: FakeGenerationClient
    store: MemoryMediaStore
    now: list[Any]


@pytest.fixture
async def rig(url: str) -> AsyncIterator[Rig]:
    from datetime import UTC, datetime

    now = [datetime.now(UTC)]
    client, store = FakeGenerationClient(), MemoryMediaStore()
    caps: dict[str, int] = {}

    async def config() -> GenerationSection:
        return generation_config(**caps)

    db = Database(SecretStr(url))
    svc = GenerationService(
        db=db,
        config=config,
        client=client,
        store=store,
        buckets=(PRIVATE, PUBLIC),
        clock=lambda: now[0],
    )
    r = Rig(svc, client, store, now)
    r.caps = caps  # type: ignore[attr-defined]
    try:
        yield r
    finally:
        await db.close()


@pytest.fixture
def key() -> SigningKey:
    return SigningKey()


def build_client(url: str, key: SigningKey, static_dir: Path, rig: Rig | None, **settings: Any) -> TestClient:
    s = make_settings(
        static_dir=static_dir,
        supabase_url=SUPABASE_URL,
        database_url=SecretStr(url),
        supabase_anon_key=None,
        supabase_service_role_key=None,
        supabase_jwt_secret=None,
        admin_emails=SecretStr(ADMIN_EMAIL),
        **({"hf_gen_token": SecretStr("hf_test_token_value")} if rig is not None else {}),
        **settings,
    )
    db = Database(s.database_url)
    app = create_app(
        s,
        health=fake_service(),
        db=db,
        verifier=TokenVerifier(SUPABASE_URL, transport=JwksServer(key).transport()),
    )
    if rig is not None:
        # Share the fake backend and store, and keep each pool on its own event loop.
        app.state.generation = GenerationService(
            db=db,
            config=rig.service._config,
            client=rig.client,
            store=rig.store,
            buckets=(PRIVATE, PUBLIC),
            clock=lambda: rig.now[0],
        )
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client(url: str, key: SigningKey, static_dir: Path, rig: Rig) -> Iterator[TestClient]:
    with build_client(url, key, static_dir, rig) as c:
        yield c
