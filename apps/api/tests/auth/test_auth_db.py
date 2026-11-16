"""Integration (real Postgres with the migrated schema): `/api/me` and RLS really applied.

Runs in the CI `db` job against `supabase/postgres` and locally against any migrated database
(`DATABASE_URL`). Tokens are minted locally and verified against a test JWKS, so the full HTTP path runs:
bearer token -> JWKS verification -> CurrentUser -> `user_tx` (role authenticated + claims) -> RLS.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from naadi_api.auth import TokenVerifier
from naadi_api.auth_db import user_tx
from naadi_api.db import Database
from naadi_api.main import create_app
from tests.conftest import fake_service, make_settings

from .jwt_helpers import SUPABASE_URL, JwksServer, SigningKey, claims, mint

pytestmark = [pytest.mark.integration, pytest.mark.db]


@pytest.fixture(scope="module")
def url() -> str:
    # seg02-db's helpers (tests/db/rls.py); imported here so unit-only runs do not need them.
    from tests.db.rls import database_url, ensure_migrated

    u = database_url()
    ensure_migrated(u)
    return u


@pytest.fixture
def pg(url: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(url, autocommit=True) as conn:
        yield conn


@pytest.fixture
def users(pg: psycopg.Connection[Any]) -> Iterator[list[uuid.UUID]]:
    from tests.db.rls import create_auth_user

    created: list[uuid.UUID] = []

    def make() -> uuid.UUID:
        uid = create_auth_user(pg)
        created.append(uid)
        return uid

    ids = [make(), make()]
    yield ids
    for uid in created:
        pg.execute("delete from auth.users where id = %s", (uid,))


@pytest.fixture
def key() -> SigningKey:
    return SigningKey()


@pytest.fixture
def client(url: str, key: SigningKey, static_dir: Path) -> Iterator[TestClient]:
    s = make_settings(
        static_dir=static_dir, supabase_url=SUPABASE_URL, database_url=SecretStr(url), admin_emails="owner@example.com"
    )
    db = Database(s.database_url)
    v = TokenVerifier(SUPABASE_URL, transport=JwksServer(key).transport())
    app = create_app(s, health=fake_service(), db=db, verifier=v)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def h(key: SigningKey, uid: uuid.UUID, **kw: Any) -> dict[str, str]:
    return {"authorization": f"Bearer {mint(key, claims(uid, **kw))}"}


def test_me_creates_profile_once(
    client: TestClient, key: SigningKey, users: list[uuid.UUID], pg: psycopg.Connection[Any]
) -> None:
    a, _ = users
    first = client.get("/api/me", headers=h(key, a))
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["profile"]["user_id"] == str(a)
    assert body["profile"]["exam_target"] == "step1"
    assert body["profile"]["settings"] == {}
    assert body["email"] == "student@example.com"
    assert body["is_admin"] is False
    second = client.get("/api/me", headers=h(key, a))
    assert second.json()["profile"]["created_at"] == body["profile"]["created_at"]
    row = pg.execute("select count(*) from public.profiles where user_id = %s", (a,)).fetchone()
    assert row is not None and row[0] == 1


def test_admin_flag_from_verified_email(client: TestClient, key: SigningKey, users: list[uuid.UUID]) -> None:
    a, _ = users
    assert client.get("/api/me", headers=h(key, a, email="Owner@Example.com")).json()["is_admin"] is True
    assert (
        client.get("/api/me", headers=h(key, a, email="owner@example.com", verified=False)).json()["is_admin"] is False
    )


def test_patch_me_updates_and_merges_settings(client: TestClient, key: SigningKey, users: list[uuid.UUID]) -> None:
    a, _ = users
    res = client.patch(
        "/api/me",
        headers=h(key, a),
        json={
            "display_name": "  Asha   R ",
            "timezone": "Europe/London",
            "settings": {"ink": {"penOnly": True}, "x": 1},
        },
    )
    assert res.status_code == 200, res.text
    p = res.json()["profile"]
    assert p["display_name"] == "Asha R"
    assert p["timezone"] == "Europe/London"
    assert p["settings"] == {"ink": {"penOnly": True}, "x": 1}
    res = client.patch("/api/me", headers=h(key, a), json={"settings": {"x": None, "theme": "dark"}})
    assert res.json()["profile"]["settings"] == {"ink": {"penOnly": True}, "theme": "dark"}
    res = client.patch("/api/me", headers=h(key, a), json={"display_name": None})
    assert res.json()["profile"]["display_name"] is None
    assert res.json()["profile"]["timezone"] == "Europe/London"  # untouched when not sent


def test_settings_total_size_is_capped(client: TestClient, key: SigningKey, users: list[uuid.UUID]) -> None:
    a, _ = users
    chunk = "y" * 15000
    for i in range(4):
        assert client.patch("/api/me", headers=h(key, a), json={"settings": {f"k{i}": chunk}}).status_code == 200
    res = client.patch("/api/me", headers=h(key, a), json={"settings": {"k9": chunk}})
    assert res.status_code == 422
    # rolled back: the oversized merge did not persist
    assert "k9" not in client.get("/api/me", headers=h(key, a)).json()["profile"]["settings"]


def test_deleted_account_with_valid_token_is_401(
    client: TestClient, key: SigningKey, pg: psycopg.Connection[Any]
) -> None:
    ghost = uuid.uuid4()  # never existed in auth.users
    res = client.get("/api/me", headers=h(key, ghost))
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "account_missing"
    assert pg.execute("select count(*) from public.profiles where user_id = %s", (ghost,)).fetchone() == (0,)


async def test_user_tx_applies_rls_not_the_pool_role(url: str, users: list[uuid.UUID]) -> None:
    a, b = users
    db = Database(SecretStr(url))
    await db.open()
    try:
        for uid in (a, b):
            async with user_tx(db, uid) as conn:
                await conn.execute("insert into public.profiles (user_id) values ((select auth.uid()))")

        async with user_tx(db, a) as conn:
            who = await (await conn.execute("select current_user as r, (select auth.uid()) as uid")).fetchone()
            assert who == {"r": "authenticated", "uid": a}
            rows = await (await conn.execute("select user_id from public.profiles")).fetchall()
            assert rows == [{"user_id": a}]  # B's row exists but is invisible
            upd = await conn.execute("update public.profiles set display_name = 'pwned' where user_id = %s", (b,))
            assert upd.rowcount == 0
            dele = await conn.execute("delete from public.profiles where user_id = %s", (b,))
            assert dele.rowcount == 0

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with user_tx(db, a) as conn:
                await conn.execute("insert into public.profiles (user_id) values (%s)", (b,))

        # The pooled connection is clean afterwards: back to the pool's own role, no claims left behind.
        assert db.pool is not None
        async with db.pool.connection() as conn:
            row = await (
                await conn.execute("select current_user, coalesce(current_setting('request.jwt.claims', true), '')")
            ).fetchone()
            assert row is not None
            assert row[0] != "authenticated"
            assert row[1] == ""
            sub = await (
                await conn.execute("select coalesce(current_setting('request.jwt.claim.sub', true), '')")
            ).fetchone()
            assert sub == ("",)
            name = await (
                await conn.execute("select display_name from public.profiles where user_id = %s", (b,))
            ).fetchone()
            assert name == (None,)
    finally:
        await db.close()


def test_anon_role_cannot_read_profiles(pg: psycopg.Connection[Any], users: list[uuid.UUID]) -> None:
    def read_as_anon() -> None:
        with pg.transaction():
            pg.execute("set local role anon")
            pg.execute("select * from public.profiles")

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        read_as_anon()
