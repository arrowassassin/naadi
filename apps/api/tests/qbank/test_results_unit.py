"""Results aggregates: outcomes, changed answers, time, flags, topics (weak under 60 %), the official weight
overlay and the deterministic insights."""

from __future__ import annotations

import datetime as dt
import uuid

from naadi_api.qbank import results
from naadi_api.qbank.exam_models import TrendPoint
from naadi_api.qbank.results import GradedQuestion, Node

NODES = {
    "s.cardio": Node("s.cardio", "system", "Cardiovascular", 1, 7, 11),
    "s.renal": Node("s.renal", "system", "Renal", 2, 11, 15),
    "s.gi": Node("s.gi", "system", "Gastrointestinal", 3, 6, 10),
    "d.path": Node("d.path", "discipline", "Pathology", 10, 45, 55),
    "c.mk": Node("c.mk", "competency", "Medical knowledge", 20, 60, 70),
    "t.x": Node("t.x", "topic", "A topic", 30, None, None),
}


def q(n: int, chosen: str | None, first: str | None = None, *, tags=("s.cardio",), secs=60, flagged=False):
    return GradedQuestion(
        n=n,
        section_n=1 if n <= 40 else 2,
        question_id=f"q{n}",
        answer="B",
        chosen=chosen,
        first_chosen=first if first is not None else chosen,
        seconds=secs if chosen else None,
        flagged=flagged,
        taxonomy_ids=tuple(tags),
    )


def test_outcomes_and_changes() -> None:
    qs = [q(1, "B"), q(2, "A"), q(3, None), q(4, "A", "B"), q(5, "B", "C"), q(6, "C", "A")]
    assert [results.outcome(x) for x in qs] == ["correct", "incorrect", "omitted", "incorrect", "correct", "incorrect"]
    assert [results.change_of(x) for x in qs] == [
        None,
        None,
        None,
        "right_to_wrong",
        "wrong_to_right",
        "wrong_to_wrong",
    ]
    c = results.change_stats(qs)
    assert (c.changed, c.right_to_wrong, c.wrong_to_right, c.wrong_to_wrong) == (3, 1, 1, 1)


def test_time_stats_use_answered_questions_only() -> None:
    qs = [q(1, "B", secs=30), q(2, "A", secs=90), q(3, "B", secs=120), q(4, None)]
    t = results.time_stats(qs, 90)
    assert t.mean_s == 80.0 and t.median_s == 90.0 and t.target_s == 90 and t.total_s == 240
    assert results.time_stats([q(1, None)], 90).mean_s is None


def test_flags() -> None:
    f = results.flag_stats([q(1, "B", flagged=True), q(2, "A", flagged=True), q(3, "B")])
    assert (f.flagged, f.flagged_correct) == (2, 1)


def test_topics_weakest_first_and_weak_below_sixty_percent() -> None:
    qs = [
        q(1, "B", tags=("s.cardio", "d.path")),
        q(2, "B", tags=("s.cardio",)),
        q(3, "A", tags=("s.renal", "d.path")),
        q(4, "B", tags=("s.renal",)),
        q(5, "A", tags=("s.renal",)),
        q(6, "B", tags=("t.x", "s.cardio", "s.cardio")),  # a duplicate tag counts once
    ]
    sys = results.topic_stats(qs, NODES, "system")
    assert [(s.id, s.total, s.correct, s.percent, s.weak) for s in sys] == [
        ("s.renal", 3, 1, 33.3, True),
        ("s.cardio", 3, 3, 100.0, False),
    ]
    disc = results.topic_stats(qs, NODES, "discipline")
    assert [(d.id, d.percent, d.weak) for d in disc] == [("d.path", 50.0, True)]
    # 60 % exactly is not weak
    assert not results.topic_stats([q(1, "B"), q(2, "B"), q(3, "B"), q(4, "A"), q(5, "A")], NODES, "system")[0].weak


def test_weight_overlay_lists_every_weighted_system_in_order() -> None:
    qs = [q(1, "B"), q(2, "A"), q(3, "B", tags=("s.gi",)), q(4, "B")]
    w = results.weight_overlay(qs, NODES)
    assert [(x.id, x.share, x.total, x.percent, x.weight_min, x.weight_max) for x in w] == [
        ("s.cardio", 75.0, 3, 66.7, 7, 11),
        ("s.renal", 0.0, 0, None, 11, 15),
        ("s.gi", 25.0, 1, 100.0, 6, 10),
    ]


def test_insights_are_deterministic_and_structured() -> None:
    qs = [
        q(1, "A", "B", tags=("s.renal",), secs=150, flagged=True),
        q(2, "A", tags=("s.renal",), secs=150),
        q(3, "B", tags=("s.cardio",), secs=150, flagged=True),
        q(4, "B", tags=("s.cardio",), secs=150),
    ]
    sys = results.topic_stats(qs, NODES, "system")
    w = results.weight_overlay(qs, NODES)
    trend = [
        TrendPoint(
            block_id=uuid.uuid4(),
            finished_at=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
            score=80,
            mode="exam",
            current=False,
        ),
        TrendPoint(
            block_id=uuid.uuid4(),
            finished_at=dt.datetime(2026, 11, 2, tzinfo=dt.UTC),
            score=50,
            mode="exam",
            current=True,
        ),
    ]
    kwargs = dict(
        percent=50.0,
        time=results.time_stats(qs, 90),
        changes=results.change_stats(qs),
        flags=results.flag_stats(qs),
        systems=sys,
        disciplines=[],
        weights=w,
        trend=trend,
    )
    out = results.insights(**kwargs)  # type: ignore[arg-type]
    assert [i.kind for i in out] == ["weakest_topic", "strongest_topic", "pace_slow", "changes_hurt", "flags_right"]
    assert out[0].vars == {"topic": "Renal", "correct": 0, "total": 2}
    assert out[2].vars == {"mean": 150, "target": 90}
    assert results.insights(**kwargs) == out  # type: ignore[arg-type]
    # nothing to say about an empty block
    empty = results.insights(
        percent=0.0,
        time=results.time_stats([], 90),
        changes=results.change_stats([]),
        flags=results.flag_stats([]),
        systems=[],
        disciplines=[],
        weights=[],
        trend=[],
    )
    assert empty == []


def test_trend_insight_when_no_room_is_taken() -> None:
    trend = [
        TrendPoint(
            block_id=uuid.uuid4(),
            finished_at=dt.datetime(2026, 11, 1, tzinfo=dt.UTC),
            score=60,
            mode="timed",
            current=False,
        ),
        TrendPoint(
            block_id=uuid.uuid4(),
            finished_at=dt.datetime(2026, 11, 2, tzinfo=dt.UTC),
            score=72,
            mode="exam",
            current=True,
        ),
    ]
    out = results.insights(
        percent=72.0,
        time=results.time_stats([], None),
        changes=results.change_stats([]),
        flags=results.flag_stats([]),
        systems=[],
        disciplines=[],
        weights=[],
        trend=trend,
    )
    assert [(i.kind, i.vars) for i in out] == [("trend_up", {"delta": 12, "previous": 60})]
