"""Aegra custom auth: the Supabase access token, verified exactly as for `/api/*` (naadi_api.auth).

- `identity` = the Supabase user id (`sub`); Aegra scopes every assistant, thread, run, cron and store item
  to it, so user B gets 404 for user A's objects.
- `permissions` = ["admin"] for admin accounts: a confirmed email (`auth.users.email_confirmed_at`) listed in
  ADMIN_EMAILS, and the token's session still live in `auth.sessions` (same `AccountDirectory` as `/api/*`).
  No email or name is placed in the identity (it is copied into run configs and logs).
- Crons and assistant writes are admin-only: a student never schedules model calls or reconfigures a graph.
  System crons are owned by `SYSTEM_IDENTITY`, which no token can produce.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any

from langgraph_sdk import Auth

from ..auth import (
    AccountDirectory,
    AuthError,
    AuthUnavailable,
    TokenVerifier,
    build_verifier,
    parse_admin_emails,
    user_from_claims,
)
from ..db import Database
from ..logs import get_logger
from ..settings import get_settings
from .identity import ADMIN_PERMISSION

log = get_logger("naadi.agent.auth")

auth = Auth()

# Set per request by host.AuthUnavailableAs503; True when the account could not be checked (-> 503).
unavailable_flag: ContextVar[dict[str, bool] | None] = ContextVar("naadi_agent_auth_unavailable", default=None)
UNAVAILABLE = json.dumps(
    {
        "error": "service_unavailable",
        "message": "Your account cannot be checked right now. Try again in a moment.",
        "details": {"retry_after_s": 5},
    }
).encode()


def mark_unavailable() -> None:
    flag = unavailable_flag.get()
    if flag is not None:  # mutated, not re-set: the middleware holds the same dict across task boundaries
        flag["unavailable"] = True


_verifier: TokenVerifier | None = None
_accounts: AccountDirectory | None = None


def configure(verifier: TokenVerifier | None = None, accounts: AccountDirectory | None = None) -> None:
    """Set the verifier and account directory (tests inject a local JWKS / HS256 secret and database)."""
    global _verifier, _accounts
    _verifier = verifier
    _accounts = accounts


def _get() -> tuple[TokenVerifier, AccountDirectory]:
    global _verifier, _accounts
    if _verifier is None or _accounts is None:
        s = get_settings()
        _verifier = _verifier or build_verifier(s)
        _accounts = _accounts or AccountDirectory(Database(s.database_url), parse_admin_emails(s.admin_emails))
    return _verifier, _accounts


class AuthUnavailableAs503:
    """Aegra turns every failure of the auth handler into 401, which tells the client to sign in again. When
    the account could not be CHECKED (database briefly unreachable), the right answer is 503 + Retry-After:
    the session is fine. The handler marks that case (agent.auth.mark_unavailable) and this ASGI middleware
    rewrites that one response."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        flag = {"unavailable": False}
        token = unavailable_flag.set(flag)
        rewriting = False

        async def wrapped(message: Any) -> None:
            nonlocal rewriting
            if message["type"] == "http.response.start" and message.get("status") == 401 and flag["unavailable"]:
                rewriting = True
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() not in (b"content-length",)]
                body = UNAVAILABLE
                headers += [(b"retry-after", b"5"), (b"content-length", str(len(body)).encode())]
                await send({**message, "status": 503, "headers": headers})
                return
            if rewriting and message["type"] == "http.response.body":
                if not message.get("more_body"):
                    await send({"type": "http.response.body", "body": UNAVAILABLE, "more_body": False})
                return
            await send(message)

        try:
            await self.app(scope, receive, wrapped)
        finally:
            unavailable_flag.reset(token)


@auth.authenticate
async def authenticate(headers: dict[str, Any]) -> Auth.types.MinimalUserDict:
    raw = headers.get("authorization") or headers.get("Authorization") or ""
    if isinstance(raw, bytes):
        raw = raw.decode("latin-1")
    scheme, _, token = str(raw).partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Auth.exceptions.HTTPException(status_code=401, detail="Sign in to continue.")
    verifier, accounts = _get()
    try:
        claims = await verifier.verify(token.strip())
        user = await accounts.resolve(user_from_claims(claims))
    except AuthUnavailable as exc:
        log.warning("agent_auth_unavailable", reason=exc.code)
        mark_unavailable()
        raise Auth.exceptions.HTTPException(status_code=503, detail=exc.message) from exc
    except AuthError as exc:
        log.info("agent_auth_rejected", reason=exc.code)
        raise Auth.exceptions.HTTPException(status_code=exc.status, detail=exc.message) from exc
    return {
        "identity": str(user.user_id),
        "display_name": "student",
        "is_authenticated": True,
        "permissions": [ADMIN_PERMISSION] if user.is_admin else [],
    }


def _is_admin(ctx: Auth.types.AuthContext) -> bool:
    return ADMIN_PERMISSION in (ctx.permissions or [])


@auth.on.crons
async def crons_admin_only(ctx: Auth.types.AuthContext, value: Any) -> bool:
    if ctx.action in ("read", "search") or _is_admin(ctx):
        return True
    log.info("agent_auth_denied", resource="crons", action=ctx.action)
    return False


@auth.on.assistants
async def assistant_writes_admin_only(ctx: Auth.types.AuthContext, value: Any) -> bool:
    if ctx.action in ("read", "search") or _is_admin(ctx):
        return True
    log.info("agent_auth_denied", resource="assistants", action=ctx.action)
    return False
