"""Test-only fixture for the Playwright auth flows (never imported by the app, never shipped).

Creates (or reuses) a Supabase Auth user with the service role and generates a one-time magic-link token
server-side, so e2e can sign in for real without an inbox. Prints one JSON object on stdout:
`{"user_id": "...", "token_hash": "...", "email_verified": bool}`. The token is single-use and expires in an
hour; callers must not log it.

    uv run python -m tests.auth.e2e_users link --email naadi-e2e-student-<run>@example.com [--local-db URL]
    uv run python -m tests.auth.e2e_users delete --email naadi-e2e-student-<run>@example.com [--local-db URL]
    uv run python -m tests.auth.e2e_users link --owner     # live admin check only; never deleted or printed
    uv run python -m tests.auth.e2e_users sweep --prefix naadi-e2e-live-   # leftovers of a crashed run

`--local-db` mirrors the user into a local Postgres `auth.users` (only localhost URLs are accepted), so a
local API whose database is not the Supabase project can still satisfy the profiles -> auth.users FK.
Only `naadi-e2e-*@example.com` addresses are accepted, so this can never touch a real person's account.
The one exception is `--owner` (link only): the first `ADMIN_EMAILS` address, used by the live smoke to
assert that the owner is an admin. That address is never printed, and that account is never deleted.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any
from urllib.parse import urlparse

import psycopg
from supabase import Client, create_client
from supabase_auth.errors import AuthApiError

TEST_EMAIL = re.compile(r"^naadi-e2e-[a-z]+-[a-z0-9-]{4,40}@example\.com$")


def _client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
    if urlparse(url).hostname not in {"localhost", "127.0.0.1", "::1"} and os.environ.get("E2E_ALLOW_HOSTED") != "1":
        raise SystemExit("SUPABASE_URL is not the local auth stack; set E2E_ALLOW_HOSTED=1 to use a hosted project")
    return create_client(url, key)


def _check_email(email: str) -> str:
    email = email.strip().lower()
    if not TEST_EMAIL.match(email):
        raise SystemExit("only naadi-e2e-<role>-<run>@example.com test addresses are allowed")
    return email


def _owner_email() -> str:
    """The first ADMIN_EMAILS entry, as written (same separators as naadi_api.auth.parse_admin_emails)."""
    raw = os.environ.get("ADMIN_EMAILS", "")
    for part in raw.replace(";", ",").replace("\n", ",").replace(" ", ",").split(","):
        if "@" in part.strip():
            return part.strip().lower()
    raise SystemExit("ADMIN_EMAILS is not set")


def _check_local(url: str) -> str:
    host = urlparse(url).hostname
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit("--local-db must point at localhost")
    return url


def _find_user(sb: Client, email: str) -> Any | None:
    page = 1
    while True:
        users = sb.auth.admin.list_users(page=page, per_page=200)
        for u in users:
            if (u.email or "").lower() == email:
                return u
        if len(users) < 200:
            return None
        page += 1


def link(email: str, local_db: str | None, *, create: bool = True) -> dict[str, Any]:
    sb = _client()
    user = _find_user(sb, email)
    if user is None:
        if not create:
            raise SystemExit("that account does not exist")
        try:
            user = sb.auth.admin.create_user({"email": email, "email_confirm": True}).user
        except AuthApiError:
            # Another worker created it a moment ago (parallel projects share the run's users).
            user = _find_user(sb, email)
            if user is None:
                raise
    res = sb.auth.admin.generate_link({"type": "magiclink", "email": email})
    props = res.properties
    if local_db:
        with psycopg.connect(_check_local(local_db), autocommit=True) as conn:
            conn.execute(
                "insert into auth.users (id, email, aud, role) "
                "values (%s, %s, 'authenticated', 'authenticated') on conflict (id) do nothing",
                (user.id, email),
            )
    return {"user_id": str(user.id), "token_hash": props.hashed_token, "type": props.verification_type}


def delete(email: str, local_db: str | None) -> dict[str, Any]:
    sb = _client()
    user = _find_user(sb, email)
    if user is not None:
        sb.auth.admin.delete_user(user.id)
        if local_db:
            with psycopg.connect(_check_local(local_db), autocommit=True) as conn:
                conn.execute("delete from auth.users where id = %s", (user.id,))
    return {"deleted": user is not None}


def sweep(prefix: str) -> dict[str, Any]:
    """Delete leftover throwaway users whose address starts with `prefix` (a crashed earlier run)."""
    if not re.fullmatch(r"naadi-e2e-[a-z]+-", prefix):
        raise SystemExit("--prefix must look like naadi-e2e-<role>-")
    sb = _client()
    stale: list[Any] = []
    page = 1
    while True:
        users = sb.auth.admin.list_users(page=page, per_page=200)
        stale += [u for u in users if (u.email or "").startswith(prefix) and TEST_EMAIL.match(u.email or "")]
        if len(users) < 200:
            break
        page += 1
    for u in stale:
        sb.auth.admin.delete_user(u.id)
    return {"deleted": len(stale)}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["link", "delete", "sweep"])
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--prefix")
    who.add_argument("--email")
    who.add_argument("--owner", action="store_true")
    ap.add_argument("--local-db")
    a = ap.parse_args(argv)
    if a.prefix:
        if a.action != "sweep":
            raise SystemExit("--prefix is only for `sweep`")
        out = sweep(a.prefix)
    elif a.owner:
        if a.action != "link" or a.local_db:
            raise SystemExit("--owner is only for `link` against the live project")
        out = link(_owner_email(), None, create=False)
    else:
        email = _check_email(a.email)
        out = link(email, a.local_db) if a.action == "link" else delete(email, a.local_db)
    sys.stdout.write(json.dumps(out) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
