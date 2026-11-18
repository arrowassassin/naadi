"""Rebalance after missed days: a pure function (no I/O, no clock, no LLM) from the plan's days and items to a
proposal. The `planner` graph computes it, shows it, and applies it only after the student confirms.

Rules (docs/segments/16-planner.md):
- Missed = `planned` / `now` / `behind` items on a day before `missed_before` (the caller derives that date from the
  student's local time and `app_config.planner.missed_after_hours`, see `missed_before`).
- Assessment days (a day with `self_assessment`, or holding an `assessment` item) never lose or gain an item, and the
  final rest day (exam date − 1, or the plan's last day) is never touched either. A missed assessment item stays
  where it is and is reported as a warning.
- Missed items keep their order and go to the earliest study days from today up to the next assessment day. A day
  takes at most `target_hours × max_items_per_day_factor` minutes (its own items count).
- When they do not fit, planned review items (`Review` / `Mixed review` days, plus missed items of such days) are
  dropped first, in a fixed order, one at a time, until everything fits. Only then may items spill past the
  assessment day, and only reading and card items: a question item never moves past the assessment that depends on
  it. Whatever is still unplaced is a warning, never silently dropped.
- Items are placed in queue order with a forward-only cursor, so two moved items never swap order. Moved items
  are appended after the day's own items. Deterministic: the same input always gives the same output.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from .generator import MIXED_PREFIX, REVIEW_REF

DEFAULT_ITEM_MINUTES = 30  # an item without a duration counts as this much when a day's load is summed
MOVABLE_STATUSES = frozenset({"planned", "now", "behind"})
SPILLS = frozenset({"read", "cards", "video", "admin"})  # never `questions`, `assessment` or `rest`

WarningCode = Literal["unplaced", "assessment_missed", "no_study_days"]
DropReason = Literal["review"]


@dataclass(frozen=True, slots=True)
class RDay:
    date: dt.date
    day_no: int
    target_hours: float | None
    assessment: bool = False
    chapter_ref: str | None = None
    topic: str | None = None

    @property
    def is_review(self) -> bool:
        return self.chapter_ref == REVIEW_REF or (self.topic or "").startswith(MIXED_PREFIX)


@dataclass(frozen=True, slots=True)
class RItem:
    id: str
    day: dt.date
    kind: str
    status: str
    sort: int | None = None
    target_minutes: int | None = None
    title: str = ""
    origin: str = "generator"


@dataclass(frozen=True, slots=True)
class RebalanceInput:
    days: Sequence[RDay]
    items: Sequence[RItem]
    today: dt.date
    missed_before: dt.date  # days strictly before this date count as missed (<= today)
    exam_date: dt.date
    study_days: frozenset[int]  # ISO weekdays
    daily_hours: float  # capacity fallback for a day without target hours
    max_items_per_day_factor: float = 1.25


@dataclass(frozen=True, slots=True)
class Move:
    item_id: str
    from_day: dt.date
    to_day: dt.date
    sort: int
    kind: str
    title: str
    minutes: int | None


@dataclass(frozen=True, slots=True)
class Drop:
    item_id: str
    day: dt.date
    reason: DropReason
    kind: str
    title: str


@dataclass(frozen=True, slots=True)
class PlanWarning:
    code: WarningCode
    count: int
    item_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Proposal:
    moves: tuple[Move, ...] = ()
    drops: tuple[Drop, ...] = ()
    warnings: tuple[PlanWarning, ...] = ()
    missed: int = 0
    spilled: int = 0  # moves that landed after an assessment day
    fingerprint: str = field(default="")

    @property
    def empty(self) -> bool:
        return not self.moves and not self.drops and not self.warnings

    def to_json(self) -> dict[str, Any]:
        return {
            "moves": [
                {
                    "item_id": m.item_id,
                    "from_day": m.from_day.isoformat(),
                    "to_day": m.to_day.isoformat(),
                    "sort": m.sort,
                    "kind": m.kind,
                    "title": m.title,
                    "minutes": m.minutes,
                }
                for m in self.moves
            ],
            "drops": [
                {"item_id": d.item_id, "day": d.day.isoformat(), "reason": d.reason, "kind": d.kind, "title": d.title}
                for d in self.drops
            ],
            "warnings": [{"code": w.code, "count": w.count, "item_ids": list(w.item_ids)} for w in self.warnings],
            "missed": self.missed,
            "spilled": self.spilled,
            "fingerprint": self.fingerprint,
        }


def missed_before(now_local: dt.datetime, missed_after_hours: float) -> dt.date:
    """Days before the returned date count as missed: a day D is missed once `missed_after_hours` have passed since
    it ended (D + 1 at 00:00), so with 6 h yesterday's items turn "behind" at 06:00 today."""
    return (now_local - dt.timedelta(hours=missed_after_hours)).date()


def fingerprint(moves: Iterable[Move], drops: Iterable[Drop]) -> str:
    """Identity of what a proposal would change, so a confirm can tell that the plan moved on since it was shown."""
    payload = json.dumps(
        [[m.item_id, m.from_day.isoformat(), m.to_day.isoformat(), m.sort] for m in moves]
        + [[d.item_id, d.day.isoformat()] for d in drops],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _minutes(i: RItem) -> int:
    return i.target_minutes if i.target_minutes is not None else DEFAULT_ITEM_MINUTES


def _order(i: RItem) -> tuple[dt.date, int, str]:
    return (i.day, i.sort if i.sort is not None else 1 << 30, i.id)


class _Placement:
    """One attempt at placing the queue; a fresh instance per attempt keeps the drop loop simple."""

    def __init__(
        self, inp: RebalanceInput, windows: list[list[RDay]], load: dict[dt.date, int], cap: dict[dt.date, int]
    ):
        self.inp = inp
        self.windows = windows
        self.load = dict(load)
        self.cap = cap

    def fits(self, day: dt.date, minutes: int) -> bool:
        used = self.load[day]
        # An item longer than a whole day still lands on an empty day (it would never fit anywhere otherwise).
        return used + minutes <= self.cap[day] or used == 0

    def run(self, queue: Sequence[RItem], *, spill: bool = True) -> tuple[list[tuple[RItem, dt.date]], list[RItem]]:
        """Place the queue in order with a forward-only cursor. Without `spill` only the stretch before the next
        assessment day is used. Items that may not spill (questions) always search that first stretch from its
        start, so an earlier reading item that had to move past the assessment never makes them look unplaceable."""
        placed: list[tuple[RItem, dt.date]] = []
        unplaced: list[RItem] = []
        w, k = 0, 0  # cursor of the items that may spill: window index, day index within the window
        k0 = 0  # separate forward-only cursor in window 0 for the items that may not spill (questions)
        for it in queue:
            m = _minutes(it)
            if spill and it.kind in SPILLS:
                found: dt.date | None = None
                ww, kk = w, k
                while ww < len(self.windows):
                    days = self.windows[ww]
                    while kk < len(days):
                        if self.fits(days[kk].date, m):
                            found = days[kk].date
                            break
                        kk += 1
                    if found is not None:
                        break
                    ww, kk = ww + 1, 0
                if found is None:
                    unplaced.append(it)
                    continue
                w, k = ww, kk
            else:
                # Questions never spill past the assessment they prepare for. Their own cursor only moves forward,
                # so two questions keep their order, and an earlier reading item that had to move past the
                # assessment never makes them look unplaceable.
                days = self.windows[0] if self.windows else []
                kk = max(k0, k if w == 0 else 0)  # never behind an earlier item that stayed in window 0
                while kk < len(days) and not self.fits(days[kk].date, m):
                    kk += 1
                if kk >= len(days):
                    unplaced.append(it)
                    continue
                k0 = kk
                if w == 0:
                    k = kk
                found = days[kk].date
            self.load[found] += m
            placed.append((it, found))
        return placed, unplaced


def rebalance(inp: RebalanceInput) -> Proposal:
    days = sorted(inp.days, key=lambda d: (d.date, d.day_no))
    if not days:
        return Proposal(fingerprint=fingerprint((), ()))
    last = days[-1]
    rest_dates = {last.date, inp.exam_date - dt.timedelta(days=1)}
    assessment_dates = {d.date for d in days if d.assessment}
    assessment_dates |= {i.day for i in inp.items if i.kind == "assessment"}
    by_date = {d.date: d for d in days}

    def fixed(date: dt.date) -> bool:
        return date in assessment_dates or date in rest_dates

    # What is missed, in plan order. A day that is fixed (assessment day, rest day) is never touched, so what was
    # missed on one stays there and is only reported.
    past = [i for i in inp.items if i.status in MOVABLE_STATUSES and i.day < inp.missed_before]
    missed_items = sorted((i for i in past if not fixed(i.day) and i.kind not in ("assessment", "rest")), key=_order)
    missed_assessments = sorted((i for i in past if i.day in assessment_dates), key=_order)
    warnings: list[PlanWarning] = []
    n_missed = len(missed_items)
    if missed_assessments:
        warnings.append(
            PlanWarning(
                "assessment_missed",
                len(missed_assessments),
                tuple(i.id for i in sorted(missed_assessments, key=_order)),
            )
        )
    if not missed_items:
        return Proposal(warnings=tuple(warnings), fingerprint=fingerprint((), ()))

    # Study days from today on, split into windows by the assessment days (which belong to no window).
    study = [
        d
        for d in days
        if d.date >= inp.today
        and d.date < inp.exam_date
        and not fixed(d.date)
        and (not inp.study_days or d.date.isoweekday() in inp.study_days)
    ]
    # One segment per stretch between assessment days (possibly empty): segment 0 runs up to the next assessment.
    boundaries = sorted(a for a in assessment_dates if a >= inp.today)
    windows: list[list[RDay]] = []
    lo = dt.date.min
    for edge in [*boundaries, dt.date.max]:
        windows.append([d for d in study if lo < d.date < edge])
        lo = edge
    if not any(windows):
        return Proposal(
            warnings=(
                *warnings,
                PlanWarning("unplaced", len(missed_items), tuple(i.id for i in missed_items)),
                PlanWarning("no_study_days", 0),
            ),
            missed=n_missed,
            fingerprint=fingerprint((), ()),
        )

    target_dates = [d.date for w in windows for d in w]
    cap: dict[dt.date, int] = {}
    for d in study:
        hours = d.target_hours if d.target_hours is not None else inp.daily_hours
        cap[d.date] = max(1, int(hours * 60 * inp.max_items_per_day_factor))
    keep_load: dict[dt.date, int] = dict.fromkeys(target_dates, 0)
    for i in inp.items:
        if i.day in keep_load and i.status != "skipped":
            keep_load[i.day] += _minutes(i)
    own_sort = {d: 0 for d in target_dates}
    for i in inp.items:
        if i.day in own_sort and i.sort is not None:
            own_sort[i.day] = max(own_sort[i.day], i.sort + 1)

    # Review items that may be dropped to make room, in a fixed order: the queue's own review items first, then
    # planned review items inside the first window (Review days before Mixed review days, earliest first).
    review_days = {d.date for d in days if d.is_review}
    queue_review = [i for i in missed_items if i.day in review_days]
    first = {d.date for d in windows[0]}  # the stretch before the next assessment
    window_review = sorted(
        (
            i
            for i in inp.items
            if i.status == "planned"
            and i.day in first
            and i.day in review_days
            and i.kind not in ("assessment", "rest")
        ),
        key=lambda i: (0 if (by_date[i.day].chapter_ref == REVIEW_REF) else 1, *_order(i)),
    )
    candidates = [*queue_review, *window_review]

    dropped: list[RItem] = []
    queue = list(missed_items)
    load = dict(keep_load)
    while True:
        # Review items give way first: everything is tried inside the stretch before the next assessment day, and
        # only what still does not fit after the reviews are gone may spill past it.
        attempt = _Placement(inp, windows, load, cap)
        placed, unplaced = attempt.run(queue, spill=False)
        if not unplaced or not candidates:
            break
        victim = candidates.pop(0)
        dropped.append(victim)
        if victim in queue:
            queue.remove(victim)
        else:
            load[victim.day] = max(0, load[victim.day] - _minutes(victim))
    # The drops are final: rebuild the placement once more from the committed queue and load.
    final = _Placement(inp, windows, load, cap)
    placed, unplaced = final.run(queue)

    next_sort = dict(own_sort)
    moves: list[Move] = []
    first_assessment_after = boundaries[0] if boundaries else None
    spilled = 0
    for it, to_day in placed:
        s = next_sort[to_day]
        next_sort[to_day] = s + 1
        if first_assessment_after is not None and to_day > first_assessment_after:
            spilled += 1
        moves.append(Move(it.id, it.day, to_day, s, it.kind, it.title, it.target_minutes))
    drops = tuple(Drop(i.id, i.day, "review", i.kind, i.title) for i in dropped)
    if unplaced:
        warnings.append(PlanWarning("unplaced", len(unplaced), tuple(i.id for i in unplaced)))
    return Proposal(
        moves=tuple(moves),
        drops=drops,
        warnings=tuple(warnings),
        missed=n_missed,
        spilled=spilled,
        fingerprint=fingerprint(moves, drops),
    )
