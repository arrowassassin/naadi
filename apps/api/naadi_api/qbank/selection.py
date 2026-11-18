"""Pure block-building logic (no database): pool filters, live facet counts, deterministic selection and the
weak-spot ranking. The routes feed these rows read under the caller's RLS; tests cover them directly."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Status = Literal["all", "unused", "incorrect", "flagged", "correct"]
Difficulty = Literal["easy", "medium", "hard"]

STATUSES: tuple[Status, ...] = ("all", "unused", "incorrect", "flagged", "correct")
DIFFICULTIES: tuple[Difficulty, ...] = ("easy", "medium", "hard")

# Weak-spot ranking: a cell needs this many graded attempts before it can be called weak, recent attempts
# weigh more (half-life), and a prior of one attempt at 50% keeps a single miss from topping the list.
WEAK_MIN_ATTEMPTS = 3
WEAK_HALF_LIFE_DAYS = 14.0
WEAK_PRIOR_WEIGHT = 2.0
WEAK_CELLS = 3
WEAK_CELL_KINDS = frozenset({"system", "discipline", "clinical_science", "topic"})


@dataclass(frozen=True, slots=True)
class PoolQuestion:
    id: str
    taxonomy_ids: tuple[str, ...]
    difficulty: str | None


@dataclass(frozen=True, slots=True)
class History:
    """What the caller's own attempts say about one question."""

    answered: bool = False  # at least one graded attempt
    last_correct: bool | None = None  # outcome of the most recent graded attempt
    flagged: bool = False  # the most recent attempt row is flagged


NO_HISTORY = History()


def matches_status(h: History, status: Status) -> bool:
    if status == "all":
        return True
    if status == "unused":
        return not h.answered
    if status == "incorrect":
        return h.answered and h.last_correct is False
    if status == "correct":
        return h.answered and h.last_correct is True
    return h.flagged


def _tagged(q: PoolQuestion, ids: frozenset[str]) -> bool:
    return not ids or any(t in ids for t in q.taxonomy_ids)


def filter_pool(
    questions: Iterable[PoolQuestion],
    history: Mapping[str, History],
    *,
    systems: Iterable[str] = (),
    disciplines: Iterable[str] = (),
    status: Status = "all",
    difficulty: Difficulty | None = None,
) -> list[PoolQuestion]:
    """Questions matching every filter (systems and disciplines are OR within a group, AND across groups)."""
    sys_ids, disc_ids = frozenset(systems), frozenset(disciplines)
    return [
        q
        for q in questions
        if _tagged(q, sys_ids)
        and _tagged(q, disc_ids)
        and matches_status(history.get(q.id, NO_HISTORY), status)
        and (difficulty is None or q.difficulty == difficulty)
    ]


@dataclass(frozen=True, slots=True)
class Facets:
    total: int
    systems: dict[str, int]
    disciplines: dict[str, int]
    status: dict[str, int]
    difficulty: dict[str, int]


def facet_counts(
    questions: Sequence[PoolQuestion],
    history: Mapping[str, History],
    *,
    systems: Sequence[str],
    disciplines: Sequence[str],
    status: Status,
    difficulty: Difficulty | None,
    system_ids: Iterable[str],
    discipline_ids: Iterable[str],
) -> Facets:
    """Live counts: each chip shows how many questions would match if it were chosen, given every *other*
    group's current selection (so chips of the same group never zero each other out)."""

    def pool(
        sys_sel: Sequence[str] = systems,
        disc_sel: Sequence[str] = disciplines,
        st: Status = status,
        diff: Difficulty | None = difficulty,
    ) -> list[PoolQuestion]:
        return filter_pool(questions, history, systems=sys_sel, disciplines=disc_sel, status=st, difficulty=diff)

    sys_set, disc_set = set(system_ids), set(discipline_ids)
    sys_counts = dict.fromkeys(sys_set, 0)
    for q in pool(sys_sel=()):
        for t in set(q.taxonomy_ids) & sys_set:
            sys_counts[t] += 1
    disc_counts = dict.fromkeys(disc_set, 0)
    for q in pool(disc_sel=()):
        for t in set(q.taxonomy_ids) & disc_set:
            disc_counts[t] += 1
    status_counts: dict[str, int] = {s: len(pool(st=s)) for s in STATUSES}
    diff_counts: dict[str, int] = {d: len(pool(diff=d)) for d in DIFFICULTIES}
    diff_counts["all"] = len(pool(diff=None))
    return Facets(len(pool()), sys_counts, disc_counts, status_counts, diff_counts)


def _rank(seed: int, qid: str) -> bytes:
    return hashlib.sha256(f"{seed}:{qid}".encode()).digest()


def choose(ids: Iterable[str], count: int, seed: int) -> list[str]:
    """`count` distinct ids: pseudo-random but a pure function of (ids, count, seed)."""
    return sorted(set(ids), key=lambda i: _rank(seed, i))[:count]


@dataclass(frozen=True, slots=True)
class GradedAttempt:
    question_id: str
    correct: bool
    at: datetime


@dataclass(frozen=True, slots=True)
class WeakCell:
    id: str
    attempts: int
    correct: int
    score: float  # recency-weighted error rate in [0, 1]; higher is weaker


def weak_cells(
    attempts: Iterable[GradedAttempt],
    taxonomy: Mapping[str, Sequence[str]],
    kinds: Mapping[str, str],
    *,
    now: datetime,
    top: int = WEAK_CELLS,
    min_attempts: int = WEAK_MIN_ATTEMPTS,
) -> list[WeakCell]:
    """The caller's weakest taxonomy cells: accuracy weighted by recency, with a minimum number of attempts."""
    weight_err: dict[str, float] = defaultdict(float)
    weight_all: dict[str, float] = defaultdict(float)
    count: dict[str, int] = defaultdict(int)
    right: dict[str, int] = defaultdict(int)
    for a in attempts:
        age_days = max(0.0, (now - a.at).total_seconds() / 86400.0)
        w = 0.5 ** (age_days / WEAK_HALF_LIFE_DAYS)
        for cell in set(taxonomy.get(a.question_id, ())):
            if kinds.get(cell) not in WEAK_CELL_KINDS:
                continue
            weight_all[cell] += w
            weight_err[cell] += 0.0 if a.correct else w
            count[cell] += 1
            right[cell] += 1 if a.correct else 0
    cells = [
        WeakCell(c, count[c], right[c], (weight_err[c] + WEAK_PRIOR_WEIGHT * 0.5) / (weight_all[c] + WEAK_PRIOR_WEIGHT))
        for c in count
        if count[c] >= min_attempts and right[c] < count[c]  # a cell never missed is not a weak spot
    ]
    cells.sort(key=lambda c: (-c.score, -c.attempts, c.id))
    return cells[:top]


def weak_spot_order(
    questions: Sequence[PoolQuestion], history: Mapping[str, History], cells: Iterable[str], seed: int
) -> list[str]:
    """Question ids from the weak cells: previously missed first, then unseen, then answered right; each tier
    shuffled deterministically."""
    wanted = frozenset(cells)
    tiers: tuple[list[str], list[str], list[str]] = ([], [], [])
    for q in questions:
        if not any(t in wanted for t in q.taxonomy_ids):
            continue
        h = history.get(q.id, NO_HISTORY)
        tiers[0 if h.answered and h.last_correct is False else 1 if not h.answered else 2].append(q.id)
    return [i for tier in tiers for i in sorted(tier, key=lambda i: _rank(seed, i))]
