"""The timed/exam session clock with a frozen clock: deadlines, pause, breaks, auto-start, expiry, grace,
closed sections, missing rows."""

from __future__ import annotations

import datetime as dt

import pytest

from naadi_api.qbank import clock
from naadi_api.qbank.clock import ClockError, Session, layout

T0 = dt.datetime(2026, 11, 16, 9, 0, tzinfo=dt.UTC)


def at(seconds: float) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


def mock_session(*, pausable: bool = False, break_s: int = 45 * 60) -> Session:
    """Mock A: 2 sections of 40 questions, 60 minutes each, 45 minutes of break pool."""
    return Session(sections=layout([40, 40], [3600, 3600]), break_s=break_s, pausable=pausable, grace_ms=2000)


def test_layout_slices_questions_consecutively() -> None:
    s = layout([40, 40], [3600, 3600])
    assert [(x.n, x.first_index, x.count) for x in s] == [(1, 0, 40), (2, 40, 40)]
    assert s[0].holds(0) and s[0].holds(39) and not s[0].holds(40) and s[1].holds(79) and not s[1].holds(80)


def test_not_started_until_section_one_starts() -> None:
    s = mock_session()
    snap = clock.snapshot(clock.advance(s, at(100_000)), at(100_000))
    assert snap.state == "not_started" and snap.section_n == 1 and snap.remaining_ms == 3_600_000
    assert snap.deadline_at is None


def test_running_deadline_and_remaining_come_from_timestamps() -> None:
    s = mock_session()
    clock.start(s, 1, at(0))
    snap = clock.snapshot(clock.advance(s, at(61.5)), at(61.5))
    assert snap.state == "running" and snap.section_n == 1
    assert snap.deadline_at == at(3600)
    assert snap.remaining_ms == 3_538_500
    # a retried start keeps the original start time
    clock.start(s, 1, at(120))
    assert s.sections[0].started_at == at(0)


def test_section_expires_at_its_deadline_and_the_break_starts_there() -> None:
    s = mock_session()
    clock.start(s, 1, at(0))
    clock.advance(s, at(3600 + 30))
    first = s.sections[0]
    assert first.ended_at == at(3600) and first.ended_reason == "expired"
    snap = clock.snapshot(s, at(3630))
    assert snap.state == "break" and snap.section_n == 2
    assert snap.break_remaining_ms == 45 * 60_000 - 30_000


def test_spent_break_pool_auto_starts_the_next_section_at_that_instant() -> None:
    s = mock_session()
    clock.start(s, 1, at(0))
    clock.end(s, 1, at(1800))  # submitted early
    clock.advance(s, at(1800 + 45 * 60 + 600))  # nobody came back
    second = s.sections[1]
    assert second.started_at == at(1800 + 45 * 60)
    assert second.deadline_at == at(1800 + 45 * 60 + 3600)
    assert s.break_used_ms == 45 * 60_000
    snap = clock.snapshot(s, at(1800 + 45 * 60 + 600))
    assert snap.state == "running" and snap.remaining_ms == 3_000_000


def test_a_closed_tab_still_finishes_the_whole_session_on_time() -> None:
    s = mock_session()
    clock.start(s, 1, at(0))
    clock.advance(s, at(10 * 3600))
    assert s.finished
    assert s.sections[0].ended_at == at(3600)
    assert s.sections[1].started_at == at(3600 + 2700)
    assert s.sections[1].ended_at == at(3600 + 2700 + 3600)
    assert clock.snapshot(s, at(10 * 3600)).state == "finished"


def test_starting_early_keeps_the_rest_of_the_break_pool() -> None:
    s = Session(sections=layout([10, 10, 10], [600, 600, 600]), break_s=900)
    clock.start(s, 1, at(0))
    clock.end(s, 1, at(300))
    clock.start(s, 2, at(300 + 240))  # 4 minutes of break
    assert s.break_used_ms == 240_000
    clock.advance(s, at(540 + 600 + 1))  # section 2 expires
    snap = clock.snapshot(s, at(540 + 601))
    assert snap.state == "break" and snap.break_remaining_ms == 660_000 - 1000
    # the pool (11 minutes left) runs out: section 3 starts by itself
    clock.advance(s, at(1140 + 660 + 5))
    assert s.sections[2].started_at == at(1140 + 660)
    assert s.break_used_ms == 900_000


def test_pause_only_in_timed_mode() -> None:
    s = mock_session(pausable=False)
    clock.start(s, 1, at(0))
    with pytest.raises(ClockError) as e:
        clock.pause(s, 1, at(10))
    assert e.value.code == "pause_exam_mode"


def test_pause_freezes_time_and_resume_moves_the_deadline() -> None:
    s = Session(sections=layout([20], [1800]), break_s=0, pausable=True)
    clock.start(s, 1, at(0))
    clock.pause(s, 1, at(600))
    later = clock.snapshot(clock.advance(s, at(5000)), at(5000))  # far past the original deadline: still paused
    assert later.state == "paused" and later.remaining_ms == 1_200_000 and later.paused
    clock.resume(s, 1, at(5000))
    sec = s.sections[0]
    assert sec.deadline_at == at(1800 + 4400) and sec.paused_total_ms == 4_400_000 and sec.paused_at is None
    assert clock.snapshot(s, at(5000)).remaining_ms == 1_200_000
    assert clock.elapsed_s(sec, at(5100)) == 700  # pauses excluded


def test_transitions_on_the_wrong_section_are_refused() -> None:
    s = mock_session()
    with pytest.raises(ClockError) as e:
        clock.start(s, 2, at(0))
    assert e.value.code == "not_current"
    with pytest.raises(ClockError) as e:
        clock.end(s, 1, at(0))
    assert e.value.code == "not_started"
    clock.start(s, 1, at(0))
    clock.end(s, 1, at(10))
    clock.end(s, 1, at(20))  # idempotent
    assert s.sections[0].ended_at == at(10)
    with pytest.raises(ClockError) as e:
        clock.start(s, 1, at(30))
    assert e.value.code == "section_ended"


def test_answers_inside_the_section_and_the_grace_window_only() -> None:
    s = mock_session()
    with pytest.raises(ClockError) as e:
        clock.answer_window(s, 0, at(0))
    assert e.value.code == "not_started"
    clock.start(s, 1, at(0))
    assert clock.answer_window(s, 39, at(3599)).n == 1
    with pytest.raises(ClockError) as e:
        clock.answer_window(s, 40, at(10))  # a question of section 2
    assert e.value.code == "not_started"
    # 1.5 s after the deadline (in flight): still recorded; 2.5 s after: refused
    assert clock.answer_window(clock.copy(s), 5, at(3601.5)).n == 1
    with pytest.raises(ClockError) as e:
        clock.answer_window(clock.copy(s), 5, at(3602.5))
    assert e.value.code == "section_ended"


def test_a_submitted_section_takes_no_late_answers() -> None:
    s = mock_session()
    clock.start(s, 1, at(0))
    clock.end(s, 1, at(100))
    with pytest.raises(ClockError) as e:
        clock.answer_window(s, 0, at(100.5))
    assert e.value.code == "section_ended"


def test_paused_sections_take_no_answers() -> None:
    s = Session(sections=layout([5], [300]), break_s=0, pausable=True)
    clock.start(s, 1, at(0))
    clock.pause(s, 1, at(10))
    with pytest.raises(ClockError) as e:
        clock.answer_window(s, 0, at(11))
    assert e.value.code == "paused"


def test_a_missing_row_is_never_fresh_time() -> None:
    s = mock_session()
    clock.start(s, 1, at(0))
    s.sections[1].missing = True
    clock.end(s, 1, at(60))
    clock.advance(s, at(61))
    assert s.sections[1].ended_reason == "expired" and s.sections[1].started_at is None
    assert clock.snapshot(s, at(61)).state == "finished"
    with pytest.raises(ClockError):
        clock.answer_window(s, 41, at(62))


def test_finish_ends_everything_now() -> None:
    s = mock_session()
    clock.start(s, 1, at(0))
    clock.finish(s, at(42))
    assert [x.ended_reason for x in s.sections] == ["submitted", "submitted"]
    assert s.sections[1].started_at is None and s.finished


def test_elapsed_is_capped_by_the_deadline() -> None:
    s = Session(sections=layout([5], [300]), break_s=0)
    clock.start(s, 1, at(0))
    assert clock.elapsed_s(s.sections[0], at(10_000)) == 300
    assert clock.elapsed_s(s.sections[0], at(42.9)) == 42


def test_advance_is_idempotent_for_the_same_instant() -> None:
    s = mock_session()
    clock.start(s, 1, at(0))
    a = clock.advance(clock.copy(s), at(9000))
    b = clock.advance(clock.advance(clock.copy(s), at(9000)), at(9000))
    assert [(x.started_at, x.ended_at) for x in a.sections] == [(x.started_at, x.ended_at) for x in b.sections]
    assert a.break_used_ms == b.break_used_ms
