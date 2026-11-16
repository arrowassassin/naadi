"""The plan generator: golden output for the reference inputs, and invariants for every other input."""

from __future__ import annotations

import datetime as dt
import itertools
from dataclasses import replace

import pytest

from naadi_api.plan.generator import (
    PlanError,
    PlanInputs,
    available_dates,
    generate,
    scaled_hours,
    summarize,
    to_csv,
)

from .helpers import CSV, REFERENCE, template


def test_reference_inputs_reproduce_the_csv_byte_for_byte() -> None:
    days = generate(template(), REFERENCE)
    assert len(days) == 60
    assert to_csv(days) == CSV.read_bytes()


def test_reference_maps_template_day_k_to_the_kth_date() -> None:
    days = generate(template(), REFERENCE)
    assert [d.template_day for d in days] == list(range(1, 61))
    assert days[-1].date == REFERENCE.exam_date - dt.timedelta(days=1)


def test_summary_is_structured() -> None:
    s = summarize(generate(template(), REFERENCE), REFERENCE)
    assert (s.study_days, s.weeks, s.days_per_week, s.assessments) == (60, 9, 7, 10)
    assert (s.extra_review_days, s.merged_days) == (0, 0)


@pytest.mark.parametrize(
    ("template_h", "daily", "want"),
    [(10, 10, 10), (8, 10, 8), (10, 6, 6), (8, 6, 5), (7, 5, 3.5), (6, 1, 1), (9, 4, 3.5), (None, 4, None)],
)
def test_hours_scale_round_to_half_with_a_floor_of_one(template_h: float | None, daily: float, want: float) -> None:
    assert scaled_hours(template_h, replace(REFERENCE, daily_hours=daily)) == want


def _inputs(n_days: int, study_days: frozenset[int], start: dt.date = REFERENCE.start) -> PlanInputs:
    return replace(REFERENCE, start=start, exam_date=start + dt.timedelta(days=n_days), study_days=study_days)


def _check(inputs: PlanInputs) -> None:
    tpl = template()
    if len(available_dates(inputs)) < inputs.min_study_days:
        with pytest.raises(PlanError, match="too_few_study_days"):
            generate(tpl, inputs)
        return
    days = generate(tpl, inputs)
    avail = available_dates(inputs)
    last = inputs.exam_date - dt.timedelta(days=1)
    assert len(avail) >= inputs.min_study_days
    # Dates: strictly increasing, inside the range, study weekdays only (the rest day always on exam − 1).
    dates = [d.date for d in days]
    assert dates == sorted(set(dates))
    assert dates[0] >= inputs.start
    assert dates[-1] == last
    assert all(d.isoweekday() in inputs.study_days for d in dates[:-1])
    assert set(dates[:-1]) == set(avail) - {last}
    # Anchors: day 1 first, rest last, every assessment kept, in template order.
    assert days[0].template_day == 1
    assert days[-1].template_day == tpl[-1].day
    assessments = [t.day for t in tpl if t.self_assessment]
    kept = [d.template_day for d in days if d.self_assessment]
    assert kept == assessments
    order = [k for d in days if not d.extra for k in d.merged_days]
    assert order == sorted(order)
    assert len(order) == len(set(order))
    for d in days:
        assert d.weekday == ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[d.date.weekday()]
        assert d.week == (d.date - inputs.start).days // 7 + 1
        assert d.day_no == days.index(d) + 1
        if d.self_assessment or d.template_day == 1:
            assert len(d.merged_days) == 1
    # Every template day is accounted for: kept, merged into a neighbour, or one of the dropped review rows.
    dropped = set(range(1, len(tpl) + 1)) - set(order)
    assert all(tpl[k - 1].is_review or tpl[k - 1].is_mixed_review for k in dropped)


@pytest.mark.parametrize("n", range(21, 181))
def test_every_length_with_all_seven_days(n: int) -> None:
    inputs = _inputs(n, frozenset(range(1, 8)))
    days = generate(template(), inputs)
    assert len(days) == n
    _check(inputs)


def test_every_weekday_set_across_lengths() -> None:
    sets = [frozenset(c) for k in range(1, 8) for c in itertools.combinations(range(1, 8), k)]
    assert len(sets) == 127
    for w, span in itertools.product(sets, (24, 45, 70, 100, 150, 260)):
        _check(_inputs(span, w))


def test_rest_day_lands_on_the_eve_even_when_it_is_not_a_study_day() -> None:
    inputs = _inputs(70, frozenset({1, 2, 3, 4, 5}), start=dt.date(2026, 11, 16))  # exam on a Tuesday? check eve
    days = generate(template(), inputs)
    assert days[-1].date == inputs.exam_date - dt.timedelta(days=1)
    assert days[-1].topic and days[-1].topic.startswith("REST")


def test_short_plans_drop_reviews_before_merging() -> None:
    days = generate(template(), _inputs(56, frozenset(range(1, 8))))  # 4 fewer than the template
    assert not any(len(d.merged_days) > 1 for d in days)
    assert sum(1 for d in days if d.chapter_ref == "Review") == 0
    days = generate(template(), _inputs(45, frozenset(range(1, 8))))
    assert not any((d.topic or "").startswith("Mixed review") for d in days)
    merged = [d for d in days if len(d.merged_days) > 1]
    assert merged
    assert all(" + " in (d.topic or "") for d in merged)


def test_long_plans_insert_review_days_through_both_phases() -> None:
    days = generate(template(), _inputs(90, frozenset(range(1, 8))))
    extras = [d for d in days if d.extra]
    assert len(extras) == 30
    assert {d.phase for d in extras} == {"Build (system-by-system)", "Dedicated (mixed, timed)"}
    assert all(d.chapter_ref == "Review" and d.template_day == 7 for d in extras)


def test_too_few_study_days_is_a_structured_error() -> None:
    with pytest.raises(PlanError) as e:
        generate(template(), _inputs(30, frozenset({6, 7})))
    assert e.value.code == "too_few_study_days"
    assert e.value.facts == {"available": 8, "needed": 21}


def test_exam_date_must_follow_start() -> None:
    with pytest.raises(PlanError) as e:
        generate(template(), _inputs(0, frozenset(range(1, 8))))
    assert e.value.code == "exam_date_not_after_start"
