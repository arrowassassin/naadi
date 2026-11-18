"""Test-only: age a throwaway e2e user's plan so days have been missed (local DB only).

Used by apps/web/e2e/planner.spec.ts: the plan the real onboarding made starts today; `shift` moves every plan day and
item back by N calendar days, as if the student had not opened Naadi for N days.

    uv run python -m tests.plan.e2e_planner shift --email naadi-e2e-<role>-<run>@example.com --days 3 --db URL

Only naadi-e2e-*@example.com users and loopback databases are accepted.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from typing import Any
from urllib.parse import urlparse

import psycopg

TEST_EMAIL = re.compile(r"^naadi-e2e-[a-z]+-[a-z0-9-]{4,40}@example\.com$")
LOCAL = {"localhost", "127.0.0.1", "::1"}
APART = 1000  # (plan_id, date) is unique: shift far away first so rows never collide in the middle of the update


def _check(email: str, db: str) -> str:
    email = email.strip().lower()
    if not TEST_EMAIL.match(email):
        raise SystemExit("only naadi-e2e-<role>-<run>@example.com test users are allowed")
    if (urlparse(db).hostname or "") not in LOCAL:
        raise SystemExit("only a loopback database is accepted")
    return email


def shift(email: str, db: str, days: int) -> dict[str, Any]:
    with psycopg.connect(db, autocommit=True) as pg:
        row = pg.execute("select id from auth.users where lower(email) = %s", (email,)).fetchone()
        if row is None:
            raise SystemExit("no such user")
        uid = uuid.UUID(str(row[0]))
        for delta in (-APART, APART - days):
            pg.execute("update public.study_plan_days set date = date + %s where user_id = %s", (delta, uid))
            pg.execute("update public.plan_items set day = day + %s where user_id = %s", (delta, uid))
        first = pg.execute("select min(date) from public.study_plan_days where user_id = %s", (uid,)).fetchone()
        return {"shifted": days, "first_day": first[0].isoformat() if first else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["shift"])
    ap.add_argument("--email", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--days", type=int, default=3)
    a = ap.parse_args()
    email = _check(a.email, a.db)
    json.dump(shift(email, a.db, a.days), sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
