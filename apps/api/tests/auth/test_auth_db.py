"""DB tests (local Postgres with the migrated schema and Supabase's auth schema): `/api/me`, the account
directory (sessions, confirmed email) and RLS really applied.

Runs in the CI `db` job and locally against a LOCAL database only (`require_local`): Postgres from
`tests/db/stack.sh up`, with the auth schema migrated by `tests/auth/auth_stack.sh up` (Supabase's auth
server), as on the hosted project. Tokens are minted locally and verified against a test JWKS, so the full
HTTP path runs: bearer token -> JWKS verification -> session + account lookup in `auth` -> `user_tx`
(role authenticated + claims) -> RLS. Not marked `integration`: no external service is involved.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from naadi_api.auth import AccountDirectory, CurrentUser, TokenVerifier, parse_admin_emails
from naadi_api.auth_db import AccountNotChecked, user_tx
from naadi_api.db import Database
from naadi_api.main import create_app
from tests.conftest import fake_service, make_settings

from .accounts import checked_user
from .jwt_helpers import SUPABASE_URL, JwksServer, SigningKey, claims, mint

pytestmark = [pytest.mark.db]

ADMIN = "owner@example.com"


@pytest.fixture(scope="module")
def url() -> str:
    # seg02-db's helpers (tests/db/rls.py); imported here so unit-only runs do not need them.
    from tests.db.rls import database_url, ensure_migrated, require_local

    u = database_url()
    require_local(u)
    ensure_migrated(u)
    with psycopg.connect(u, autocommit=True) as conn:
        row = conn.execute(
            "select to_regclass('auth.sessions') is not null and exists (select 1 from information_schema.columns"
            " where table_schema = 'auth' and table_name = 'users' and column_name = 'email_confirmed_at')"
        ).fetchone()
    if not (row and row[0]):
        pytest.fail("the auth schema is not migrated: run apps/api/tests/auth/auth_stack.sh up (or ci)")
    return u


@pytest.fixture
def pg(url: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(url, autocommit=True) as conn:
        yield conn


@pytest.fixture
def users(pg: psycopg.Connection[Any]) -> Iterator[list[uuid.UUID]]:
    """A student and a second user, both with confirmed emails (as after a magic-link sign-in)."""
    from tests.db.rls import create_auth_user

    ids = [create_auth_user(pg, email=f"{uuid.uuid4().hex[:12]}@example.com") for _ in range(2)]
    pg.execute("update auth.users set email_confirmed_at = now() where id = any(%s)", (ids,))
    yield ids
    pg.execute("delete from auth.users where id = any(%s)", (ids,))


@pytest.fixture
def key() -> SigningKey:
    return SigningKey()


def make_client(url: str, key: SigningKey, static_dir: Path, *, ttl_s: float = 30.0) -> TestClient:
    s = make_settings(static_dir=static_dir, supabase_url=SUPABASE_URL, database_url=SecretStr(url), admin_emails=ADMIN)
    db = Database(s.database_url)
    v = TokenVerifier(SUPABASE_URL, transport=JwksServer(key).transport())
    accounts = AccountDirectory(db, parse_admin_emails(ADMIN), ttl_s=ttl_s)
    return TestClient(
        create_app(s, health=fake_service(), db=db, verifier=v, accounts=accounts), raise_server_exceptions=False
    )


@pytest.fixture
def client(url: str, key: SigningKey, static_dir: Path) -> Iterator[TestClient]:
    with make_client(url, key, static_dir) as c:
        yield c


def open_session(pg: psycopg.Connection[Any], uid: uuid.UUID) -> str:
    sid = uuid.uuid4()
    pg.execute(
        "insert into auth.sessions (id, user_id, created_at, updated_at) values (%s, %s, now(), now())", (sid, uid)
    )
    return str(sid)


def email_of(pg: psycopg.Connection[Any], uid: uuid.UUID) -> str:
    row = pg.execute("select email from auth.users where id = %s", (uid,)).fetchone()
    assert row is not None
    return str(row[0])


def h(key: SigningKey, pg: psycopg.Connection[Any], uid: uuid.UUID, **kw: Any) -> dict[str, str]:
    """A token for a fresh live session of `uid`, carrying the account's email as Supabase issues it.
    For a user who does not exist (a deleted account) the token names a session that does not exist."""
    row = pg.execute("select email from auth.users where id = %s", (uid,)).fetchone()
    if row is not None:
        kw.setdefault("email", row[0])
        kw.setdefault("session_id", open_session(pg, uid))
    else:
        kw.setdefault("session_id", str(uuid.uuid4()))
    return {"authorization": f"Bearer {mint(key, claims(uid, **kw))}"}


def test_me_creates_profile_once(
    client: TestClient, key: SigningKey, users: list[uuid.UUID], pg: psycopg.Connection[Any]
) -> None:
    a, _ = users
    first = client.get("/api/me", headers=h(key, pg, a))
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["profile"]["user_id"] == str(a)
    assert body["profile"]["exam_target"] == "step1"
    assert body["profile"]["settings"] == {}
    assert body["email"] == email_of(pg, a)
    assert body["email_verified"] is True
    assert body["is_admin"] is False
    second = client.get("/api/me", headers=h(key, pg, a))
    assert second.json()["profile"]["created_at"] == body["profile"]["created_at"]
    row = pg.execute("select count(*) from public.profiles where user_id = %s", (a,)).fetchone()
    assert row is not None and row[0] == 1


def test_admin_comes_from_auth_users_not_the_token(
    client: TestClient, key: SigningKey, pg: psycopg.Connection[Any], users: list[uuid.UUID]
) -> None:
    a, _ = users
    # A student whose token claims the owner's address and email_verified: still not an admin.
    forged = h(key, pg, a, email=ADMIN, user_metadata={"email_verified": True})
    assert client.get("/api/me", headers=forged).json()["is_admin"] is False
    # The owner's account, unconfirmed: not an admin; confirmed: admin, whatever user_metadata says.
    pg.execute("update auth.users set email = %s, email_confirmed_at = null where id = %s", ("Owner@Example.com", a))
    assert client.get("/api/me", headers=h(key, pg, a)).json()["is_admin"] is False
    pg.execute("update auth.users set email_confirmed_at = now() where id = %s", (a,))
    me = client.get("/api/me", headers=h(key, pg, a, user_metadata={"email_verified": False})).json()
    assert (me["is_admin"], me["email_verified"]) == (True, True)
    assert client.get("/api/admin/overview", headers=h(key, pg, a)).status_code == 200


def test_signing_out_everywhere_revokes_api_access(
    url: str, key: SigningKey, static_dir: Path, pg: psycopg.Connection[Any], users: list[uuid.UUID]
) -> None:
    a, _ = users
    with make_client(url, key, static_dir, ttl_s=0) as c:
        headers = h(key, pg, a)
        assert c.get("/api/me", headers=headers).status_code == 200
        pg.execute("delete from auth.sessions where user_id = %s", (a,))  # what global sign-out does
        res = c.get("/api/me", headers=headers)
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "session_revoked"


def test_patch_me_updates_and_merges_settings(
    client: TestClient, key: SigningKey, pg: psycopg.Connection[Any], users: list[uuid.UUID]
) -> None:
    a, _ = users
    res = client.patch(
        "/api/me",
        headers=h(key, pg, a),
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
    res = client.patch("/api/me", headers=h(key, pg, a), json={"settings": {"x": None, "theme": "dark"}})
    assert res.json()["profile"]["settings"] == {"ink": {"penOnly": True}, "theme": "dark"}
    res = client.patch("/api/me", headers=h(key, pg, a), json={"display_name": None})
    assert res.json()["profile"]["display_name"] is None
    assert res.json()["profile"]["timezone"] == "Europe/London"  # untouched when not sent


def test_settings_total_size_is_capped(
    client: TestClient, key: SigningKey, pg: psycopg.Connection[Any], users: list[uuid.UUID]
) -> None:
    a, _ = users
    chunk = "y" * 15000
    for i in range(4):
        assert client.patch("/api/me", headers=h(key, pg, a), json={"settings": {f"k{i}": chunk}}).status_code == 200
    res = client.patch("/api/me", headers=h(key, pg, a), json={"settings": {"k9": chunk}})
    assert res.status_code == 422
    # rolled back: the oversized merge did not persist
    assert "k9" not in client.get("/api/me", headers=h(key, pg, a)).json()["profile"]["settings"]


def test_deleted_account_with_valid_token_is_401(
    client: TestClient, key: SigningKey, pg: psycopg.Connection[Any]
) -> None:
    ghost = uuid.uuid4()  # never existed in auth.users
    res = client.get("/api/me", headers=h(key, pg, ghost))
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "account_missing"
    assert pg.execute("select count(*) from public.profiles where user_id = %s", (ghost,)).fetchone() == (0,)


async def test_user_tx_applies_rls_not_the_pool_role(url: str, users: list[uuid.UUID]) -> None:
    a, b = users
    db = Database(SecretStr(url))
    await db.open()
    try:
        for uid in (a, b):
            async with user_tx(db, checked_user(uid)) as conn:
                await conn.execute("insert into public.profiles (user_id) values ((select auth.uid()))")

        async with user_tx(db, checked_user(a)) as conn:
            who = await (await conn.execute("select current_user as r, (select auth.uid()) as uid")).fetchone()
            assert who == {"r": "authenticated", "uid": a}
            rows = await (await conn.execute("select user_id from public.profiles")).fetchall()
            assert rows == [{"user_id": a}]  # B's row exists but is invisible
            upd = await conn.execute("update public.profiles set display_name = 'pwned' where user_id = %s", (b,))
            assert upd.rowcount == 0
            dele = await conn.execute("delete from public.profiles where user_id = %s", (b,))
            assert dele.rowcount == 0

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with user_tx(db, checked_user(a)) as conn:
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


async def test_user_tx_refuses_a_user_whose_session_was_not_checked(url: str, users: list[uuid.UUID]) -> None:
    """No route can reach user data with only a verified token: the session must have been checked."""
    a, _ = users
    db = Database(SecretStr(url))
    token_only = CurrentUser(user_id=a, email=None, email_verified=False, is_admin=False)
    try:
        with pytest.raises(AccountNotChecked):
            async with user_tx(db, token_only):
                pass
    finally:
        await db.close()


def _poll_status(fn: Any, want: int, timeout_s: float = 3.0) -> Any:
    deadline = time.monotonic() + timeout_s
    while True:
        res = fn()
        if res.status_code == want or time.monotonic() > deadline:
            return res
        time.sleep(0.2)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/courses", None),
        ("get", "/api/reading-position?chapter_id=ch-none", None),
        ("put", "/api/reading-position", {"chapter_id": "ch-none", "section_id": "sec-none", "offset": 1.0}),
        ("get", "/api/me", None),
        ("get", "/api/admin/overview", None),
    ],
    ids=["courses", "position-read", "position-write", "me", "admin"],
)
def test_every_user_data_route_refuses_a_revoked_session_within_the_cache_window(
    url: str,
    key: SigningKey,
    static_dir: Path,
    pg: psycopg.Connection[Any],
    users: list[uuid.UUID],
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    a, _ = users
    pg.execute("update auth.users set email = %s where id = %s", (ADMIN, a))  # admin, so /api/admin works
    with make_client(url, key, static_dir, ttl_s=1.0) as c:
        headers = h(key, pg, a)

        def call() -> Any:
            return getattr(c, method)(path, headers=headers, **({"json": body} if body is not None else {}))

        assert call().status_code != 401  # works while the session is live
        pg.execute("delete from auth.sessions where user_id = %s", (a,))  # "sign out of all devices"
        res = _poll_status(call, 401)
        assert res.status_code == 401, (path, res.status_code, res.text[:200])
        assert res.json()["error"]["code"] == "session_revoked"
    if path.startswith("/api/reading-position") and method == "put":
        n = pg.execute("select count(*) from public.reading_positions where user_id = %s", (a,)).fetchone()
        assert n == (0,)  # nothing was written after the revocation
