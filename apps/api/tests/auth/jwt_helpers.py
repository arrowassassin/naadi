"""Test-only signing keys and token minting that mirror Supabase's access tokens (ES256 + JWKS)."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm

SUPABASE_URL = "https://testproject.supabase.co"
ISSUER = f"{SUPABASE_URL}/auth/v1"
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"


@dataclass
class SigningKey:
    kid: str = field(default_factory=lambda: uuid.uuid4().hex)
    private: ec.EllipticCurvePrivateKey = field(default_factory=lambda: ec.generate_private_key(ec.SECP256R1()))

    def jwk(self) -> dict[str, Any]:
        pub = json.loads(ECAlgorithm.to_jwk(self.private.public_key()))
        return {**pub, "kid": self.kid, "alg": "ES256", "use": "sig", "key_ops": ["verify"]}


def claims(
    sub: uuid.UUID | str | None = None,
    *,
    email: str | None = "student@example.com",
    verified: bool = True,
    ttl_s: int = 3600,
    now: float | None = None,
    **extra: Any,
) -> dict[str, Any]:
    t = int(now if now is not None else time.time())
    c: dict[str, Any] = {
        "iss": ISSUER,
        "aud": "authenticated",
        "sub": str(sub or uuid.uuid4()),
        "role": "authenticated",
        "iat": t,
        "exp": t + ttl_s,
        "aal": "aal1",
        "session_id": str(uuid.uuid4()),
        "is_anonymous": False,
        "app_metadata": {"provider": "email", "providers": ["email"]},
        "user_metadata": {"email_verified": verified} if email else {},
    }
    if email is not None:
        c["email"] = email
    c.update(extra)
    return c


def mint(key: SigningKey, payload: dict[str, Any], *, headers: dict[str, Any] | None = None) -> str:
    h = {"kid": key.kid, "typ": "JWT"}
    h.update(headers or {})
    return jwt.encode(payload, key.private, algorithm="ES256", headers=h)


class JwksServer:
    """An httpx transport serving a JWKS document; counts fetches and can be switched to fail."""

    def __init__(self, *keys: SigningKey) -> None:
        self.keys = list(keys)
        self.fetches = 0
        self.fail = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == JWKS_URL
        self.fetches += 1
        if self.fail:
            return httpx.Response(503, json={"error": "down"})
        return httpx.Response(200, json={"keys": [k.jwk() for k in self.keys]})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)
