"""Test-only fixture for the Playwright auth flows (never imported by the app, never shipped).

Creates (or reuses) a Supabase Auth user with the service role and generates a one-time magic-link token
server-side, so e2e can sign in for real without an inbox. Prints one JSON object on stdout:
`{"user_id": "...", "token_hash": "...", "email_verified": bool}`. The token is single-use and expires in an
hour; callers must not log it.

    uv run python -m tests.auth.e2e_users link --email naadi-e2e-student@example.com [--local-db URL]
    uv run python -m tests.auth.e2e_users delete --email naadi-e2e-student@example.com [--local-db URL]

`--local-db` mirrors the user into a local Postgres `auth.users` (only localhost URLs are accepted), so a
local API whose database is not the Supabase project can still satisfy the profiles -> auth.users FK.
Only addresses at example.com are accepted, so this can never touch a real person's account.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.parse import urlparse

import psycopg
from supabase import Client, create_client

ALLOWED_DOMAIN = "example.com"


def _client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
    return create_client(url, key)


def _check_email(email: str) -> str:
    email = email.strip().lower()
    if not email.endswith(f"@{ALLOWED_DOMAIN}") or email.count("@") != 1:
        raise SystemExit(f"only test addresses at {ALLOWED_DOMAIN} are allowed")
    return email


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


def link(email: str, local_db: str | None) -> dict[str, Any]:
    sb = _client()
    user = _find_user(sb, email)
    if user is None:
        user = sb.auth.admin.create_user({"email": email, "email_confirm": True}).user
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


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["link", "delete"])
    ap.add_argument("--email", required=True)
    ap.add_argument("--local-db")
    a = ap.parse_args(argv)
    email = _check_email(a.email)
    out = link(email, a.local_db) if a.action == "link" else delete(email, a.local_db)
    sys.stdout.write(json.dumps(out) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
