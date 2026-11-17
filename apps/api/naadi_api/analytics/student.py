"""Rolling, student-scoped analytics: Today's by-system bars, readiness and the coach's facts.

"Graded attempt" (agreed with segment 09): `attempts.correct is not null` on a non-mock question
(`questions.block is null`). Timed and exam blocks leave `correct` null until the block finishes, then write
omitted questions as wrong, so nothing counts mid-block and an omitted question counts as a miss.

`attempts.correct` is not readable by clients, so the aggregate statements here step out of the
`authenticated` role for one statement (as the Qbank does) and filter on `auth.uid()` themselves; everything
else runs under the caller's RLS.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import psycopg

from ..cards.routes import VISIBLE as CARDS_VISIBLE
from .blueprint import SystemWeight, system_weights, weighted_accuracy

Row = dict[str, Any]
SystemState = Literal["ok", "weak", "insufficient"]


async def _rows(conn: psycopg.AsyncConnection[Any], query: str, params: Mapping[str, Any]) -> list[Row]:
    cur = await conn.execute(query, params)
    return [dict(r) for r in await cur.fetchall()]


async def privileged_rows(conn: psycopg.AsyncConnection[Any], query: str, params: Mapping[str, Any]) -> list[Row]:
    """One statement outside the `authenticated` role (claims kept); `query` must filter on auth.uid()."""
    await conn.execute("reset role")
    try:
        return await _rows(conn, query, params)
    finally:
        await conn.execute("set local role authenticated")


_SYSTEMS = """
select id, kind, label, weight_min, weight_max, sort from public.taxonomy_nodes
where kind = 'system' and %(exam)s = any(exam) order by sort, label
"""


async def load_weights(conn: psycopg.AsyncConnection[Any], exam: str) -> list[SystemWeight]:
    return system_weights(await _rows(conn, _SYSTEMS, {"exam": exam}))


# Per system: graded attempts in the by-system window, and first graded attempts (per question, ever) that fall
# inside the readiness window. One row with system '' carries the answered total.
# questions-scope: rls (the caller's own attempts only: shared questions plus their own private ones)
_CELLS = """
with graded as (
  select a.id, a.question_id, a.correct, a.created_at, q.taxonomy_ids
  from public.attempts a join public.questions q on q.id = a.question_id
  where a.user_id = (select auth.uid()) and a.correct is not null and q.block is null and q.exam = %(exam)s
),
firsts as (
  select distinct on (question_id) question_id, correct, created_at, taxonomy_ids
  from graded order by question_id, created_at, id
),
cells as (
  select s as system, 1 as recent, g.correct::int as recent_correct, 0 as first, 0 as first_correct
  from graded g cross join lateral unnest(g.taxonomy_ids) s
  where g.created_at >= %(now)s - make_interval(days => %(window)s)
  union all
  select s, 0, 0, 1, f.correct::int
  from firsts f cross join lateral unnest(f.taxonomy_ids) s
  where f.created_at >= %(now)s - make_interval(days => %(readiness)s)
)
select system, sum(recent)::int as recent, sum(recent_correct)::int as recent_correct,
       sum(first)::int as first, sum(first_correct)::int as first_correct
from cells where system = any(%(systems)s) group by system
union all
select '', 0, 0, count(*)::int, 0 from firsts where created_at >= %(now)s - make_interval(days => %(readiness)s)
"""


@dataclass(frozen=True, slots=True)
class Cells:
    recent: dict[str, tuple[int, int]]  # system → (graded, correct) in the by-system window
    first: dict[str, tuple[int, int]]  # system → (first attempts, correct) in the readiness window
    answered: int  # distinct questions first answered in the readiness window


async def load_cells(
    conn: psycopg.AsyncConnection[Any],
    exam: str,
    systems: Sequence[str],
    *,
    now: dt.datetime,
    window_days: int,
    readiness_days: int,
) -> Cells:
    rows = await privileged_rows(
        conn,
        _CELLS,
        {"exam": exam, "systems": list(systems), "now": now, "window": window_days, "readiness": readiness_days},
    )
    recent: dict[str, tuple[int, int]] = {}
    first: dict[str, tuple[int, int]] = {}
    answered = 0
    for r in rows:
        if r["system"] == "":
            answered = int(r["first"])
            continue
        if r["recent"]:
            recent[r["system"]] = (int(r["recent"]), int(r["recent_correct"]))
        if r["first"]:
            first[r["system"]] = (int(r["first"]), int(r["first_correct"]))
    return Cells(recent, first, answered)


@dataclass(frozen=True, slots=True)
class SystemStat:
    id: str
    label: str
    weight: float
    attempts: int
    correct: int
    percent: int | None  # whole percent; None under the attempt threshold
    state: SystemState


def by_system(
    weights: Sequence[SystemWeight], recent: Mapping[str, tuple[int, int]], *, min_attempts: int, weak_below: float
) -> list[SystemStat]:
    out: list[SystemStat] = []
    for w in weights:
        n, c = recent.get(w.id, (0, 0))
        if n < min_attempts:
            out.append(SystemStat(w.id, w.label, w.midpoint, n, c, None, "insufficient"))
            continue
        share = c / n
        out.append(
            SystemStat(w.id, w.label, w.midpoint, n, c, round(share * 100), "weak" if share < weak_below else "ok")
        )
    return out


@dataclass(frozen=True, slots=True)
class Readiness:
    score: int | None  # whole percent, None until there is enough data
    coverage: int  # whole percent of the blueprint weight with at least one first attempt
    answered: int
    needed_answered: int
    needed_coverage: int


def readiness(weights: Sequence[SystemWeight], cells: Cells, *, min_answered: int, min_coverage: float) -> Readiness:
    """Blueprint-weighted % correct over first attempts; withheld below the answered/coverage thresholds."""
    acc = weighted_accuracy(cells.first, weights)
    enough = cells.answered >= min_answered and acc.coverage >= min_coverage and acc.score is not None
    return Readiness(
        score=round(acc.score * 100) if enough and acc.score is not None else None,
        coverage=round(acc.coverage * 100),
        answered=cells.answered,
        needed_answered=min_answered,
        needed_coverage=round(min_coverage * 100),
    )


# ── coach facts ──

# questions-scope: rls (the caller's own attempts only: shared questions plus their own private ones)
_MISSES = """
select s as system, count(*)::int as misses
from public.attempts a join public.questions q on q.id = a.question_id cross join lateral unnest(q.taxonomy_ids) s
where a.user_id = (select auth.uid()) and a.correct is false and a.chosen is not null and q.block is null
  and q.exam = %(exam)s and a.created_at >= %(now)s - make_interval(days => %(days)s) and s = any(%(systems)s)
group by s
"""

_HAS_CHANGES = """
select count(*) = 2 as ok from pg_attribute
where attrelid = 'public.attempts'::regclass and attname in ('changes', 'first_chosen') and not attisdropped
"""

# Segment 09's columns: `changes` = number of answer changes, `first_chosen` = the first choice. Right → wrong =
# the first choice was the key and the final one is not.
# questions-scope: rls (the caller's own attempts only: shared questions plus their own private ones)
_CHANGED = """
select count(*) filter (where a.changes > 0)::int as changed,
       count(*) filter (where a.changes > 0 and a.first_chosen = q.answer and a.chosen <> q.answer)::int
         as right_to_wrong
from public.attempts a join public.questions q on q.id = a.question_id
where a.user_id = (select auth.uid()) and a.correct is not null and q.block is null
  and a.created_at >= %(now)s - make_interval(days => %(days)s)
"""

_PLAN = """
select count(*) filter (where i.day < %(today)s and i.status not in ('done', 'skipped'))::int as behind,
       count(*) filter (where i.day < %(today)s and i.status = 'skipped')::int as skipped,
       count(distinct i.day) filter (where i.day < %(today)s and i.status not in ('done', 'skipped'))::int
         as behind_days
from public.plan_items i
where i.user_id = (select auth.uid()) and i.kind in ('read', 'questions', 'cards')
  and i.day >= %(today)s - 14
"""

# Cards due now, counted exactly as the Cards screen counts them (segment 10: visible cards, new ones included).
# VISIBLE reads attempts.correct (the question-linked card gate), so this runs as a privileged statement for
# the caller's own id.
_CARDS = (
    "select count(*) filter (where r.card_id is null or r.due <= %(now)s)::int as due,"  # noqa: S608 - VISIBLE is a constant
    " coalesce(sum(r.reps), 0)::int as reps, coalesce(sum(r.lapses), 0)::int as lapses"
    " from public.flashcards f left join public.card_reviews r on r.card_id = f.id and r.user_id = %(uid)s"
    " where %(uid)s = (select auth.uid()) and " + CARDS_VISIBLE
)


async def cards_due(conn: psycopg.AsyncConnection[Any], *, now: dt.datetime) -> dict[str, int]:
    """{"due", "reps", "lapses"} for the caller (the same `due_now` as GET /api/cards/stats)."""
    row = (await privileged_rows(conn, _CARDS, {"now": now, "uid": await _uid(conn)}))[0]
    return {k: int(v) for k, v in row.items()}


async def _uid(conn: psycopg.AsyncConnection[Any]) -> Any:
    return (await _rows(conn, "select auth.uid() as uid", {}))[0]["uid"]


@dataclass(frozen=True, slots=True)
class CoachFacts:
    """Numbers and taxonomy labels only (no text the student wrote, no identity)."""

    exam: str
    weak_systems: tuple[tuple[str, str, int, int, int], ...]  # (id, label, attempts, correct, percent)
    misses_7d: int
    miss_systems: tuple[tuple[str, str, int], ...]  # (id, label, misses)
    changed_answers: int | None
    right_to_wrong: int | None
    plan_behind_items: int
    plan_behind_days: int
    plan_skipped_items: int
    cards_due: int
    card_retention: int | None  # whole percent of reviews not lapsed

    def as_dict(self) -> dict[str, Any]:
        return {
            "exam": self.exam,
            "weak_systems": [
                {"id": i, "label": label, "attempts": n, "correct": c, "percent": p}
                for i, label, n, c, p in self.weak_systems
            ],
            "misses_7d": self.misses_7d,
            "miss_systems": [{"id": i, "label": label, "misses": m} for i, label, m in self.miss_systems],
            "changed_answers": self.changed_answers,
            "right_to_wrong": self.right_to_wrong,
            "plan_behind_items": self.plan_behind_items,
            "plan_behind_days": self.plan_behind_days,
            "plan_skipped_items": self.plan_skipped_items,
            "cards_due": self.cards_due,
            "card_retention": self.card_retention,
        }


async def coach_facts(
    conn: psycopg.AsyncConnection[Any],
    exam: str,
    *,
    now: dt.datetime,
    today: dt.date,
    window_days: int,
    min_attempts: int,
    weak_below: float,
) -> CoachFacts:
    weights = await load_weights(conn, exam)
    ids = [w.id for w in weights]
    labels = {w.id: w.label for w in weights}
    cells = await load_cells(conn, exam, ids, now=now, window_days=window_days, readiness_days=window_days)
    stats = by_system(weights, cells.recent, min_attempts=min_attempts, weak_below=weak_below)
    weak = sorted((s for s in stats if s.state == "weak"), key=lambda s: (s.percent or 0, -s.weight))
    miss_rows = await privileged_rows(conn, _MISSES, {"exam": exam, "now": now, "days": 7, "systems": ids})
    misses = sorted(((r["system"], int(r["misses"])) for r in miss_rows), key=lambda x: (-x[1], x[0]))
    total_misses = (
        await privileged_rows(
            conn,
            # questions-scope: rls (the caller's own attempts only, filtered on auth.uid())
            "select count(*)::int as n from public.attempts a join public.questions q on q.id = a.question_id"
            " where a.user_id = (select auth.uid()) and a.correct is false and a.chosen is not null"
            " and q.block is null and q.exam = %(exam)s and a.created_at >= %(now)s - interval '7 days'",
            {"exam": exam, "now": now},
        )
    )[0]["n"]
    changed: int | None = None
    r2w: int | None = None
    if (await _rows(conn, _HAS_CHANGES, {}))[0]["ok"]:
        row = (await privileged_rows(conn, _CHANGED, {"now": now, "days": window_days}))[0]
        changed, r2w = int(row["changed"]), int(row["right_to_wrong"])
    plan = (await _rows(conn, _PLAN, {"today": today}))[0]
    cards = await cards_due(conn, now=now)
    reps, lapses = int(cards["reps"]), int(cards["lapses"])
    return CoachFacts(
        exam=exam,
        weak_systems=tuple((s.id, s.label, s.attempts, s.correct, s.percent or 0) for s in weak[:3]),
        misses_7d=int(total_misses),
        miss_systems=tuple((i, labels[i], m) for i, m in misses[:3]),
        changed_answers=changed,
        right_to_wrong=r2w,
        plan_behind_items=int(plan["behind"]),
        plan_behind_days=int(plan["behind_days"]),
        plan_skipped_items=int(plan["skipped"]),
        cards_due=int(cards["due"]),
        card_retention=round(100 * (reps - lapses) / reps) if reps > 0 else None,
    )
