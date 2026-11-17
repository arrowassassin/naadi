"""Rebalance (pure): property tests over generated plans (21..180 study days) and the golden missed-days case."""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import replace

import pytest

from naadi_api.config_api.schemas import PlanItemMinutes
from naadi_api.plan.generator import PlanInputs, generate
from naadi_api.plan.items import plan_items
from naadi_api.plan.rebalance import (
    MOVABLE_STATUSES,
    Proposal,
    RDay,
    RebalanceInput,
    RItem,
    missed_before,
    rebalance,
)

from .helpers import REFERENCE, template

SIZES = PlanItemMinutes(
    cards=30, admin=30, extra=30, questions_untimed=60, seconds_per_question=90, review_factor=1.0, min_read=30
)
START = dt.date(2026, 11, 16)


def build(n_days: int, daily_hours: float = 10, study_days: frozenset[int] = frozenset(range(1, 8))):
    """A generated plan with `n_days` calendar days to the exam: (days, items, inputs)."""
    inputs = PlanInputs(
        start=START,
        exam_date=START + dt.timedelta(days=n_days),
        study_days=study_days,
        daily_hours=daily_hours,
    )
    days = generate(template(), inputs)
    items = plan_items(days, "step1", SIZES)
    rdays = [RDay(d.date, d.day_no, d.target_hours, bool(d.self_assessment), d.chapter_ref, d.topic) for d in days]
    ritems: list[RItem] = []
    for d in days:
        for i in items[d.day_no]:
            ritems.append(RItem(f"{d.day_no}-{i.sort}", d.date, i.kind, "planned", i.sort, i.target_minutes, i.title))
    return days, rdays, ritems, inputs


def run(rdays, ritems, inputs, today: dt.date, *, factor: float = 1.25, daily_hours: float = 10) -> Proposal:
    return rebalance(
        RebalanceInput(
            days=rdays,
            items=ritems,
            today=today,
            missed_before=today,
            exam_date=inputs.exam_date,
            study_days=inputs.study_days,
            daily_hours=daily_hours,
            max_items_per_day_factor=factor,
        )
    )


def after(items: list[RItem], p: Proposal) -> list[RItem]:
    moved = {m.item_id: m for m in p.moves}
    dropped = {d.item_id for d in p.drops}
    out = []
    for i in items:
        if i.id in dropped:
            out.append(replace(i, status="skipped"))
        elif i.id in moved:
            out.append(replace(i, day=moved[i.id].to_day, sort=moved[i.id].sort))
        else:
            out.append(i)
    return out


def scenario(seed: int):
    rnd = random.Random(seed)  # noqa: S311 - seeded test data, not security
    n = rnd.choice([21, 22, 30, 45, 60, 75, 90, 120, 150, 180])
    hours = rnd.choice([4, 6, 8, 10])
    days, rdays, ritems, inputs = build(n, daily_hours=hours)
    today_idx = rnd.randrange(2, len(days) - 1)
    today = days[today_idx].date
    # The student skipped a random stretch of days before today, did some of the rest.
    gap = rnd.randint(1, min(6, today_idx))
    status = {}
    for i in ritems:
        if i.day < today:
            miss = i.day >= days[today_idx - gap].date and rnd.random() < 0.85
            status[i.id] = "planned" if miss else rnd.choice(["done", "done", "skipped"])
    ritems = [replace(i, status=status.get(i.id, "planned")) for i in ritems]
    return days, rdays, ritems, inputs, today, hours


SEEDS = range(60)


@pytest.mark.parametrize("seed", SEEDS)
def test_invariants(seed: int) -> None:
    days, rdays, ritems, inputs, today, hours = scenario(seed)
    p = run(rdays, ritems, inputs, today, daily_hours=hours)
    by_id = {i.id: i for i in ritems}
    assessment = {d.date for d in rdays if d.assessment} | {i.day for i in ritems if i.kind == "assessment"}
    rest = {days[-1].date, inputs.exam_date - dt.timedelta(days=1)}

    # Assessment and rest days never gain or lose an item; the E-1 rest day stays where it is.
    for m in p.moves:
        assert m.to_day not in assessment and m.to_day not in rest
        assert by_id[m.item_id].day not in assessment | rest
        assert m.to_day >= today and m.to_day < inputs.exam_date
        assert by_id[m.item_id].kind not in ("assessment", "rest")
    for d in p.drops:
        assert d.day not in assessment | rest and by_id[d.item_id].status == "planned"

    # Nothing is silently dropped: every missed item is moved, dropped (listed) or in an `unplaced` warning.
    missed = [
        i
        for i in ritems
        if i.status in MOVABLE_STATUSES
        and i.day < today
        and i.day not in assessment | rest
        and i.kind not in ("assessment", "rest")
    ]
    unplaced = {x for w in p.warnings if w.code == "unplaced" for x in w.item_ids}
    moved = {m.item_id for m in p.moves}
    dropped = {d.item_id for d in p.drops}
    assert {i.id for i in missed} <= moved | dropped | unplaced
    assert not (moved & dropped) and not (moved & unplaced)
    assert p.missed == len(missed)

    # Order kept: moved items land in their original order (by day, sort), never swapped.
    order = {
        i.id: n
        for n, i in enumerate(sorted((by_id[m.item_id] for m in p.moves), key=lambda i: (i.day, i.sort or 0, i.id)))
    }
    landing = [(m.to_day, m.sort) for m in sorted(p.moves, key=lambda m: order[m.item_id])]
    assert landing == sorted(landing)

    # Capacity: a day takes at most target x factor minutes, unless one item alone is longer than the day.
    new = after(ritems, p)
    load: dict[dt.date, list[int]] = {}
    for i in new:
        if i.status != "skipped" and i.day >= today:
            load.setdefault(i.day, []).append(i.target_minutes or 30)
    caps = {d.date: int((d.target_hours or hours) * 60 * 1.25) for d in rdays}
    moved_days = {m.to_day for m in p.moves}
    for day in moved_days:
        mins = load[day]
        assert sum(mins) <= caps[day] or len(mins) == 1  # one item longer than a whole day may sit alone

    # Questions never spill past the assessment day they prepare for.
    next_assessment = min((a for a in assessment if a >= today), default=None)
    for m in p.moves:
        if next_assessment and m.to_day > next_assessment:
            assert m.kind in ("read", "cards", "video", "admin")

    # Deterministic.
    assert run(rdays, ritems, inputs, today, daily_hours=hours) == p
    assert run(rdays, list(reversed(ritems)), inputs, today, daily_hours=hours) == p


@pytest.mark.parametrize("n", [21, 60, 180])
def test_sort_and_days_applied_keep_every_day_ordered(n: int) -> None:
    days, rdays, ritems, inputs = build(n)
    today = days[5].date
    ritems = [replace(i, status="planned") for i in ritems]
    p = run(rdays, ritems, inputs, today)
    new = after(ritems, p)
    seen: set[tuple[dt.date, int | None]] = set()
    for i in new:
        key = (i.day, i.sort)
        assert key not in seen, f"two items on one day share sort {key}"
        seen.add(key)


def test_nothing_missed_proposes_nothing() -> None:
    days, rdays, ritems, inputs = build(60)
    p = run(rdays, ritems, inputs, days[0].date)
    assert p.empty and p.missed == 0 and p.moves == ()


def test_missed_assessment_stays_and_warns() -> None:
    _days, rdays, ritems, inputs = build(60)
    a = next(i for i in ritems if i.kind == "assessment")
    today = a.day + dt.timedelta(days=2)
    p = run(rdays, ritems, inputs, today)
    assert a.id not in {m.item_id for m in p.moves}
    assert any(w.code == "assessment_missed" and a.id in w.item_ids for w in p.warnings)


def test_exam_passed_or_no_days_left_is_a_warning_not_a_drop() -> None:
    days, rdays, ritems, inputs = build(30)
    today = days[-1].date
    p = run(rdays, ritems, inputs, today)
    assert p.moves == ()
    assert any(w.code == "unplaced" and w.count > 0 for w in p.warnings)
    assert any(w.code == "no_study_days" for w in p.warnings)


def test_overflow_drops_review_first_then_spills_reading_only() -> None:
    days, rdays, ritems, inputs = build(45, daily_hours=6)
    today = days[24].date
    # Everything before today is missed: far more than the window before the next assessment can hold.
    p = run(rdays, ritems, inputs, today, daily_hours=6)
    assert p.missed > 0
    review_days = {d.date for d in rdays if d.is_review}
    assert all(d.day in review_days or d.reason == "review" for d in p.drops)
    unplaced = {x for w in p.warnings if w.code == "unplaced" for x in w.item_ids}
    by_id = {i.id: i for i in ritems}
    assert p.drops or unplaced  # something had to give
    # A dropped item is listed with its day; an item still unplaced is a warning with its count.
    for w in p.warnings:
        if w.code == "unplaced":
            assert w.count == len(w.item_ids) > 0
    assert all(by_id[x].kind != "assessment" for x in unplaced)


def test_golden_three_missed_days_in_week_four() -> None:
    """The reference plan (60 days, 10 h, every weekday), the student misses days 22-24 (week 4)."""
    inp = REFERENCE
    days = generate(template(), inp)
    items = plan_items(days, "step1", SIZES)
    rdays = [RDay(d.date, d.day_no, d.target_hours, bool(d.self_assessment), d.chapter_ref, d.topic) for d in days]
    ritems = [
        RItem(f"{d.day_no}-{i.sort}", d.date, i.kind, "planned", i.sort, i.target_minutes, i.title)
        for d in days
        for i in items[d.day_no]
    ]
    today = days[24].date  # day 25
    ritems = [replace(i, status="done") if i.day < days[21].date else i for i in ritems]
    p = run(rdays, ritems, inp, today)
    fixed_days = {d.date for d in rdays if d.assessment} | {days[-1].date}
    missed_ids = [i.id for i in ritems if i.status == "planned" and i.day < today and i.day not in fixed_days]
    assert p.missed == len(missed_ids) > 0
    handled = {m.item_id for m in p.moves} | {d.item_id for d in p.drops}
    handled |= {x for w in p.warnings if w.code == "unplaced" for x in w.item_ids}
    assert set(missed_ids) <= handled
    # Nothing lands before today, the rest day is untouched, and the summary is stable.
    assert min(m.to_day for m in p.moves) >= today
    assert days[-1].date not in {m.to_day for m in p.moves}
    assert (len(p.moves), len(p.drops), [w.code for w in p.warnings]) == GOLDEN
    # Review day 28 gives way (listed as drops); the big question blocks that do not fit before the next
    # assessment are a warning with their ids, never silently lost.
    assert [d.day for d in p.drops] == [dt.date(2026, 12, 13)] * 3
    assert p.warnings[0].count == 5 == len(p.warnings[0].item_ids)


def test_missed_before_waits_for_the_grace_hours() -> None:
    at = lambda h: dt.datetime(2026, 11, 20, h, 0)  # noqa: E731
    assert missed_before(at(5), 6) == dt.date(2026, 11, 19)  # yesterday's items are not yet missed
    assert missed_before(at(6), 6) == dt.date(2026, 11, 20)
    assert missed_before(at(23), 6) == dt.date(2026, 11, 20)


GOLDEN = (7, 3, ["unplaced"])
