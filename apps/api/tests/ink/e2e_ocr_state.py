"""Test-only, read-only: a throwaway e2e student's handwriting-search state (local DB only).

Used by apps/web/e2e/ink-ocr-optin.spec.ts to check the OCR queue, which no client can read (the OCR columns are
backend-only, migration 0017):

    uv run python -m tests.ink.e2e_ocr_state --email naadi-e2e-<role>-<run>@example.com --db URL

Prints one JSON line: {"handwriting_search": <the raw stored value or null>, "layers": [{"surface_type",
"surface_id", "item_count", "ocr_status", "has_text", "ink_rows"}]}. Never prints recognised text. Only
naadi-e2e-*@example.com users and loopback databases are accepted.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any
from urllib.parse import urlparse

import psycopg

TEST_EMAIL = re.compile(r"^naadi-e2e-[a-z]+-[a-z0-9-]{4,40}@example\.com$")
LOCAL = {"localhost", "127.0.0.1", "::1"}


def state(db: str, email: str) -> dict[str, Any]:
    if not TEST_EMAIL.match(email):
        raise SystemExit("only naadi-e2e-*@example.com users")
    if (urlparse(db).hostname or "") not in LOCAL:
        raise SystemExit("only a loopback database")
    with psycopg.connect(db, autocommit=True) as conn:
        user = conn.execute("select id from auth.users where email = %s", (email,)).fetchone()
        if user is None:
            raise SystemExit("no such user")
        uid = user[0]
        flag = conn.execute(
            "select settings -> 'handwriting_search' from public.profiles where user_id = %s", (uid,)
        ).fetchone()
        rows = conn.execute(
            "select l.surface_type, l.surface_id, l.item_count, l.ocr_status, l.recognized_text is not null,"
            " (select count(*) from public.embeddings e where e.owner_user_id = l.user_id and e.ref_type = 'ink'"
            "  and e.ref_id = l.surface_type || ':' || l.surface_id)"
            " from public.ink_layers l where l.user_id = %s order by l.surface_type, l.surface_id",
            (uid,),
        ).fetchall()
    keys = ("surface_type", "surface_id", "item_count", "ocr_status", "has_text", "ink_rows")
    return {
        "handwriting_search": flag[0] if flag else None,
        "layers": [dict(zip(keys, r, strict=True)) for r in rows],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--email", required=True)
    p.add_argument("--db", required=True)
    a = p.parse_args(argv)
    print(json.dumps(state(a.db, a.email)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
