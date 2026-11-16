"""FSRS-6 scheduling for flashcards: the official `fsrs` package, server-authoritative.

The web previews intervals with ts-fsrs 6's FSRS-6 algorithm driven by the same policy (apps/web/src/cards/fsrs.ts);
the shared golden vectors (packages/shared/fixtures/fsrs-golden.json) prove both give the same state, step and
due time for the same reviews. `fsrs` 6.3.3 is pinned because its default weights, learning/relearning steps and
fuzz formula are ts-fsrs 6.0.0's.

The one change to the package: `fsrs.Scheduler` draws fuzz from the unseeded `random.random()`, so a preview could
never equal the result. `SeededScheduler` draws it from the RNG ts-fsrs's fuzzing middleware uses (FNV-1a hash of
`f"{card_id}{reps}"` seeding mulberry32), so the preview a student saw is what the server schedules.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fsrs import Card, Rating, Scheduler, State

NEW = 0  # no review yet (no card_reviews row); 1-3 are fsrs.State values
FUZZ_RANGES = ((2.5, 7.0, 0.15), (7.0, 20.0, 0.1), (20.0, math.inf, 0.05))
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def fnv1a32(s: str) -> int:
    """FNV-1a over UTF-16 code units (JavaScript's `charCodeAt`), as ts-fsrs hashes its fuzz seed."""
    h = 2166136261
    data = s.encode("utf-16-le")
    for i in range(0, len(data), 2):
        h ^= data[i] | (data[i + 1] << 8)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def mulberry32_first(seed: int) -> float:
    """The first value of mulberry32(seed), in [0, 1) (32-bit arithmetic as in JavaScript's Math.imul)."""

    def imul(a: int, b: int) -> int:
        return (a * b) & 0xFFFFFFFF

    t = (seed + 1831565813) & 0xFFFFFFFF
    t = imul(t ^ (t >> 15), t | 1)
    t = (t ^ ((t + imul(t ^ (t >> 7), t | 61)) & 0xFFFFFFFF)) & 0xFFFFFFFF
    return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296


def fuzz_seed(card_id: str, reps_after: int) -> str:
    return f"{card_id}{reps_after}"


def _round_half_up(x: float) -> int:
    return math.floor(x + 0.5)  # JavaScript's Math.round (Python's round() rounds half to even)


class SeededScheduler(Scheduler):
    """`fsrs.Scheduler` whose fuzz comes from a seeded RNG instead of `random.random()`."""

    def __init__(self, *, fuzz_seed: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._fuzz_seed = fuzz_seed

    def _get_fuzzed_interval(self, *, interval: timedelta) -> timedelta:
        days = interval.days
        if days < 2.5:
            return interval
        delta = 1.0
        for start, end, factor in FUZZ_RANGES:
            delta += factor * max(min(float(days), end) - start, 0.0)
        lo = max(2, _round_half_up(days - delta))
        hi = min(_round_half_up(days + delta), self.maximum_interval)
        lo = min(lo, hi)
        f = mulberry32_first(fnv1a32(self._fuzz_seed))
        return timedelta(days=min(math.floor(f * (hi - lo + 1) + lo), self.maximum_interval))


@dataclass(frozen=True)
class FsrsParams:
    """The `fsrs` app_config key, validated (config_api.schemas.FsrsConfig)."""

    weights: tuple[float, ...]
    desired_retention: float
    learning_steps_s: tuple[int, ...]
    relearning_steps_s: tuple[int, ...]
    maximum_interval: int
    enable_fuzz: bool

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> FsrsParams:
        return cls(
            weights=tuple(float(w) for w in cfg["weights"]),
            desired_retention=float(cfg["desired_retention"]),
            learning_steps_s=tuple(int(s) for s in cfg["learning_steps_s"]),
            relearning_steps_s=tuple(int(s) for s in cfg["relearning_steps_s"]),
            maximum_interval=int(cfg["maximum_interval"]),
            enable_fuzz=bool(cfg["enable_fuzz"]),
        )

    def scheduler(self, seed: str) -> SeededScheduler:
        return SeededScheduler(
            fuzz_seed=seed,
            parameters=self.weights,
            desired_retention=self.desired_retention,
            learning_steps=tuple(timedelta(seconds=s) for s in self.learning_steps_s),
            relearning_steps=tuple(timedelta(seconds=s) for s in self.relearning_steps_s),
            maximum_interval=self.maximum_interval,
            enable_fuzzing=self.enable_fuzz,
        )


def validate_params(weights: Sequence[float]) -> None:
    """Raises ValueError when the weights are not 21 values inside the FSRS-6 bounds (the package's own check)."""
    Scheduler(parameters=weights)


@dataclass(frozen=True)
class Memory:
    """One student's scheduling state of one card (a `card_reviews` row; `state == NEW` when there is none)."""

    state: int = NEW
    step: int | None = None
    stability: float | None = None
    difficulty: float | None = None
    due: datetime | None = None
    last_review: datetime | None = None
    reps: int = 0
    lapses: int = 0


@dataclass(frozen=True)
class Outcome:
    rating: int
    memory: Memory  # after the review (due and last_review set)
    elapsed_days: int  # whole 24-hour days since the previous review, 0 for a new card
    scheduled_days: int  # the new interval in whole days (0 while the next step is minutes away)


def elapsed_days(last_review: datetime | None, at: datetime) -> int:
    """Whole 24-hour periods, as `fsrs` counts them (time zones and DST never change a schedule)."""
    return 0 if last_review is None else max(0, (at - last_review).days)


def review(params: FsrsParams, card_id: str, memory: Memory, rating: int, at: datetime) -> Outcome:
    """Schedule one rating. `at` must be timezone-aware; it is converted to UTC (as the package requires)."""
    if at.tzinfo is None:
        raise ValueError("review time must be timezone-aware")
    at = at.astimezone(UTC)
    if memory.state == NEW:
        card = Card(card_id=0, state=State.Learning, step=0, due=at)
    else:
        card = Card(
            card_id=0,
            state=State(memory.state),
            step=memory.step,
            stability=memory.stability,
            difficulty=memory.difficulty,
            due=memory.due or at,
            last_review=memory.last_review,
        )
    reps = memory.reps + 1
    after, _log = params.scheduler(fuzz_seed(card_id, reps)).review_card(card, Rating(rating), at)
    lapses = memory.lapses + (1 if memory.state == State.Review and rating == Rating.Again else 0)
    interval = after.due - at
    assert after.stability is not None
    assert after.difficulty is not None
    return Outcome(
        rating=rating,
        memory=Memory(
            state=int(after.state),
            step=after.step,
            stability=float(after.stability),
            difficulty=float(after.difficulty),
            due=after.due,
            last_review=at,
            reps=reps,
            lapses=lapses,
        ),
        elapsed_days=elapsed_days(memory.last_review, at),
        scheduled_days=interval.days,
    )


def epoch_ms(t: datetime) -> int:
    return (t - _EPOCH) // timedelta(milliseconds=1)
