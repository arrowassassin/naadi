"""Mock exam answer keys never reach stored content (the importer strips them; seed/mock.py)."""

from __future__ import annotations

import psycopg

from naadi_api.seed.data import SeedBundle
from naadi_api.seed.importer import run_seed

from .rls import DB_MARKS
from .seed_helpers import options
from .test_seed import b, first_run, url  # noqa: F401 - the module's seeded-database fixtures

pytestmark = DB_MARKS


def test_mock_exam_answer_keys_are_not_stored_as_content(first_run: object, url: str, b: SeedBundle) -> None:  # noqa: F811
    with psycopg.connect(url) as c:
        rows = c.execute(
            "select s.id, s.body::text, coalesce(s.body_html, '') from public.chapter_sections s"
            " join public.chapters ch on ch.id = s.chapter_id where ch.kind = 'mock_exam'"
        ).fetchall()
        answers = c.execute(
            "select count(*) from public.questions q join public.chapters ch on ch.id = q.chapter_id"
            " where ch.kind = 'mock_exam' and q.answer is not null"
        ).fetchone()
    assert rows and answers is not None and answers[0] > 0  # the answers live on the questions
    for sid, body, html in rows:
        assert '"Answer"' not in body and "<th>Answer</th>" not in html, sid
    again = run_seed(options(url), bundle=b)  # idempotent: a re-import keeps them stripped
    assert again.ok and again.changes == 0
