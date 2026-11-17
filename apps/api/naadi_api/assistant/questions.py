"""When may the assistant see a question's answer? Only after the student's own graded attempt was revealed to
them: in tutor mode, or once the timed/exam block it belongs to is finished (the S08/S09 rules). Mock-exam
content never (S07 rule). Everything here reads with the service connection and filters on the user."""

from __future__ import annotations

import uuid
from typing import Any

from psycopg.rows import dict_row

from ..db import Database

HIDDEN_FIELDS = ("answer", "answer_html", "explanation_html", "why_not_html", "educational_objective")

_REVEALED = """
select distinct a.question_id from public.attempts a
join public.questions q on q.id = a.question_id and q.status = 'published'
 and (q.owner_user_id is null or q.owner_user_id = a.user_id)
left join public.chapters c on c.id = q.chapter_id
left join public.blocks b on b.id = a.block_id and b.user_id = a.user_id
where a.user_id = %(uid)s and a.question_id = any(%(ids)s)
  and a.chosen is not null and a.correct is not null
  and coalesce(c.kind, '') <> 'mock_exam'
  and (a.mode = 'tutor' or b.config->>'mode' = 'tutor' or b.finished_at is not null
       or (a.block_id is null and a.mode is null))
"""

_LATEST_CHOSEN = """
select a.chosen from public.attempts a
left join public.blocks b on b.id = a.block_id and b.user_id = a.user_id
where a.user_id = %(uid)s and a.question_id = %(q)s and a.chosen is not null and a.correct is not null
  and (a.mode = 'tutor' or b.config->>'mode' = 'tutor' or b.finished_at is not null
       or (a.block_id is null and a.mode is null))
order by a.created_at desc limit 1
"""


async def _conn_rows(db: Database, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    async with db.pool.connection(timeout=5.0) as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return list(await cur.fetchall())


async def revealed_questions(db: Database, user_id: uuid.UUID, question_ids: list[str]) -> set[str]:
    ids = sorted({q for q in question_ids if q})
    if not ids:
        return set()
    rows = await _conn_rows(db, _REVEALED, {"uid": user_id, "ids": ids})
    return {r["question_id"] for r in rows}


async def revealed_choice(db: Database, user_id: uuid.UUID, question_id: str) -> str | None:
    """The student's own chosen option on their latest revealed attempt, or None (no revealed attempt)."""
    rows = await _conn_rows(db, _LATEST_CHOSEN, {"uid": user_id, "q": question_id})
    return rows[0]["chosen"] if rows else None


async def question_for_assistant(db: Database, user_id: uuid.UUID, question_id: str) -> dict[str, Any] | None:
    """The question as the assistant may see it: stem and options always (published, not a mock exam); the answer
    and explanation fields only when the student's attempt was revealed. None when it does not exist."""
    rows = await _conn_rows(
        db,
        "select q.id, q.stem_html, q.options, q.chapter_id, c.kind as chapter_kind,"
        " q.answer, q.answer_html, q.explanation_html, q.why_not_html, q.educational_objective"
        " from public.questions q left join public.chapters c on c.id = q.chapter_id"
        " where q.id = %(q)s and q.status = 'published'"
        # Pool connection: a student's private question is read only for its owner.
        " and (q.owner_user_id is null or q.owner_user_id = %(uid)s)",
        {"q": question_id, "uid": user_id},
    )
    if not rows or rows[0]["chapter_kind"] == "mock_exam":
        return None
    row = rows[0]
    revealed = question_id in await revealed_questions(db, user_id, [question_id])
    out: dict[str, Any] = {
        "id": row["id"],
        "stem_html": row["stem_html"],
        "options": row["options"],
        "chapter_id": row["chapter_id"],
        "revealed": revealed,
    }
    if revealed:
        out["chosen"] = await revealed_choice(db, user_id, question_id)
        for f in HIDDEN_FIELDS:
            out[f] = row[f]
    return out
