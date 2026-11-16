"""Unit: block selection (filters, no duplicates, deterministic seed), live facet counts, weak-spot ranking."""

from __future__ import annotations

import datetime as dt

from naadi_api.qbank.selection import (
    GradedAttempt,
    History,
    PoolQuestion,
    choose,
    facet_counts,
    filter_pool,
    matches_status,
    weak_cells,
    weak_spot_order,
)

SYS_A, SYS_B = "x.sys.a", "x.sys.b"
DISC_1, DISC_2 = "x.disc.1", "x.disc.2"


def pool() -> list[PoolQuestion]:
    return [
        PoolQuestion("q1", (SYS_A, DISC_1), "easy"),
        PoolQuestion("q2", (SYS_A, DISC_2), "hard"),
        PoolQuestion("q3", (SYS_B, DISC_1), "medium"),
        PoolQuestion("q4", (SYS_B, DISC_2), "medium"),
        PoolQuestion("q5", (), None),
    ]


HISTORY = {
    "q1": History(answered=True, last_correct=False, flagged=False),
    "q2": History(answered=True, last_correct=True, flagged=True),
    "q3": History(answered=False, last_correct=None, flagged=True),
}


def ids(qs: list[PoolQuestion]) -> list[str]:
    return [q.id for q in qs]


def test_no_filters_returns_everything() -> None:
    assert ids(filter_pool(pool(), HISTORY)) == ["q1", "q2", "q3", "q4", "q5"]


def test_groups_are_or_within_and_across() -> None:
    assert ids(filter_pool(pool(), HISTORY, systems=[SYS_A, SYS_B])) == ["q1", "q2", "q3", "q4"]
    assert ids(filter_pool(pool(), HISTORY, systems=[SYS_A], disciplines=[DISC_1])) == ["q1"]
    assert ids(filter_pool(pool(), HISTORY, systems=[SYS_A], disciplines=[DISC_1, DISC_2])) == ["q1", "q2"]


def test_status_filters_use_the_callers_history() -> None:
    assert ids(filter_pool(pool(), HISTORY, status="unused")) == ["q3", "q4", "q5"]
    assert ids(filter_pool(pool(), HISTORY, status="incorrect")) == ["q1"]
    assert ids(filter_pool(pool(), HISTORY, status="correct")) == ["q2"]
    assert ids(filter_pool(pool(), HISTORY, status="flagged")) == ["q2", "q3"]
    assert matches_status(History(), "all")


def test_difficulty_filter() -> None:
    assert ids(filter_pool(pool(), HISTORY, difficulty="medium")) == ["q3", "q4"]
    assert ids(filter_pool(pool(), HISTORY, status="unused", difficulty="medium")) == ["q3", "q4"]


def test_facet_counts_ignore_their_own_group() -> None:
    f = facet_counts(
        pool(),
        HISTORY,
        systems=[SYS_A],
        disciplines=[],
        status="all",
        difficulty=None,
        system_ids=[SYS_A, SYS_B],
        discipline_ids=[DISC_1, DISC_2],
    )
    assert f.total == 2  # q1, q2
    assert f.systems == {SYS_A: 2, SYS_B: 2}  # choosing system B would still match its own two questions
    assert f.disciplines == {DISC_1: 1, DISC_2: 1}  # given system A
    assert f.status["unused"] == 0 and f.status["incorrect"] == 1 and f.status["all"] == 2
    assert f.difficulty == {"easy": 1, "medium": 0, "hard": 1, "all": 2}


def test_choose_is_deterministic_distinct_and_seed_dependent() -> None:
    ids_ = [f"q{i}" for i in range(50)]
    a = choose(ids_, 10, seed=7)
    assert a == choose(list(reversed(ids_)), 10, seed=7)  # independent of input order
    assert len(set(a)) == 10
    assert a != choose(ids_, 10, seed=8)
    assert choose(ids_ + ids_, 50, seed=1) and len(choose(ids_ + ids_, 50, seed=1)) == 50  # no duplicates
    assert set(choose(ids_, 5, seed=7)) <= set(choose(ids_, 10, seed=7))  # a prefix of the same ordering


NOW = dt.datetime(2026, 11, 16, 12, 0, tzinfo=dt.UTC)


def attempt(qid: str, ok: bool, days_ago: float) -> GradedAttempt:
    return GradedAttempt(qid, ok, NOW - dt.timedelta(days=days_ago))


KINDS = {SYS_A: "system", SYS_B: "system", DISC_1: "discipline", DISC_2: "discipline", "step1": "exam"}
TAXONOMY = {
    "q1": (SYS_A, DISC_1, "step1"),
    "q2": (SYS_A, DISC_2),
    "q3": (SYS_B, DISC_1),
    "q4": (SYS_B, DISC_2),
}


def test_weak_cells_rank_recent_misses_first_and_need_minimum_attempts() -> None:
    attempts = [
        attempt("q1", False, 1),
        attempt("q2", False, 1),
        attempt("q1", False, 2),  # SYS_A: 3 misses
        attempt("q3", True, 1),
        attempt("q4", True, 1),
        attempt("q3", True, 2),  # SYS_B: 3 right
    ]
    cells = weak_cells(attempts, TAXONOMY, KINDS, now=NOW)
    assert cells[0].id == SYS_A
    assert SYS_B not in [c.id for c in cells]  # never missed
    assert "step1" not in [c.id for c in cells]  # only system / discipline / topic cells
    assert weak_cells(attempts[:2], TAXONOMY, KINDS, now=NOW) == []  # below the minimum attempts


def test_old_misses_weigh_less_than_recent_ones() -> None:
    recent_miss = [attempt("q1", False, 0.5), attempt("q1", True, 60), attempt("q1", True, 61)]
    old_miss = [attempt("q3", False, 90), attempt("q3", True, 1), attempt("q3", True, 2)]
    cells = {c.id: c for c in weak_cells(recent_miss + old_miss, TAXONOMY, KINDS, now=NOW)}
    assert cells[SYS_A].score > cells[SYS_B].score


def test_weak_spot_order_puts_missed_then_unseen_then_correct() -> None:
    history = {
        "q1": History(True, False, False),
        "q2": History(True, True, False),
        "q3": History(False, None, False),
    }
    order = weak_spot_order(pool(), history, [SYS_A, SYS_B], seed=3)
    assert order[0] == "q1"  # missed
    assert set(order[1:3]) == {"q3", "q4"}  # unseen
    assert order[3] == "q2"  # right last
    assert "q5" not in order  # not in a weak cell
    assert order == weak_spot_order(pool(), history, [SYS_A, SYS_B], seed=3)
