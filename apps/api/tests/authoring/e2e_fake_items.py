"""Test-only: forget the items the scripted fake author wrote, for the review UI e2e (local databases only).

    uv run python -m tests.authoring.e2e_fake_items embeddings --db URL   # before each drafting run
    uv run python -m tests.authoring.e2e_fake_items retire --db URL --ids ai-1,ai-2   # after a test
    uv run python -m tests.authoring.e2e_fake_items seed-ids --db URL                  # unreviewed seed items keyed A

The fake author writes the same template for every topic, so once one such item is embedded the linter's originality
rule (L16, cosine > 0.92 against the bank) drops every further draft. `embeddings` deletes the fake items' vectors
(never the questions); `retire` also retires the named ones, so none stays in a student's pool. Only items whose
recorded author model is the fake's are touched. Prints one JSON line with the counts.
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import urlparse

import psycopg

LOCAL = {"localhost", "127.0.0.1", "::1"}
SEED_IDS = (
    "select id from public.questions where status = 'published' and answer = 'A'"
    " and review->>'humanExpertReviewed' = 'false' and id not like 'ai-%' order by id limit 400"
)
FAKE = "nvidia/fake-author"
FORGET = (
    "delete from public.embeddings where ref_type = 'question' and ref_id in"
    " (select id from public.questions where review->'authorModel'->>'model' = %s)"
)
RETIRE = "update public.questions set status = 'retired' where id = any(%s) and review->'authorModel'->>'model' = %s"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["embeddings", "retire", "seed-ids"])
    p.add_argument("--db", required=True)
    p.add_argument("--ids", default="", help="comma list of question ids (retire)")
    a = p.parse_args(argv)
    if urlparse(a.db).hostname not in LOCAL:
        print("refusing a non-local database", file=sys.stderr)
        return 2
    with psycopg.connect(a.db, autocommit=True) as conn:
        if a.action == "seed-ids":
            cur = conn.execute(SEED_IDS)
            print(json.dumps({"ids": [r[0] for r in cur.fetchall()]}))
            return 0
        out: dict[str, int] = {}
        cur = conn.execute(FORGET, (FAKE,))
        out["embeddings"] = cur.rowcount
        if a.action == "retire":
            ids = [i for i in a.ids.split(",") if i]
            cur = conn.execute(RETIRE, (ids, FAKE))
            out["retired"] = cur.rowcount
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
