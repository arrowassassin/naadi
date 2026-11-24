"""Test-only fixture for the Playwright pathway flows (never imported by the app, never shipped).

Creates a throwaway stage with tasks in every verification state, so a test can look at "Re-verify" on tasks it
controls without touching the seeded pathway (several viewports run the same spec at once), and removes it again.

    uv run python -m tests.pathway.e2e_pathway make  --tag <slug> --db <local database url>
    uv run python -m tests.pathway.e2e_pathway drop  --tag <slug> --db <local database url>
    uv run python -m tests.pathway.e2e_pathway state --task <id> --db <local database url>

`make` prints `{"stage": ..., "fresh": ..., "d90": ..., "d91": ..., "flagged": ...}`. Only localhost databases are
accepted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import psycopg
from psycopg.types.json import Jsonb

from naadi_api.cards.stats import DEFAULT_ZONE

TAG = re.compile(r"^[a-z0-9]{3,24}$")


def _connect(url: str) -> psycopg.Connection[tuple[object, ...]]:
    if urlparse(url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit("only a local database is accepted")
    return psycopg.connect(url, autocommit=True)


def _tag(tag: str) -> str:
    if not TAG.match(tag):
        raise SystemExit("tag must be 3-24 lowercase letters or digits")
    return tag


def make(url: str, tag: str) -> dict[str, str]:
    tag = _tag(tag)
    # The test's new student sees staleness counted from today in their profile's zone (the profiles default):
    # in UTC it can still be yesterday, which would make the 90-day task 91 days old on screen.
    today = dt.datetime.now(ZoneInfo(DEFAULT_ZONE)).date()
    stage = f"e2e{tag}"
    ids = {k: f"e2e{tag}-{k}" for k in ("fresh", "d90", "d91", "flagged")}
    with _connect(url) as c:
        c.execute("delete from public.pathway_stages where key = %s", (stage,))
        c.execute(
            "insert into public.pathway_stages (key, sort, title, summary_md, official_links, last_verified)"
            " values (%s, 9000, %s, %s, %s, %s)",
            (stage, f"E2E stage {tag}", "A throwaway stage for tests.", Jsonb([]), today),
        )
        rows = (
            ("fresh", 1, today, False, 0, "https://www.ecfmg.org/e2e-fresh"),
            ("d90", 2, today - dt.timedelta(days=90), False, 0, "https://www.usmle.org/e2e-90"),
            ("d91", 3, today - dt.timedelta(days=91), False, 50, "https://www.nrmp.org/e2e-91"),
            ("flagged", 4, today, True, 0, "https://www.ecfmg.org/e2e-flagged"),
        )
        for name, sort, verified, flagged, cost, official in rows:
            c.execute(
                "insert into public.pathway_tasks (id, stage_key, sort, title, detail_md, due_rule, cost_usd,"
                " official_url, last_verified, needs_review) values (%s, %s, %s, %s, %s, 'start+7d', %s, %s, %s, %s)",
                (
                    ids[name],
                    stage,
                    sort,
                    f"E2E task {name} {tag}",
                    f"Detail for {name} [ECFMG](https://ecfmg.org).",
                    cost,
                    official,
                    verified,
                    flagged,
                ),
            )
        c.execute(
            "insert into public.pathway_task_checks (task_id, url, http_status, changed, finding) values"
            " (%s, 'https://www.ecfmg.org/e2e-flagged', 200, true, %s)",
            (
                ids["flagged"],
                Jsonb(
                    {
                        "status": "changed",
                        "summary_key": "page_changed_differences",
                        "still_accurate": False,
                        "differences": ["The fee is now $950."],
                        "quotes": [{"text": "The fee is $950.", "url": "https://www.ecfmg.org/e2e-flagged"}],
                    }
                ),
            ),
        )
    return {"stage": stage, **ids}


def drop(url: str, tag: str) -> dict[str, bool]:
    with _connect(url) as c:
        c.execute("delete from public.pathway_stages where key = %s", (f"e2e{_tag(tag)}",))
    return {"ok": True}


def state(url: str, task: str) -> dict[str, object]:
    with _connect(url) as c:
        row = c.execute(
            "select last_verified, needs_review,"
            " (select count(*) from public.pathway_task_checks k where k.task_id = t.id and k.resolved_at is null)"
            " from public.pathway_tasks t where id = %s",
            (task,),
        ).fetchone()
    if row is None:
        return {"found": False}
    return {
        "found": True,
        "last_verified": row[0].isoformat() if row[0] else None,
        "needs_review": row[1],
        "open_checks": row[2],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["make", "drop", "state"])
    ap.add_argument("--tag")
    ap.add_argument("--task")
    ap.add_argument("--db", required=True)
    a = ap.parse_args()
    if a.action == "make":
        out: dict[str, object] = dict(make(a.db, a.tag or ""))
    elif a.action == "drop":
        out = dict(drop(a.db, a.tag or ""))
    else:
        out = state(a.db, a.task or "")
    print(json.dumps(out))


if __name__ == "__main__":
    main()
