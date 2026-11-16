"""DB-test helper: a signed-in account as Supabase leaves it after a magic-link sign-in.

The API decides identity from the token but the session and admin status from Supabase's `auth` schema
(`AccountDirectory`): the session must exist in `auth.sessions`, and admin needs a confirmed email
(`auth.users.email_confirmed_at`) listed in ADMIN_EMAILS. `signed_in()` prepares exactly that for an
existing `auth.users` row and returns the Authorization header for a token of that session.

Needs the auth schema migrated by Supabase's auth server (`tests/auth/auth_stack.sh up|ci`).
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import psycopg

from tests.conftest import is_local_url

from .jwt_helpers import SigningKey, claims, mint


def _local_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not is_local_url(url):
        raise RuntimeError("signed_in() writes auth rows: DATABASE_URL must be a local test database")
    return url


def open_session(conn: psycopg.Connection[Any], user_id: uuid.UUID) -> str:
    sid = uuid.uuid4()
    conn.execute(
        "insert into auth.sessions (id, user_id, created_at, updated_at) values (%s, %s, now(), now())",
        (sid, user_id),
    )
    return str(sid)


def signed_in(
    key: SigningKey,
    user_id: uuid.UUID,
    *,
    email: str | None = None,
    confirmed: bool = True,
    header: str = "authorization",
    **claim_overrides: Any,
) -> dict[str, str]:
    """Set the account's email (if given) and confirmation, open a live session, and return
    `{header: "Bearer <token>"}` for it. The token carries the account's email, as Supabase issues it."""
    with psycopg.connect(_local_db_url(), autocommit=True) as conn:
        if email is not None:
            conn.execute("update auth.users set email = %s where id = %s", (email, user_id))
        conn.execute(
            "update auth.users set email_confirmed_at = case when %s then now() else null end where id = %s",
            (confirmed, user_id),
        )
        row = conn.execute("select email from auth.users where id = %s", (user_id,)).fetchone()
        if row is None:
            raise RuntimeError("signed_in(): no auth.users row for that id")
        sid = open_session(conn, user_id)
    claim_overrides.setdefault("email", row[0])
    token = mint(key, claims(user_id, session_id=sid, **claim_overrides))
    return {header: f"Bearer {token}"}
