"""Test-only helper for the Playwright generation spec (never imported by the app, never shipped).

Creates / removes one shared chapter with two sections for the approval flow, so the spec never edits seeded
chapters. Local databases only.

    uv run python -m tests.generation.e2e_world make --tag abc123
    uv run python -m tests.generation.e2e_world drop --tag abc123
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

import psycopg
from psycopg.types.json import Jsonb

from tests.conftest import is_local_url

DOC = {
    "type": "doc",
    "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Generated figure target"}]}],
}


def _conn() -> psycopg.Connection[tuple[object, ...]]:
    url = os.environ.get("DATABASE_URL", "")
    if not is_local_url(url):
        raise SystemExit("e2e_world writes rows: DATABASE_URL must be a local test database")
    return psycopg.connect(url, autocommit=True)


def make(tag: str) -> dict[str, object]:
    chapter, src = f"gen-e2e-{tag}", f"gen-e2e-src-{tag}"
    with _conn() as c:
        c.execute(
            "insert into public.sources (id, name, license, allow_ai_ingest) values"
            " ('naadi-ai-generated', 'Naadi AI-generated illustrations', 'proprietary-naadi', false)"
            " on conflict (id) do nothing"
        )
        c.execute(
            "insert into public.sources (id, name, license) values (%s, 'Naadi original', 'proprietary-naadi')", (src,)
        )
        c.execute(
            "insert into public.chapters (id, exam, kind, title, sort, source_id, status)"
            " values (%s, 'step1', 'chapter', %s, 990, %s, 'published')",
            (chapter, f"Generation e2e {tag}", src),
        )
        for i in (1, 2):
            c.execute(
                "insert into public.chapter_sections (id, chapter_id, heading, sort, body, body_html)"
                " values (%s, %s, %s, %s, %s, '<p>Generated figure target</p>')",
                (f"{chapter}-s{i}", chapter, f"Target section {i}", i, Jsonb(DOC)),
            )
    return {"chapter": chapter, "section": f"{chapter}-s1", "title": f"Generation e2e {tag}"}


def drop(tag: str) -> dict[str, object]:
    chapter, src = f"gen-e2e-{tag}", f"gen-e2e-src-{tag}"
    with _conn() as c:
        c.execute(
            "delete from public.media_assets where id in"
            " (select media_id from public.section_media where section_id like %s)",
            (f"{chapter}-s%",),
        )
        c.execute("delete from public.chapters where id = %s", (chapter,))
        c.execute("delete from public.sources where id = %s", (src,))
    return {"dropped": chapter}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make", "drop"])
    ap.add_argument("--tag", required=True)
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[a-z0-9]{4,24}", a.tag):
        raise SystemExit("tag must be 4-24 lowercase letters or digits")
    print(json.dumps(make(a.tag) if a.cmd == "make" else drop(a.tag)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
