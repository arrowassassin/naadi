"""Test-only: a private chapter with every rich content node, owned by a throwaway e2e user (local DB only).

Used by apps/web/e2e/learn.spec.ts to render math, Mermaid, video, PDF, question embeds, highlights and an
unknown node through the real API and the production build (its CSP), since the seed has none of them.

    uv run python -m tests.courses.e2e_rich create --email naadi-e2e-<role>-<run>@example.com --db URL
    uv run python -m tests.courses.e2e_rich delete --email ... --db URL

Prints one JSON line: {"chapter_id": ..., "section_id": ...}. Only naadi-e2e-*@example.com users and loopback
databases are accepted; the rows are removed with the user (on delete cascade) or by `delete`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from typing import Any
from urllib.parse import urlparse

import psycopg
from psycopg.types.json import Jsonb

TEST_EMAIL = re.compile(r"^naadi-e2e-[a-z]+-[a-z0-9-]{4,40}@example\.com$")
LOCAL = {"localhost", "127.0.0.1", "::1"}
MERMAID = "flowchart TD\n  A[Metabolic acidosis] --> B{Gap > 12?}\n  B -->|yes| C[High gap]\n  B -->|no| D[Normal gap]"


def _t(text: str, *marks: dict[str, Any]) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text", "text": text}
    if marks:
        node["marks"] = list(marks)
    return node


def _p(*inline: dict[str, Any]) -> dict[str, Any]:
    return {"type": "paragraph", "content": list(inline)}


def _hl(meaning: str) -> dict[str, Any]:
    color = {"key_fact": "lime", "formula": "violet", "mistake": "amber"}[meaning]
    return {"type": "highlight", "attrs": {"color": color, "meaning": meaning}}


def body(prefix: str, question_id: str) -> dict[str, Any]:
    return {
        "type": "doc",
        "content": [
            _p(
                _t("Highlights: "),
                _t("key fact", _hl("key_fact")),
                _t(", "),
                _t("formula", _hl("formula")),
                _t(", "),
                _t("mistake", _hl("mistake")),
            ),
            {"type": "math", "attrs": {"latex": "\\text{AG} = Na^+ - (Cl^- + HCO_3^-)"}},
            {"type": "mermaid", "attrs": {"code": MERMAID}},
            {
                "type": "callout",
                "attrs": {"variant": "trap"},
                "content": [_p(_t("A normal gap does not rule out a mixed disorder."))],
            },
            {"type": "video", "attrs": {"mediaId": f"{prefix}-vid", "startS": 30, "endS": None}},
            {"type": "pdf", "attrs": {"mediaId": f"{prefix}-pdf", "page": 2}},
            {"type": "questionEmbed", "attrs": {"questionId": question_id}},
            {"type": "sparkle", "content": [_p(_t("Text inside a node type from the future."))]},
            _p(_t("The end of the rich section.")),
        ],
    }


def _check(email: str, db: str) -> str:
    email = email.strip().lower()
    if not TEST_EMAIL.match(email):
        raise SystemExit("only naadi-e2e-<role>-<run>@example.com test users are allowed")
    if (urlparse(db).hostname or "") not in LOCAL:
        raise SystemExit("refusing a non-local database")
    return email


def _prefix(email: str) -> str:
    return "e2erich-" + hashlib.sha256(email.encode()).hexdigest()[:10]


def create(email: str, db: str) -> dict[str, str]:
    prefix = _prefix(email)
    with psycopg.connect(db, autocommit=True) as c:
        row = c.execute("select id from auth.users where lower(email) = %s", (email,)).fetchone()
        if row is None:
            raise SystemExit("no such user (sign the test user in first)")
        q = c.execute("select id from public.questions where chapter_id = 'ch01' order by id limit 1").fetchone()
        if q is None:
            raise SystemExit("the seed is missing (ch01 questions)")
        delete(email, db)
        c.execute(
            "insert into public.media_assets (id, kind, source_id, title, youtube_id, embeddable, duration_s,"
            " review_status) values (%s, 'video_youtube', 'youtube', 'Anion gap, step by step', 'M7lc1UVf-VE', true,"
            " 754, 'approved')",
            (f"{prefix}-vid",),
        )
        c.execute(
            "insert into public.media_assets (id, kind, source_id, title, url, review_status)"
            " values (%s, 'pdf', 'naadi-original-v1', 'Lab values', 'content-public/figures/ch01-fig01.svg',"
            " 'approved')",
            (f"{prefix}-pdf",),
        )
        c.execute(
            "insert into public.chapters (id, exam, kind, title, status, owner_user_id)"
            " values (%s, 'step1', 'user_upload', 'Rich content check', 'published', %s)",
            (prefix, row[0]),
        )
        c.execute(
            "insert into public.chapter_sections (id, chapter_id, sort, heading, body, reading_minutes)"
            " values (%s, %s, 1, 'Every rich node', %s, 2)",
            (f"{prefix}-s01", prefix, Jsonb(body(prefix, q[0]))),
        )
    return {"chapter_id": prefix, "section_id": f"{prefix}-s01"}


def delete(email: str, db: str) -> dict[str, str]:
    prefix = _prefix(email)
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("delete from public.chapters where id = %s", (prefix,))
        c.execute("delete from public.media_assets where id = any(%s)", ([f"{prefix}-vid", f"{prefix}-pdf"],))
    return {"chapter_id": prefix}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["create", "delete"])
    ap.add_argument("--email", required=True)
    ap.add_argument("--db", required=True)
    a = ap.parse_args(argv)
    email = _check(a.email, a.db)
    out = create(email, a.db) if a.action == "create" else delete(email, a.db)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
