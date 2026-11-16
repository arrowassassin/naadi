"""Aegra custom auth: the Supabase access token, verified exactly as for `/api/*` (naadi_api.auth).

- `identity` = the Supabase user id (`sub`); Aegra scopes every assistant, thread, run, cron and store item
  to it, so user B gets 404 for user A's objects.
- `permissions` = ["admin"] for verified admin emails. No email or name is placed in the identity (it is
  copied into run configs and logs).
- Crons and assistant writes are admin-only: a student never schedules model calls or reconfigures a graph.
  System crons are owned by `SYSTEM_IDENTITY`, which no token can produce.
"""

from __future__ import annotations

from typing import Any

from langgraph_sdk import Auth

from ..auth import AuthError, TokenVerifier, build_verifier, parse_admin_emails, user_from_claims
from ..logs import get_logger
from ..settings import get_settings
from .identity import ADMIN_PERMISSION

log = get_logger("naadi.agent.auth")

auth = Auth()

_verifier: TokenVerifier | None = None
_admins: frozenset[str] | None = None


def configure(verifier: TokenVerifier | None = None, admin_emails: frozenset[str] | None = None) -> None:
    """Set the verifier (tests inject one with a local JWKS / HS256 secret)."""
    global _verifier, _admins
    _verifier = verifier
    _admins = admin_emails


def _get() -> tuple[TokenVerifier, frozenset[str]]:
    global _verifier, _admins
    if _verifier is None:
        s = get_settings()
        _verifier = build_verifier(s)
        _admins = parse_admin_emails(s.admin_emails)
    return _verifier, _admins or frozenset()


@auth.authenticate
async def authenticate(headers: dict[str, Any]) -> Auth.types.MinimalUserDict:
    raw = headers.get("authorization") or headers.get("Authorization") or ""
    if isinstance(raw, bytes):
        raw = raw.decode("latin-1")
    scheme, _, token = str(raw).partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Auth.exceptions.HTTPException(status_code=401, detail="Sign in to continue.")
    verifier, admins = _get()
    try:
        claims = await verifier.verify(token.strip())
        user = user_from_claims(claims, admins)
    except AuthError as exc:
        log.info("agent_auth_rejected", reason=exc.code)
        raise Auth.exceptions.HTTPException(status_code=401, detail=exc.message) from exc
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
