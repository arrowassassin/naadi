"""Analytics over a student's graded attempts. Each function filters on `user_id` explicitly, so it is correct
both under the caller's RLS (`user_tx`) and with the privileged role; only graded rows count (`correct is not
null`), so nothing about a timed/exam block in progress shows up before it is finished.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import psycopg

NodeKind = Literal["system", "discipline", "competency", "clinical_science", "topic"]
Mode = Literal["tutor", "timed", "exam"]


@dataclass(frozen=True)
class NodeAccuracy:
    node_id: str
    kind: str
    label: str
    attempts: int
    correct: int

    @property
    def percent(self) -> float:
        return round(100.0 * self.correct / self.attempts, 1) if self.attempts else 0.0


@dataclass(frozen=True)
class TimePerQuestion:
    answered: int
    mean_s: float | None
    median_s: float | None


@dataclass(frozen=True)
class TrendRow:
    block_id: uuid.UUID
    finished_at: dt.datetime
    score: float
    mode: Mode


async def _rows(conn: psycopg.AsyncConnection[Any], query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    cur = await conn.execute(query, params)
    cols = [c.name for c in cur.description or ()]
    return [r if isinstance(r, dict) else dict(zip(cols, r, strict=True)) for r in await cur.fetchall()]


async def accuracy_by_node(
    conn: psycopg.AsyncConnection[Any],
    user_id: uuid.UUID,
    *,
    exam: str,
    kinds: Sequence[NodeKind] = ("system", "discipline", "competency", "clinical_science"),
    since: dt.datetime | None = None,
    modes: Sequence[Mode] = ("tutor", "timed", "exam"),
) -> list[NodeAccuracy]:
    """Accuracy per taxonomy node of `exam` over the student's graded, answered attempts (omitted not counted)."""
    rows = await _rows(
        conn,
        """
        select n.id as node_id, n.kind, n.label, count(*)::int as attempts,
               count(*) filter (where a.correct)::int as correct
        from public.attempts a
        join public.questions q on q.id = a.question_id
        join public.taxonomy_nodes n on n.id = any(q.taxonomy_ids)
        where a.user_id = %s and a.correct is not null and a.chosen is not null and q.exam = %s
          and n.kind = any(%s) and a.mode = any(%s) and (%s::timestamptz is null or a.created_at >= %s)
        group by n.id, n.kind, n.label, n.sort
        order by n.kind, n.sort, n.label
        """,
        (user_id, exam, list(kinds), list(modes), since, since),
    )
    return [NodeAccuracy(r["node_id"], r["kind"], r["label"], r["attempts"], r["correct"]) for r in rows]


async def time_per_question(
    conn: psycopg.AsyncConnection[Any],
    user_id: uuid.UUID,
    *,
    exam: str | None = None,
    since: dt.datetime | None = None,
    modes: Sequence[Mode] = ("timed", "exam"),
) -> TimePerQuestion:
    rows = await _rows(
        conn,
        """
        select count(*)::int as answered, avg(a.seconds)::float as mean_s,
               (percentile_cont(0.5) within group (order by a.seconds))::float as median_s
        from public.attempts a join public.questions q on q.id = a.question_id
        where a.user_id = %s and a.correct is not null and a.chosen is not null and a.seconds is not null
          and a.mode = any(%s) and (%s::text is null or q.exam = %s)
          and (%s::timestamptz is null or a.created_at >= %s)
        """,
        (user_id, list(modes), exam, exam, since, since),
    )
    r = rows[0]
    mean, median = r["mean_s"], r["median_s"]
    return TimePerQuestion(
        int(r["answered"]),
        round(mean, 1) if mean is not None else None,
        round(median, 1) if median is not None else None,
    )


async def score_trend(
    conn: psycopg.AsyncConnection[Any],
    user_id: uuid.UUID,
    *,
    modes: Sequence[Mode] = ("timed", "exam"),
    exam: str | None = None,
    limit: int = 10,
    until: dt.datetime | None = None,
) -> list[TrendRow]:
    """The last `limit` finished blocks in `modes` (finished at or before `until`), oldest first."""
    rows = await _rows(
        conn,
        """
        select id, finished_at, score::float as score, config->>'mode' as mode from public.blocks
        where user_id = %s and finished_at is not null and score is not null and config->>'mode' = any(%s)
          and (%s::text is null or config->>'exam' = %s) and (%s::timestamptz is null or finished_at <= %s)
        order by finished_at desc, id limit %s
        """,
        (user_id, list(modes), exam, exam, until, until, max(1, min(limit, 100))),
    )
    return [TrendRow(r["id"], r["finished_at"], float(r["score"]), r["mode"]) for r in reversed(rows)]
