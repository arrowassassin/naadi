"""Supabase access-token verification, the `CurrentUser` dependency and the admin gate.

Why PyJWT and not `supabase.auth.get_claims`: the SDK (2.34.1) checks the signature and `exp` only. It
does not check `aud`, `iss` or `nbf`, applies no clock-skew leeway, and its JWKS cache is private. PyJWT
(already a dependency of `supabase-auth`) verifies all of them with one call, so we verify with PyJWT
against the project's JWKS, which we fetch and cache ourselves.

Rules:
- asymmetric keys (ES256/RS256) from `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`, cached for
  `jwks_ttl_s`, refetched on an unknown `kid` (rate-limited, so random kids cannot hammer Supabase);
- `SUPABASE_JWT_SECRET` (HS256) only as a fallback when it is configured;
- `aud = authenticated`, `iss = <SUPABASE_URL>/auth/v1`, `exp`/`nbf`/`iat` with at most 60 s skew;
- `role = authenticated`, `sub` is a UUID, anonymous sessions are refused.

Tokens and emails are never logged: failures log only a reason code.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any

import httpx
import jwt
from fastapi import Depends, Request
from pydantic import SecretStr

from .errors import error_response
from .logs import get_logger
from .settings import Settings

log = get_logger("naadi.auth")

AUDIENCE = "authenticated"
MAX_LEEWAY_S = 60
ASYMMETRIC_ALGS = frozenset({"ES256", "RS256"})
MAX_TOKEN_BYTES = 8192


class AuthError(Exception):
    """401: the request carries no usable credentials. `code` is stable and safe to show."""

    def __init__(self, code: str, message: str, status: int = 401) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.status = status


class AuthUnavailable(AuthError):
    """503: we cannot verify right now (JWKS unreachable and no cached key, or auth not configured)."""

    def __init__(self, message: str = "Sign-in cannot be checked right now. Try again in a moment.") -> None:
        super().__init__("auth_unavailable", message, status=503)


class Forbidden(AuthError):
    def __init__(self, message: str = "You do not have access to this.") -> None:
        super().__init__("forbidden", message, status=403)


def parse_admin_emails(raw: SecretStr | str | None) -> frozenset[str]:
    """`ADMIN_EMAILS`: comma, semicolon or whitespace separated; compared case-insensitively."""
    if raw is None:
        return frozenset()
    text = raw.get_secret_value() if isinstance(raw, SecretStr) else raw
    parts = text.replace(";", ",").replace("\n", ",").replace(" ", ",").split(",")
    return frozenset(p.strip().lower() for p in parts if p.strip() and "@" in p)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    user_id: uuid.UUID
    # PII: excluded from repr so it never lands in a log line or traceback by accident.
    email: str | None = field(repr=False)
    email_verified: bool
    is_admin: bool
    session_id: str | None = field(default=None, repr=False)


def _email_verified(claims: Mapping[str, Any]) -> bool:
    """Supabase puts the verified flag in `user_metadata.email_verified` (set for magic-link and OAuth
    sign-ins once the address is confirmed). Only an explicit `true` counts."""
    meta = claims.get("user_metadata")
    return isinstance(meta, Mapping) and meta.get("email_verified") is True


def user_from_claims(claims: Mapping[str, Any], admin_emails: frozenset[str]) -> CurrentUser:
    sub = claims.get("sub")
    try:
        user_id = uuid.UUID(str(sub))
    except ValueError as exc:
        raise AuthError("invalid_token", "The sign-in token is not valid.") from exc
    email_raw = claims.get("email")
    email = email_raw.strip() if isinstance(email_raw, str) and email_raw.strip() else None
    verified = email is not None and _email_verified(claims)
    is_admin = verified and email is not None and email.lower() in admin_emails
    sid = claims.get("session_id")
    return CurrentUser(
        user_id=user_id,
        email=email,
        email_verified=verified,
        is_admin=is_admin,
        session_id=sid if isinstance(sid, str) else None,
    )


class TokenVerifier:
    def __init__(
        self,
        supabase_url: str | None,
        *,
        jwt_secret: SecretStr | None = None,
        leeway_s: int = MAX_LEEWAY_S,
        jwks_ttl_s: float = 600.0,
        min_refresh_interval_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        self.issuer = f"{supabase_url.rstrip('/')}/auth/v1" if supabase_url else None
        self.jwks_url = f"{self.issuer}/.well-known/jwks.json" if self.issuer else None
        self._secret = jwt_secret
        self._leeway = min(max(leeway_s, 0), MAX_LEEWAY_S)
        self._ttl = jwks_ttl_s
        self._min_refresh = min_refresh_interval_s
        self._transport = transport
        self._clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._fetched_at: float | None = None
        self._last_attempt: float | None = None
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return self.issuer is not None

    async def _fetch_jwks(self) -> None:
        assert self.jwks_url is not None
        self._last_attempt = self._clock()
        # Raw httpx: the SDK exposes no public JWKS fetch (see the module docstring).
        async with httpx.AsyncClient(timeout=5.0, transport=self._transport) as http:
            res = await http.get(self.jwks_url, headers={"accept": "application/json"})
        res.raise_for_status()
        body = res.json()
        keys: dict[str, jwt.PyJWK] = {}
        for raw in body.get("keys", []) if isinstance(body, dict) else []:
            if not isinstance(raw, dict) or not raw.get("kid") or raw.get("use", "sig") != "sig":
                continue
            try:
                key = jwt.PyJWK(raw)
            except (jwt.PyJWKError, jwt.InvalidKeyError):
                continue
            if key.algorithm_name in ASYMMETRIC_ALGS:
                keys[str(raw["kid"])] = key
        self._keys = keys
        self._fetched_at = self._clock()

    def _fresh(self) -> bool:
        return self._fetched_at is not None and self._clock() - self._fetched_at < self._ttl

    def _may_refetch(self) -> bool:
        return self._last_attempt is None or self._clock() - self._last_attempt >= self._min_refresh

    async def _key_for(self, kid: str) -> jwt.PyJWK:
        key = self._keys.get(kid)
        if key is not None and self._fresh():
            return key
        async with self._lock:
            key = self._keys.get(kid)
            if key is not None and self._fresh():
                return key
            if self._may_refetch() or (key is None and self._fetched_at is None):
                try:
                    await self._fetch_jwks()
                except (httpx.HTTPError, ValueError) as exc:
                    log.warning("jwks_fetch_failed", error=type(exc).__name__)
                    if key is not None:
                        return key  # stale but still published a moment ago: better than failing everyone
                    if not self._keys:
                        raise AuthUnavailable() from exc
            key = self._keys.get(kid)
        if key is None:
            raise AuthError("invalid_token", "The sign-in token is not valid.")
        return key

    async def verify(self, token: str) -> dict[str, Any]:
        if not self.configured:
            raise AuthUnavailable("Sign-in is not configured on this server.")
        if len(token) > MAX_TOKEN_BYTES or token.count(".") != 2:
            raise AuthError("invalid_token", "The sign-in token is not valid.")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError("invalid_token", "The sign-in token is not valid.") from exc
        alg = header.get("alg")
        key: Any
        if alg in ASYMMETRIC_ALGS:
            kid = header.get("kid")
            if not isinstance(kid, str) or not kid:
                raise AuthError("invalid_token", "The sign-in token is not valid.")
            jwk = await self._key_for(kid)
            if jwk.algorithm_name != alg:
                raise AuthError("invalid_token", "The sign-in token is not valid.")
            key = jwk.key
        elif alg == "HS256" and self._secret is not None:
            key = self._secret.get_secret_value()
        else:
            raise AuthError("invalid_token", "The sign-in token is not valid.")
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[alg],
                audience=AUDIENCE,
                issuer=self.issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "sub", "aud", "iss"], "verify_nbf": True},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("token_expired", "Your session has expired. Sign in again.") from exc
        except jwt.PyJWTError as exc:
            raise AuthError("invalid_token", "The sign-in token is not valid.") from exc
        if claims.get("role") != "authenticated" or claims.get("is_anonymous") is True:
            raise AuthError("invalid_token", "The sign-in token is not valid.")
        return claims


def _bearer(request: Request) -> str:
    raw = request.headers.get("authorization")
    if not raw:
        raise AuthError("unauthorized", "Sign in to continue.")
    scheme, _, token = raw.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("unauthorized", "Sign in to continue.")
    return token.strip()


async def get_current_user(request: Request) -> CurrentUser:
    verifier: TokenVerifier = request.app.state.auth
    admins: frozenset[str] = request.app.state.admin_emails
    token = _bearer(request)
    try:
        claims = await verifier.verify(token)
    except AuthError as exc:
        log.info("auth_rejected", reason=exc.code)
        raise
    user = user_from_claims(claims, admins)
    request.state.user = user
    return user


async def require_admin(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
    """Server-side admin gate. The client only hides admin UI; this is the enforcement."""
    if not user.is_admin:
        log.info("admin_denied")
        raise Forbidden("This needs an admin account.")
    return user


AuthUser = Annotated[CurrentUser, Depends(get_current_user)]
AdminUser = Annotated[CurrentUser, Depends(require_admin)]


def build_verifier(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> TokenVerifier:
    return TokenVerifier(settings.supabase_url, jwt_secret=settings.supabase_jwt_secret, transport=transport)


async def _auth_error_handler(_: Request, exc: Exception) -> Any:
    assert isinstance(exc, AuthError)
    headers: dict[str, str] = {}
    if exc.status == 401:
        err = "invalid_token" if exc.code in {"invalid_token", "token_expired"} else "invalid_request"
        headers["www-authenticate"] = f'Bearer realm="naadi", error="{err}"'
    elif exc.status == 503:
        headers["retry-after"] = "5"
    return error_response(exc.status, exc.message, exc.code, headers=headers)


def install_auth(app: Any, settings: Settings, verifier: TokenVerifier | None = None) -> None:
    app.state.auth = verifier or build_verifier(settings)
    app.state.admin_emails = parse_admin_emails(settings.admin_emails)
    app.add_exception_handler(AuthError, _auth_error_handler)
