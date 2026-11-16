"""Test-only: practice history and a coach suggestion for a throwaway e2e user (local DB only).

Used by apps/web/e2e/today.spec.ts so Today shows real numbers from the real API: graded attempts on seeded
Step 1 questions (one weak system, one strong, one with too few attempts) and one rules-worded coach
suggestion whose facts come from the same rows.

    uv run python -m tests.plan.e2e_today history --email naadi-e2e-<role>-<run>@example.com --db URL
    uv run python -m tests.plan.e2e_today coach --email ... --db URL
    uv run python -m tests.plan.e2e_today delete --email ... --db URL

`history` prints one JSON line with the expected per-system numbers, computed by direct SQL after the insert.
Only naadi-e2e-*@example.com users and loopback databases are accepted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import uuid
from typing import Any
from urllib.parse import urlparse

import psycopg
from psycopg.types.json import Jsonb

TEST_EMAIL = re.compile(r"^naadi-e2e-[a-z]+-[a-z0-9-]{4,40}@example\.com$")
LOCAL = {"localhost", "127.0.0.1", "::1"}
# (system, questions, right): weak (<55 %), strong, and under the 5-attempt threshold.
PLAN = (
    ("step1.sys.respiratory_renal", 10, 4),
    ("step1.sys.cardiovascular", 6, 6),
    ("step1.sys.gastrointestinal", 2, 1),
)
TAG = "e2e-today"


def _check(email: str, db: str) -> str:
    email = email.strip().lower()
    if not TEST_EMAIL.match(email):
        raise SystemExit("only naadi-e2e-<role>-<run>@example.com test users are allowed")
    if (urlparse(db).hostname or "") not in LOCAL:
        raise SystemExit("only a loopback database is accepted")
    return email


def _user(pg: psycopg.Connection[Any], email: str) -> uuid.UUID:
    row = pg.execute("select id from auth.users where lower(email) = %s", (email,)).fetchone()
    if row is None:
        raise SystemExit("no such user")
    return uuid.UUID(str(row[0]))


def history(email: str, db: str) -> dict[str, Any]:
    with psycopg.connect(db, autocommit=True) as pg:
        uid = _user(pg, email)
        used: set[str] = set()
        picks: list[tuple[str, bool]] = []
        for system, n, right in PLAN:
            rows = pg.execute(
                "select id from public.questions where exam = 'step1' and block is null and status = 'published'"
                " and %s = any(taxonomy_ids) and not (taxonomy_ids && %s) order by id",
                (system, [s for s, _, _ in PLAN if s != system]),
            ).fetchall()
            ids = [r[0] for r in rows if r[0] not in used][:n]
            if len(ids) < n:
                raise SystemExit(f"not enough seeded questions for {system}")
            used.update(ids)
            picks += [(q, i < right) for i, q in enumerate(ids)]
        block = uuid.uuid4()
        pg.execute(
            "insert into public.blocks (id, user_id, config, question_ids, started_at, finished_at)"
            " values (%s, %s, %s, %s, now(), now())",
            (block, uid, Jsonb({"exam": "step1", "mode": "tutor", "tag": TAG}), [q for q, _ in picks]),
        )
        at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)
        for q, ok in picks:
            pg.execute(
                "insert into public.attempts (user_id, question_id, block_id, chosen, correct, seconds, mode,"
                " created_at) select %s, id, %s,"
                " case when %s then answer else (case when answer = 'A' then 'B' else 'A' end) end,"
                " %s, 40, 'tutor', %s from public.questions where id = %s",
                (uid, block, ok, ok, at, q),
            )
        out: dict[str, Any] = {}
        for system, _, _ in PLAN:
            n, c = pg.execute(
                "select count(*), count(*) filter (where a.correct) from public.attempts a"
                " join public.questions q on q.id = a.question_id where a.user_id = %s and a.correct is not null"
                " and %s = any(q.taxonomy_ids) and q.block is null and a.created_at >= now() - interval '14 days'",
                (uid, system),
            ).fetchone() or (0, 0)
            out[system] = {"attempts": n, "correct": c, "percent": round(100 * c / n) if n >= 5 else None}
        return {"systems": out, "answered": len(picks)}


def coach(email: str, db: str) -> dict[str, Any]:
    with psycopg.connect(db, autocommit=True) as pg:
        uid = _user(pg, email)
        tz = pg.execute("select timezone from public.profiles where user_id = %s", (uid,)).fetchone()
        from zoneinfo import ZoneInfo

        today = dt.datetime.now(dt.UTC).astimezone(ZoneInfo(tz[0] if tz else "UTC")).date()
        system, n, right = PLAN[0]
        label = pg.execute("select label from public.taxonomy_nodes where id = %s", (system,)).fetchone()
        facts = {
            "system": label[0] if label else system,
            "percent": round(100 * right / n),
            "attempts": n,
            "correct": right,
            "questions": 20,
        }
        action = {
            "add_items": [
                {
                    "kind": "questions",
                    "title": facts["system"],
                    "ref_type": "weak_spot",
                    "ref_id": "step1",
                    "target_count": 20,
                    "target_minutes": 60,
                }
            ]
        }
        sid = uuid.uuid4()
        pg.execute(
            "insert into public.coach_suggestions (id, user_id, for_date, kind, facts, action, source)"
            " values (%s, %s, %s, 'catch_up_block', %s, %s, 'rules')",
            (sid, uid, today, Jsonb(facts), Jsonb(action)),
        )
        return {"suggestion_id": str(sid), "facts": facts}


def delete(email: str, db: str) -> dict[str, Any]:
    with psycopg.connect(db, autocommit=True) as pg:
        row = pg.execute("select id from auth.users where lower(email) = %s", (email,)).fetchone()
        if row is None:
            return {"deleted": 0}
        uid = row[0]
        pg.execute("delete from public.coach_suggestions where user_id = %s", (uid,))
        pg.execute("delete from public.attempts where user_id = %s", (uid,))
        cur = pg.execute("delete from public.blocks where user_id = %s and config->>'tag' = %s", (uid, TAG))
        return {"deleted": cur.rowcount}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["history", "coach", "delete"])
    ap.add_argument("--email", required=True)
    ap.add_argument("--db", required=True)
    a = ap.parse_args(argv)
    email = _check(a.email, a.db)
    out = {"history": history, "coach": coach, "delete": delete}[a.action](email, a.db)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
