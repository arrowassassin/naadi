"""Unit tests: token verification, the CurrentUser/admin dependencies and the auth routes (no database)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from pydantic import SecretStr

from naadi_api.auth import (
    AccountDirectory,
    AuthError,
    AuthUnavailable,
    TokenVerifier,
    parse_admin_emails,
    user_from_claims,
)
from naadi_api.auth_limits import RateLimited, RateLimiter
from naadi_api.db import Database
from naadi_api.main import create_app
from naadi_api.settings import Settings
from tests.conftest import LogCapture, fake_service, make_settings

from .fake_accounts import FakeAuthDb
from .jwt_helpers import ISSUER, SUPABASE_URL, JwksServer, SigningKey, claims, mint

ADMIN = "Owner@Example.com"


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def verifier(server: JwksServer, clock: Clock | None = None, **kw: Any) -> TokenVerifier:
    return TokenVerifier(SUPABASE_URL, transport=server.transport(), clock=clock or Clock(), **kw)


# ── TokenVerifier ──


async def test_valid_token_returns_claims() -> None:
    key = SigningKey()
    v = verifier(JwksServer(key))
    sub = uuid.uuid4()
    c = await v.verify(mint(key, claims(sub)))
    assert c["sub"] == str(sub)


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda c: c.update(exp=int(time.time()) - 61, iat=int(time.time()) - 3600), "token_expired"),
        (lambda c: c.update(aud="anon"), "invalid_token"),
        (lambda c: c.update(iss="https://evil.supabase.co/auth/v1"), "invalid_token"),
        (lambda c: c.update(nbf=int(time.time()) + 120), "invalid_token"),
        (lambda c: c.update(iat=int(time.time()) + 120), "invalid_token"),
        (lambda c: c.update(role="anon"), "invalid_token"),
        (lambda c: c.update(is_anonymous=True), "invalid_token"),
        (lambda c: c.pop("exp"), "invalid_token"),
        (lambda c: c.pop("sub"), "invalid_token"),
    ],
    ids=["expired", "wrong-aud", "wrong-iss", "nbf-future", "iat-future", "role-anon", "anonymous", "no-exp", "no-sub"],
)
async def test_rejected_claims(mutate: Callable[[dict[str, Any]], Any], code: str) -> None:
    key = SigningKey()
    v = verifier(JwksServer(key))
    c = claims()
    mutate(c)
    with pytest.raises(AuthError) as ei:
        await v.verify(mint(key, c))
    assert ei.value.code == code
    assert ei.value.status == 401


async def test_clock_skew_within_60s_is_accepted() -> None:
    key = SigningKey()
    v = verifier(JwksServer(key))
    now = time.time()
    # expired 30 s ago and issued 30 s in the future: both inside the 60 s leeway
    await v.verify(mint(key, claims(now=now - 3600, ttl_s=3600 - 30)))
    await v.verify(mint(key, claims(now=now + 30)))


async def test_bad_signature_is_rejected() -> None:
    published, attacker = SigningKey(), SigningKey()
    attacker.kid = published.kid  # same kid, different private key
    v = verifier(JwksServer(published))
    with pytest.raises(AuthError) as ei:
        await v.verify(mint(attacker, claims()))
    assert ei.value.code == "invalid_token"


async def test_tampered_payload_is_rejected() -> None:
    key = SigningKey()
    v = verifier(JwksServer(key))
    token = mint(key, claims(email="student@example.com"))
    h, _p, s = token.split(".")
    forged = jwt.encode(claims(email="owner@example.com"), "k" * 32, algorithm="HS256").split(".")[1]
    with pytest.raises(AuthError):
        await v.verify(f"{h}.{forged}.{s}")


@pytest.mark.parametrize(
    "token",
    ["", "abc", "a.b", "a.b.c.d", "x" * 9000, "eyJhbGciOiJub25lIn0.eyJzdWIiOiJ4In0."],
    ids=["empty", "garbage", "two-parts", "four-parts", "huge", "alg-none"],
)
async def test_malformed_tokens(token: str) -> None:
    v = verifier(JwksServer(SigningKey()))
    with pytest.raises(AuthError):
        await v.verify(token)


async def test_hs256_rejected_without_secret_and_accepted_with_it() -> None:
    token = jwt.encode(claims(), "the-legacy-secret-at-least-32-bytes-long!", algorithm="HS256")
    with pytest.raises(AuthError):
        await verifier(JwksServer()).verify(token)
    v = verifier(JwksServer(), jwt_secret=SecretStr("the-legacy-secret-at-least-32-bytes-long!"))
    assert (await v.verify(token))["role"] == "authenticated"


async def test_alg_confusion_hs256_with_public_key_is_rejected() -> None:
    """Classic attack: HMAC-sign with the published public key as the secret and claim alg=HS256."""
    key = SigningKey()
    pem = key.private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": key.kid}).encode())
    payload = _b64(json.dumps(claims()).encode())
    sig = _b64(hmac.new(pem, f"{header}.{payload}".encode(), hashlib.sha256).digest())
    with pytest.raises(AuthError):
        await verifier(JwksServer(key)).verify(f"{header}.{payload}.{sig}")
    # Even with a legacy HS256 secret configured, the public key is not that secret.
    with pytest.raises(AuthError):
        await verifier(JwksServer(key), jwt_secret=SecretStr("a-different-legacy-secret-32-bytes!!")).verify(
            f"{header}.{payload}.{sig}"
        )


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


async def test_jwks_is_cached_and_refetched_on_unknown_kid() -> None:
    old, new = SigningKey(), SigningKey()
    server = JwksServer(old)
    clock = Clock()
    v = verifier(server, clock)
    for _ in range(3):
        await v.verify(mint(old, claims()))
    assert server.fetches == 1

    # Rotation: Supabase publishes the new key; a token with the new kid triggers one refetch.
    server.keys = [old, new]
    clock.t += 31
    await v.verify(mint(new, claims()))
    assert server.fetches == 2


async def test_unknown_kid_refetch_is_rate_limited() -> None:
    key = SigningKey()
    server = JwksServer(key)
    clock = Clock()
    v = verifier(server, clock)
    await v.verify(mint(key, claims()))
    stranger = SigningKey()
    for _ in range(5):
        with pytest.raises(AuthError) as ei:
            await v.verify(mint(stranger, claims()))
        assert ei.value.code == "invalid_token"
    assert server.fetches == 1  # within the 30 s window: no extra fetches for random kids
    clock.t += 31
    with pytest.raises(AuthError):
        await v.verify(mint(stranger, claims()))
    assert server.fetches == 2


async def test_jwks_outage_uses_cached_key_and_fails_closed_without_one() -> None:
    key = SigningKey()
    server = JwksServer(key)
    clock = Clock()
    v = verifier(server, clock)
    await v.verify(mint(key, claims()))
    server.fail = True
    clock.t += 601  # cache is stale, refetch fails: the previously published key still verifies
    await v.verify(mint(key, claims()))

    cold = verifier(server, Clock())
    with pytest.raises(AuthUnavailable) as ei:
        await cold.verify(mint(key, claims()))
    assert ei.value.status == 503


async def test_unconfigured_verifier_is_unavailable() -> None:
    with pytest.raises(AuthUnavailable):
        await TokenVerifier(None).verify("a.b.c")


# ── Claims → CurrentUser, ADMIN_EMAILS ──


def test_admin_emails_parsing() -> None:
    assert parse_admin_emails(None) == frozenset()
    assert parse_admin_emails(SecretStr(" A@x.com, b@Y.org;c@z.io\nnot-an-email ,, ")) == {
        "a@x.com",
        "b@y.org",
        "c@z.io",
    }


def test_token_alone_never_decides_admin_or_verified() -> None:
    u = user_from_claims(claims(email="owner@example.com", user_metadata={"email_verified": True}))
    assert (u.is_admin, u.email_verified) == (False, False)


def test_current_user_repr_has_no_email() -> None:
    u = user_from_claims(claims(email="private.person@example.com"))
    assert "private.person" not in repr(u)


async def _resolve(db: FakeAuthDb, token_claims: dict[str, Any], **kw: Any) -> Any:
    return await AccountDirectory(db, parse_admin_emails(ADMIN), **kw).resolve(user_from_claims(token_claims))


async def test_admin_needs_a_confirmed_listed_email_in_auth_users() -> None:
    db = FakeAuthDb()
    owner = db.add_user("Owner@Example.com")
    unconfirmed = db.add_user("owner@example.com", confirmed=False)
    student = db.add_user("student@example.com")
    for uid, admin in ((owner, True), (unconfirmed, False), (student, False)):
        u = await _resolve(db, claims(uid, session_id=db.open_session(uid)))
        assert u.is_admin is admin


async def test_user_metadata_cannot_grant_or_revoke_admin() -> None:
    db = FakeAuthDb()
    student = db.add_user("student@example.com", confirmed=False)
    # The token claims a verified owner address: user_metadata and the token email are ignored.
    forged = claims(student, session_id=db.open_session(student), email="owner@example.com")
    forged["user_metadata"] = {"email_verified": True}
    u = await _resolve(db, forged)
    assert (u.is_admin, u.email_verified, u.email) == (False, False, "student@example.com")
    # And a real admin stays admin even if they set email_verified=false in their own metadata.
    owner = db.add_user("owner@example.com")
    c = claims(owner, session_id=db.open_session(owner), email="owner@example.com")
    c["user_metadata"] = {"email_verified": False}
    assert (await _resolve(db, c)).is_admin is True


async def test_revoked_or_missing_session_is_401() -> None:
    db = FakeAuthDb()
    uid = db.add_user("student@example.com")
    sid = db.open_session(uid)
    clock = Clock()
    directory = AccountDirectory(db, frozenset(), ttl_s=30, clock=clock)
    await directory.resolve(user_from_claims(claims(uid, session_id=sid)))
    del db.sessions[sid]  # "sign out of all devices"
    clock.t += 29  # still cached
    await directory.resolve(user_from_claims(claims(uid, session_id=sid)))
    clock.t += 2  # cache expired: the revocation is seen
    with pytest.raises(AuthError) as ei:
        await directory.resolve(user_from_claims(claims(uid, session_id=sid)))
    assert (ei.value.code, ei.value.status) == ("session_revoked", 401)
    no_sid = claims(uid)
    no_sid.pop("session_id")
    with pytest.raises(AuthError) as ei:
        await directory.resolve(user_from_claims(no_sid))
    assert ei.value.code == "session_revoked"
    with pytest.raises(AuthError) as ei:
        await directory.resolve(user_from_claims(claims(uuid.uuid4(), session_id=sid)))
    assert ei.value.code == "account_missing"


async def test_lookups_are_cached_per_session() -> None:
    db = FakeAuthDb()
    uid = db.add_user("student@example.com")
    sid = db.open_session(uid)
    directory = AccountDirectory(db, frozenset(), clock=Clock())
    for _ in range(5):
        await directory.resolve(user_from_claims(claims(uid, session_id=sid)))
    assert db.queries == 1


async def test_directory_without_database_is_unavailable() -> None:
    with pytest.raises(AuthUnavailable):
        await AccountDirectory(Database(None), frozenset()).resolve(
            user_from_claims(claims(session_id=str(uuid.uuid4())))
        )


def test_rate_limiter_window() -> None:
    clock = Clock()
    rl = RateLimiter(2, 10.0, clock=clock)
    rl.hit("u")
    rl.hit("u")
    with pytest.raises(RateLimited) as ei:
        rl.hit("u")
    assert 1 <= ei.value.retry_after_s <= 10
    rl.hit("other")
    clock.t += 10.01
    rl.hit("u")


def test_rate_limiter_bounds_memory() -> None:
    rl = RateLimiter(1, 60.0, max_keys=3)
    for k in "abcd":
        rl.hit(k)
    rl.hit("a")  # "a" was evicted as least recently seen, so it starts fresh


# ── Routes (TestClient, no database) ──


@pytest.fixture
def signing_key() -> SigningKey:
    return SigningKey()


@pytest.fixture
def accounts() -> FakeAuthDb:
    return FakeAuthDb()


@pytest.fixture
def auth_client(static_dir: Path, signing_key: SigningKey, accounts: FakeAuthDb) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def make(*, real_accounts: bool = False, **kw: Any) -> TestClient:
        base: dict[str, Any] = {
            "static_dir": static_dir,
            "supabase_url": SUPABASE_URL,
            "supabase_anon_key": "public-anon-key",
            "admin_emails": ADMIN,
            "google_oauth_configured": True,
            "app_version": "test-1",
        }
        base.update(kw)
        s: Settings = make_settings(**base)
        v = TokenVerifier(SUPABASE_URL, transport=JwksServer(signing_key).transport())
        directory = None if real_accounts else AccountDirectory(accounts, parse_admin_emails(s.admin_emails))
        app = create_app(s, health=fake_service(), db=Database(None), verifier=v, accounts=directory)
        c = TestClient(app, raise_server_exceptions=False)
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


PROTECTED = [("get", "/api/me"), ("patch", "/api/me"), ("get", "/api/nav"), ("get", "/api/admin/overview")]


@pytest.mark.parametrize(("method", "path"), PROTECTED)
@pytest.mark.parametrize(
    "header",
    [None, "Basic abc", "Bearer", "Bearer not.a.jwt", "bearer a.b.c"],
    ids=["missing", "basic", "empty-bearer", "garbage", "lowercase-garbage"],
)
def test_protected_routes_reject_bad_credentials(
    auth_client: Callable[..., TestClient], method: str, path: str, header: str | None
) -> None:
    c = auth_client()
    headers = {"authorization": header} if header else {}
    res = getattr(c, method)(path, headers=headers, **({"json": {}} if method == "patch" else {}))
    assert res.status_code == 401
    body = res.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] in {"unauthorized", "invalid_token"}
    assert body["error"]["request_id"] == res.headers["x-request-id"]
    assert res.headers["www-authenticate"].startswith("Bearer ")
    assert res.headers["cache-control"] == "no-store"


def test_expired_token_401_with_code(auth_client: Callable[..., TestClient], signing_key: SigningKey) -> None:
    t = int(time.time())
    token = mint(signing_key, claims(now=t - 7200, ttl_s=3600))
    res = auth_client().get("/api/nav", headers=bearer(token))
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "token_expired"
    assert 'error="invalid_token"' in res.headers["www-authenticate"]


def test_token_from_another_project_is_rejected(
    auth_client: Callable[..., TestClient], signing_key: SigningKey
) -> None:
    token = mint(signing_key, claims(iss="https://otherproject.supabase.co/auth/v1"))
    assert auth_client().get("/api/nav", headers=bearer(token)).status_code == 401


def test_nav_returns_only_enabled_items(auth_client: Callable[..., TestClient], signing_key: SigningKey) -> None:
    res = auth_client().get("/api/nav", headers=bearer(mint(signing_key, claims())))
    assert res.status_code == 200
    body = res.json()
    assert [i["id"] for i in body["items"]] == ["today", "learn"]
    assert body["items"][0]["path"] == "/"
    # no database in unit tests: app_config's logged fail-safe (config/app_config.yaml) answers
    assert body["features"]["assistant"] is False
    assert body["features"]["today"] is True
    assert body["source"] == "file_fallback"


def test_admin_route_403_for_non_admin_200_for_admin(
    auth_client: Callable[..., TestClient], signing_key: SigningKey, accounts: FakeAuthDb
) -> None:
    c = auth_client()
    res = c.get("/api/admin/overview", headers=bearer(accounts.login(signing_key, "student@example.com")))
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "forbidden"
    unconfirmed = accounts.login(signing_key, "owner@example.com", confirmed=False)
    assert c.get("/api/admin/overview", headers=bearer(unconfirmed)).status_code == 403
    ok = c.get("/api/admin/overview", headers=bearer(accounts.login(signing_key, "owner@EXAMPLE.com")))
    assert ok.status_code == 200
    assert ok.json() == {"environment": "prod", "version": "test-1", "admins_configured": 1, "profiles": 0}


def test_revoked_session_is_401_on_account_routes(
    auth_client: Callable[..., TestClient], signing_key: SigningKey, accounts: FakeAuthDb
) -> None:
    c = auth_client()
    token = accounts.login(signing_key, "owner@example.com")
    assert c.get("/api/admin/overview", headers=bearer(token)).status_code == 200
    c.app.state.accounts._cache.clear()  # type: ignore[attr-defined]
    accounts.sessions.clear()
    for method, path in (("get", "/api/admin/overview"), ("get", "/api/me"), ("patch", "/api/me")):
        res = getattr(c, method)(path, headers=bearer(token), **({"json": {}} if method == "patch" else {}))
        assert res.status_code == 401, path
        assert res.json()["error"]["code"] == "session_revoked"


def test_me_without_database_is_503(auth_client: Callable[..., TestClient], signing_key: SigningKey) -> None:
    res = auth_client(real_accounts=True).get("/api/me", headers=bearer(mint(signing_key, claims())))
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "auth_unavailable"
    assert res.headers["retry-after"]


@pytest.mark.parametrize(
    "body",
    [
        {"timezone": "Mars/Olympus"},
        {"timezone": "../../etc/passwd"},
        {"timezone": None},
        {"display_name": "x" * 121},
        {"settings": {"blob": "x" * 17000}},
        {"settings": "not-an-object"},
        {"email": "new@example.com"},
        {"is_admin": True},
        {"user_id": str(uuid.uuid4())},
    ],
    ids=["bad-tz", "path-tz", "null-tz", "long-name", "big-settings", "settings-type", "email", "is_admin", "user_id"],
)
def test_patch_me_validation(
    auth_client: Callable[..., TestClient], signing_key: SigningKey, accounts: FakeAuthDb, body: Any
) -> None:
    token = accounts.login(signing_key, "student@example.com")
    res = auth_client().patch("/api/me", headers=bearer(token), json=body)
    assert res.status_code == 422, res.text
    assert res.json()["error"]["code"] == "validation_error"
    assert "x" * 50 not in res.text  # never echoes input


def test_rate_limit_per_user(auth_client: Callable[..., TestClient], signing_key: SigningKey) -> None:
    c = auth_client()
    c.app.state.rate_limiters["me_read"] = RateLimiter(3, 60.0)  # type: ignore[attr-defined]
    a, b = mint(signing_key, claims()), mint(signing_key, claims())
    for _ in range(3):
        assert c.get("/api/nav", headers=bearer(a)).status_code == 200
    res = c.get("/api/nav", headers=bearer(a))
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "rate_limited"
    assert int(res.headers["retry-after"]) >= 1
    assert c.get("/api/nav", headers=bearer(b)).status_code == 200  # another user is unaffected


def test_public_config(auth_client: Callable[..., TestClient]) -> None:
    res = auth_client(app_base_url="https://naadi.example.org/").get("/api/public-config")
    assert res.status_code == 200
    assert res.headers["cache-control"] == "public, max-age=60"
    body = res.json()
    # flags, nav, exam rules and UI copy come from app_config (S05); checked in tests/config_api and tests/db
    assert body.pop("features")["ink_lab"] is False  # admins only in prod
    assert [i["id"] for i in body.pop("nav")] == ["today", "learn"]
    assert body.pop("exam_rules")["modes"]["exam"]["assistant"] is False
    assert body.pop("ui_copy")["signin.tagline"]
    assert body == {
        "auth_configured": True,
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": "public-anon-key",
        "google_oauth_enabled": True,
        "app_base_url": "https://naadi.example.org",
        "app_version": "test-1",
    }


def test_public_config_google_off_and_unconfigured(auth_client: Callable[..., TestClient]) -> None:
    body = auth_client(google_oauth_configured=False, supabase_url=None, app_env="local").get("/api/public-config")
    assert body.json()["google_oauth_enabled"] is False
    assert body.json()["auth_configured"] is False
    assert body.json()["features"]["ink_lab"] is True  # everyone outside prod


def test_public_config_has_no_secrets(auth_client: Callable[..., TestClient]) -> None:
    c = auth_client(
        supabase_service_role_key="service-role-secret-value",
        supabase_jwt_secret="jwt-secret-value-xyz",
        database_url="postgresql://u:dbpassword123@h:5432/d",
    )
    text = c.get("/api/public-config").text
    for secret in ("service-role-secret-value", "jwt-secret-value-xyz", "dbpassword123", "owner@example.com"):
        assert secret not in text.lower()


def test_tokens_and_emails_never_logged(
    auth_client: Callable[..., TestClient], signing_key: SigningKey, accounts: FakeAuthDb, logs: LogCapture
) -> None:
    c = auth_client()
    good = accounts.login(signing_key, "owner@example.com")
    bad = mint(SigningKey(), claims(email="intruder@example.com"))
    c.get("/api/admin/overview", headers=bearer(good))
    c.get("/api/nav", headers=bearer(bad))
    c.get("/api/me", headers=bearer(good))
    c.patch("/api/me", headers=bearer(good), json={"display_name": "Private Name"})
    text = logs.text
    assert text  # something was logged
    for needle in (good, bad, good.split(".")[1], "owner@example.com", "intruder@example.com", "Private Name", ISSUER):
        assert needle not in text
    reasons = [line.get("reason") for line in logs.lines() if line.get("event") == "auth_rejected"]
    assert "invalid_token" in reasons


def test_hs256_project_tokens_work_end_to_end(static_dir: Path) -> None:
    """Legacy shared-secret projects and CI's auth container: no JWKS, HS256 with SUPABASE_JWT_SECRET."""
    secret = "ci-only-throwaway-secret-at-least-32-bytes"
    s = make_settings(static_dir=static_dir, supabase_url=SUPABASE_URL, supabase_jwt_secret=secret)
    app = create_app(s, health=fake_service(), db=Database(None))
    with TestClient(app, raise_server_exceptions=False) as c:
        good = jwt.encode(claims(), secret, algorithm="HS256")
        assert c.get("/api/nav", headers=bearer(good)).status_code == 200
        forged = jwt.encode(claims(), "another-secret-of-at-least-32-bytes!!", algorithm="HS256")
        assert c.get("/api/nav", headers=bearer(forged)).status_code == 401


@pytest.mark.parametrize(
    ("url", "origin"),
    [
        ("https://abc.supabase.co", "https://abc.supabase.co"),
        ("http://localhost:54329", "http://localhost:54329"),
        ("http://127.0.0.1:9999/", "http://127.0.0.1:9999"),
        ("http://auth.example.org", None),
    ],
)
def test_supabase_origin_allows_plain_http_only_on_loopback(url: str, origin: str | None) -> None:
    assert make_settings(supabase_url=url).supabase_origin == origin
