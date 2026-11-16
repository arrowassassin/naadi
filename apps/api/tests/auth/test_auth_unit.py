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

from naadi_api.auth import AuthError, AuthUnavailable, TokenVerifier, parse_admin_emails, user_from_claims
from naadi_api.auth_limits import RateLimited, RateLimiter
from naadi_api.db import Database
from naadi_api.main import create_app
from naadi_api.settings import Settings
from tests.conftest import LogCapture, fake_service, make_settings

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


def test_admin_requires_verified_email_case_insensitive() -> None:
    admins = parse_admin_emails(ADMIN)
    assert user_from_claims(claims(email="OWNER@example.COM"), admins).is_admin is True
    assert user_from_claims(claims(email="owner@example.com", verified=False), admins).is_admin is False
    assert user_from_claims(claims(email="someone@example.com"), admins).is_admin is False
    assert user_from_claims(claims(email=None), admins).is_admin is False
    # email_verified must be literally true, not a truthy string
    c = claims(email="owner@example.com")
    c["user_metadata"] = {"email_verified": "true"}
    assert user_from_claims(c, admins).is_admin is False


def test_current_user_repr_has_no_email() -> None:
    u = user_from_claims(claims(email="private.person@example.com"), frozenset())
    assert "private.person" not in repr(u)


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
def auth_client(static_dir: Path, signing_key: SigningKey) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def make(**kw: Any) -> TestClient:
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
        app = create_app(s, health=fake_service(), db=Database(None), verifier=v)
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
    assert body["features"] == {"assistant": False, "search": False}
    assert body["source"] == "server_default"


def test_admin_route_403_for_non_admin_200_for_admin(
    auth_client: Callable[..., TestClient], signing_key: SigningKey
) -> None:
    c = auth_client()
    res = c.get("/api/admin/overview", headers=bearer(mint(signing_key, claims(email="student@example.com"))))
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "forbidden"
    unverified = mint(signing_key, claims(email="owner@example.com", verified=False))
    assert c.get("/api/admin/overview", headers=bearer(unverified)).status_code == 403
    ok = c.get("/api/admin/overview", headers=bearer(mint(signing_key, claims(email="owner@EXAMPLE.com"))))
    assert ok.status_code == 200
    assert ok.json() == {"environment": "prod", "version": "test-1", "admins_configured": 1, "profiles": 0}


def test_me_without_database_is_503(auth_client: Callable[..., TestClient], signing_key: SigningKey) -> None:
    res = auth_client().get("/api/me", headers=bearer(mint(signing_key, claims())))
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
def test_patch_me_validation(auth_client: Callable[..., TestClient], signing_key: SigningKey, body: Any) -> None:
    res = auth_client().patch("/api/me", headers=bearer(mint(signing_key, claims())), json=body)
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
    assert res.headers["cache-control"] == "public, max-age=300"
    assert res.json() == {
        "auth_configured": True,
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": "public-anon-key",
        "google_oauth_enabled": True,
        "app_base_url": "https://naadi.example.org",
        "app_version": "test-1",
        "features": {"ink_lab": False},
    }


def test_public_config_google_off_and_unconfigured(auth_client: Callable[..., TestClient]) -> None:
    body = auth_client(google_oauth_configured=False, supabase_url=None, app_env="local").get("/api/public-config")
    assert body.json()["google_oauth_enabled"] is False
    assert body.json()["auth_configured"] is False
    assert body.json()["features"] == {"ink_lab": True}


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
    auth_client: Callable[..., TestClient], signing_key: SigningKey, logs: LogCapture
) -> None:
    c = auth_client()
    good = mint(signing_key, claims(email="owner@example.com"))
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
    with TestClient(create_app(s, health=fake_service(), db=Database(None)), raise_server_exceptions=False) as c:
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
