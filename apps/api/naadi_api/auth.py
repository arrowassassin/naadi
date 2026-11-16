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
- `role = authenticated`, `sub` is a UUID, anonymous sessions are refused;
- for user data and admin decisions (`AccountUser`, `AdminUser`), the session must still exist in
  `auth.sessions` and admin needs a confirmed email (`auth.users.email_confirmed_at`) listed in
  ADMIN_EMAILS: nothing user-editable in the token (`user_metadata`) decides it.

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
    # True only when `AccountDirectory.resolve` has checked the session and account in `auth`: `user_tx`
    # refuses any other user, so no route can reach user data with a revoked session.
    account_checked: bool = False


def user_from_claims(claims: Mapping[str, Any]) -> CurrentUser:
    """Identity from a verified token only. The token cannot decide `email_verified` or `is_admin`
    (`user_metadata` is editable by the user): `AccountDirectory.resolve` fills them from `auth.users`."""
    sub = claims.get("sub")
    try:
        user_id = uuid.UUID(str(sub))
    except ValueError as exc:
        raise AuthError("invalid_token", "The sign-in token is not valid.") from exc
    email_raw = claims.get("email")
    email = email_raw.strip() if isinstance(email_raw, str) and email_raw.strip() else None
    sid = claims.get("session_id")
    return CurrentUser(
        user_id=user_id,
        email=email,
        email_verified=False,
        is_admin=False,
        session_id=sid if isinstance(sid, str) and sid else None,
    )


_ACCOUNT_SQL = """
select u.email,
       u.email_confirmed_at is not null as confirmed,
       exists (
         select 1 from auth.sessions s
         where s.id = %(sid)s and s.user_id = u.id and (s.not_after is null or s.not_after > now())
       ) as live
from auth.users u
where u.id = %(uid)s
"""


@dataclass(frozen=True, slots=True)
class _Account:
    email: str | None = field(repr=False)
    confirmed: bool
    live: bool
    at: float


class AccountDirectory:
    """Account facts the token cannot be trusted with, read from Supabase's `auth` schema with the API's
    own database role (no row leaves the database except the caller's own):

    - the session still exists (`auth.sessions`, so "sign out of all devices" revokes API access too);
    - the account still exists, its email and whether that email is confirmed (`email_confirmed_at`),
      which together with ADMIN_EMAILS decide `is_admin`.

    Results are cached per (user, session) for `ttl_s` (default 30 s), so a revoked session stops
    working within that time; the cache is bounded.
    """

    def __init__(
        self,
        db: Any,
        admin_emails: frozenset[str],
        *,
        ttl_s: float = 30.0,
        max_entries: int = 10_000,
        clock: Any = time.monotonic,
    ) -> None:
        self._db = db
        self._admins = admin_emails
        self._ttl = ttl_s
        self._max = max_entries
        self._clock = clock
        self._cache: dict[tuple[uuid.UUID, str], _Account] = {}

    async def _fetch(self, user_id: uuid.UUID, session_id: str) -> _Account | None:
        import psycopg  # local: keeps `naadi_api.auth` importable without the DB driver loaded

        if not getattr(self._db, "configured", False):
            raise AuthUnavailable("Your account cannot be checked right now. Try again in a moment.")
        if self._db.pool is None:
            await self._db.open()
        try:
            async with self._db.pool.connection(timeout=5.0) as conn:
                cur = await conn.execute(_ACCOUNT_SQL, {"uid": user_id, "sid": session_id})
                row = await cur.fetchone()
        except (psycopg.OperationalError, TimeoutError) as exc:
            log.warning("account_lookup_unavailable", error=type(exc).__name__)
            raise AuthUnavailable("Your account cannot be checked right now. Try again in a moment.") from exc
        except psycopg.errors.InvalidTextRepresentation:
            return _Account(email=None, confirmed=False, live=False, at=self._clock())
        if row is None:
            return None
        return _Account(email=row[0], confirmed=bool(row[1]), live=bool(row[2]), at=self._clock())

    async def resolve(self, user: CurrentUser) -> CurrentUser:
        if not user.session_id:
            raise AuthError("session_revoked", "This session has ended. Sign in again.")
        key = (user.user_id, user.session_id)
        acct = self._cache.get(key)
        if acct is None or self._clock() - acct.at >= self._ttl:
            fetched = await self._fetch(user.user_id, user.session_id)
            if fetched is None:
                self._cache.pop(key, None)
                raise AuthError("account_missing", "This account no longer exists. Sign in again.")
            acct = fetched
            if len(self._cache) >= self._max:
                self._cache.clear()
            self._cache[key] = acct
        if not acct.live:
            raise AuthError("session_revoked", "This session has ended. Sign in again.")
        email = acct.email.strip() if acct.email else None
        verified = email is not None and acct.confirmed
        return CurrentUser(
            user_id=user.user_id,
            email=email,
            email_verified=verified,
            is_admin=verified and email is not None and email.lower() in self._admins,
            session_id=user.session_id,
            account_checked=True,
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
    """A verified token's identity (no database). Routes that read or write user data, or decide admin,
    use `AccountUser` / `AdminUser`, which also check the session and account in `auth`."""
    verifier: TokenVerifier = request.app.state.auth
    token = _bearer(request)
    try:
        claims = await verifier.verify(token)
    except AuthError as exc:
        log.info("auth_rejected", reason=exc.code)
        raise
    user = user_from_claims(claims)
    request.state.user = user
    return user


async def get_account_user(request: Request, user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
    directory: AccountDirectory = request.app.state.accounts
    try:
        account = await directory.resolve(user)
    except AuthError as exc:
        log.info("auth_rejected", reason=exc.code)
        raise
    request.state.user = account
    return account


async def require_admin(user: Annotated[CurrentUser, Depends(get_account_user)]) -> CurrentUser:
    """Server-side admin gate. The client only hides admin UI; this is the enforcement."""
    if not user.is_admin:
        log.info("admin_denied")
        raise Forbidden("This needs an admin account.")
    return user


AuthUser = Annotated[CurrentUser, Depends(get_current_user)]
AccountUser = Annotated[CurrentUser, Depends(get_account_user)]
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


def install_auth(
    app: Any,
    settings: Settings,
    verifier: TokenVerifier | None = None,
    accounts: AccountDirectory | None = None,
) -> None:
    app.state.auth = verifier or build_verifier(settings)
    app.state.admin_emails = parse_admin_emails(settings.admin_emails)
    app.state.accounts = accounts or AccountDirectory(app.state.db, app.state.admin_emails)
    app.add_exception_handler(AuthError, _auth_error_handler)
