"""Fixtures for the pathway db tests: a real Postgres, the real HTTP path (token -> JWKS -> RLS), an in-memory vault
storage, and a stage with tasks of every verification state."""

from __future__ import annotations

import datetime as dt
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

ADMIN_EMAIL = "pathway-owner@example.com"
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic"


class FakeStorage:
    """The vault's Storage in memory. Nothing here talks to a network."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.signed: list[tuple[str, int]] = []
        self.removed: list[tuple[str, str]] = []

    async def create_signed_upload(self, path: str) -> tuple[str, str]:
        return f"https://storage.example.test/upload/{path}", "token-" + uuid.uuid4().hex

    async def download(self, bucket: str, path: str) -> bytes | None:
        return self.objects.get((bucket, path))

    async def upload(self, path: str, data: bytes, content_type: str) -> None:
        self.objects[("vault", path)] = data

    async def signed_url(self, path: str, seconds: int) -> str:
        self.signed.append((path, seconds))
        return f"https://storage.example.test/sign/{path}?token=t"

    async def remove(self, bucket: str, path: str) -> None:
        self.removed.append((bucket, path))
        self.objects.pop((bucket, path), None)


@dataclass
class World:
    tag: str
    a: uuid.UUID
    b: uuid.UUID
    admin: uuid.UUID
    stage: str
    stage2: str
    t_fresh: str  # verified today
    t_day90: str  # exactly 90 days ago: not stale
    t_day91: str  # 91 days ago: stale
    t_flagged: str  # fresh but flagged by the weekly check
    t_paid: str  # costs 100 USD
    t_other: str  # in stage2, costs 50 USD
    storage: FakeStorage
    key: SigningKey

    def h(self, who: uuid.UUID) -> dict[str, str]:
        email = ADMIN_EMAIL if who == self.admin else None
        return signed_in(self.key, who, email=email, header="Authorization")


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
def world(pg: psycopg.Connection[Any], key: SigningKey) -> Iterator[World]:
    from tests.db.fixtures import insert_row
    from tests.db.rls import create_auth_user

    tag = uuid.uuid4().hex[:8]
    today = dt.datetime.now(dt.UTC).date()
    w = World(
        tag=tag,
        a=create_auth_user(pg),
        b=create_auth_user(pg),
        admin=create_auth_user(pg, email=ADMIN_EMAIL),
        stage=f"t{tag}_s1",
        stage2=f"t{tag}_s2",
        t_fresh=f"t{tag}-fresh",
        t_day90=f"t{tag}-d90",
        t_day91=f"t{tag}-d91",
        t_flagged=f"t{tag}-flag",
        t_paid=f"t{tag}-paid",
        t_other=f"t{tag}-other",
        storage=FakeStorage(),
        key=key,
    )
    for k, sort in ((w.stage, 9001), (w.stage2, 9002)):
        insert_row(
            pg,
            "pathway_stages",
            {
                "key": k,
                "sort": sort,
                "title": f"Stage {k}",
                "summary_md": "s",
                "official_links": psycopg.types.json.Jsonb([]),
                "last_verified": today,
            },
        )
    rows = [
        (w.t_fresh, w.stage, 1, today, False, 0, "https://www.ecfmg.org/a"),
        (w.t_day90, w.stage, 2, today - dt.timedelta(days=90), False, 0, "https://www.nrmp.org/b"),
        (w.t_day91, w.stage, 3, today - dt.timedelta(days=91), False, 0, "https://example.com/c"),
        (w.t_flagged, w.stage, 4, today, True, 0, "https://www.usmle.org/d"),
        (w.t_paid, w.stage, 5, today, False, 100, "https://www.usmle.org/e"),
        (w.t_other, w.stage2, 1, today, False, 50, "https://www.usmle.org/f"),
    ]
    for tid, st, sort, lv, flag, cost, official in rows:
        insert_row(
            pg,
            "pathway_tasks",
            {
                "id": tid,
                "stage_key": st,
                "sort": sort,
                "title": f"Task {tid}",
                "detail_md": "Detail [ECFMG](https://ecfmg.org)",
                "due_rule": "start+7d",
                "cost_usd": cost,
                "official_url": official,
                "last_verified": lv,
                "needs_review": flag,
            },
        )
    yield w
    pg.execute("delete from public.vault_documents where user_id = any(%s)", ([w.a, w.b],))
    pg.execute("delete from auth.users where id = any(%s)", ([w.a, w.b, w.admin],))
    pg.execute("delete from public.pathway_stages where key = any(%s)", ([w.stage, w.stage2],))


@pytest.fixture
def client(url: str, key: SigningKey, static_dir: Path, world: World) -> Iterator[TestClient]:
    s = make_settings(
        static_dir=static_dir,
        supabase_url=SUPABASE_URL,
        database_url=SecretStr(url),
        admin_emails=ADMIN_EMAIL,
        supabase_anon_key=None,
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
    app.state.vault_storage = world.storage
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
