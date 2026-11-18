"""The timed/exam session clock: a pure state machine over `block_sections` rows and the server's `now`.

Nothing ticks in the background. Every request loads the rows, calls `advance(session, now)` to apply what
happened since the last request (a section that ran past its deadline ends as `expired`; a break whose shared
pool is spent starts the next section at the instant the pool ran out), persists the changed rows and answers
from `snapshot(session, now)`. So a closed tab still expires on time, and the client never decides time.

Rules (HANDOFF 5.1/8, segment 09):
- A section runs `time_limit_s`; pausing (timed mode only) moves its deadline by the paused time.
- Between sections a break runs against one pool per session (`break_s`). Starting the next section early
  keeps the rest of the pool; when the pool is spent the next section starts automatically.
- Answers are accepted while the section is running, and for `grace_ms` after its deadline (network latency on
  a last-second answer), never after a section was submitted or for a section that has not started.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

ClockState = Literal["not_started", "running", "paused", "break", "finished"]
EndReason = Literal["submitted", "expired"]


class ClockError(Exception):
    """A transition the session's state does not allow (the API answers 409 with `code`)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def ms(delta: dt.timedelta) -> int:
    return int(delta / dt.timedelta(milliseconds=1))


def after(t: dt.datetime, milliseconds: int) -> dt.datetime:
    return t + dt.timedelta(milliseconds=milliseconds)


@dataclass
class Section:
    n: int
    first_index: int
    count: int
    time_limit_s: int
    started_at: dt.datetime | None = None
    deadline_at: dt.datetime | None = None
    paused_at: dt.datetime | None = None
    paused_total_ms: int = 0
    ended_at: dt.datetime | None = None
    ended_reason: EndReason | None = None
    # True when the row is not in the database (missing or deleted): it is written back as expired.
    missing: bool = False

    @property
    def ended(self) -> bool:
        return self.ended_at is not None

    @property
    def started(self) -> bool:
        return self.started_at is not None

    def holds(self, index: int) -> bool:
        """`index` is 0-based in the block's `question_ids`."""
        return self.first_index <= index < self.first_index + self.count


@dataclass
class Session:
    sections: list[Section]
    break_s: int
    break_used_ms: int = 0
    pausable: bool = False
    grace_ms: int = 2000
    changed: set[int] = field(default_factory=set)  # section numbers to write back
    break_changed: bool = False

    def section(self, n: int) -> Section:
        for s in self.sections:
            if s.n == n:
                return s
        raise ClockError("no_such_section")

    def section_of(self, index: int) -> Section | None:
        return next((s for s in self.sections if s.holds(index)), None)

    @property
    def break_left_ms(self) -> int:
        return max(0, self.break_s * 1000 - self.break_used_ms)

    @property
    def finished(self) -> bool:
        return all(s.ended for s in self.sections)

    def current(self) -> Section | None:
        """The first section that has not ended (the one running, paused, about to start or after a break)."""
        return next((s for s in self.sections if not s.ended), None)

    def previous(self, s: Section) -> Section | None:
        i = self.sections.index(s)
        return self.sections[i - 1] if i > 0 else None


@dataclass(frozen=True)
class Snapshot:
    server_now: dt.datetime
    state: ClockState
    section_n: int | None
    sections: int
    deadline_at: dt.datetime | None
    remaining_ms: int | None
    break_remaining_ms: int | None
    paused: bool
    time_limit_ms: int | None


def layout(counts: Sequence[int], seconds_each: Sequence[int]) -> list[Section]:
    """Sections for a block: consecutive slices of `question_ids`."""
    out: list[Section] = []
    first = 0
    for i, (c, limit) in enumerate(zip(counts, seconds_each, strict=True), start=1):
        out.append(Section(n=i, first_index=first, count=c, time_limit_s=limit))
        first += c
    return out


def _end(session: Session, s: Section, at: dt.datetime, reason: EndReason) -> None:
    if s.paused_at is not None:
        s.paused_total_ms += max(0, ms(at - s.paused_at))
        s.paused_at = None
    s.ended_at = at
    s.ended_reason = reason
    session.changed.add(s.n)


def _start(session: Session, s: Section, at: dt.datetime) -> None:
    s.started_at = at
    s.deadline_at = after(at, s.time_limit_s * 1000)
    session.changed.add(s.n)


def advance(session: Session, now: dt.datetime) -> Session:
    """Apply everything that happened by `now` (in place; returns the session). Idempotent for the same `now`."""
    for lost in session.sections:
        if lost.missing and not lost.ended:
            # Never fresh time: a lost row is an expired section (it ends now: nothing of its time is known).
            _end(session, lost, now, "expired")
    while True:
        s = session.current()
        if s is None:
            return session
        if s.started:
            assert s.deadline_at is not None
            if s.paused_at is None and now >= s.deadline_at:
                _end(session, s, s.deadline_at, "expired")
                continue
            return session
        prev = session.previous(s)
        if prev is None:
            return session  # the session has not started
        assert prev.ended_at is not None
        left = session.break_left_ms
        if ms(now - prev.ended_at) >= left:
            # The break pool ran out while nobody was looking: the section started at that instant.
            session.break_used_ms += left
            session.break_changed = True
            _start(session, s, after(prev.ended_at, left))
            continue
        return session


def snapshot(session: Session, now: dt.datetime) -> Snapshot:
    """The clock as the client sees it (call `advance` first)."""
    total = len(session.sections)
    s = session.current()
    if s is None:
        return Snapshot(now, "finished", None, total, None, None, None, False, None)
    limit_ms = s.time_limit_s * 1000
    if s.started:
        assert s.deadline_at is not None
        if s.paused_at is not None:
            return Snapshot(
                now, "paused", s.n, total, s.deadline_at, max(0, ms(s.deadline_at - s.paused_at)), None, True, limit_ms
            )
        left = max(0, ms(s.deadline_at - now))
        return Snapshot(now, "running", s.n, total, s.deadline_at, left, None, False, limit_ms)
    prev = session.previous(s)
    if prev is None:
        return Snapshot(now, "not_started", s.n, total, None, limit_ms, None, False, limit_ms)
    assert prev.ended_at is not None
    left = max(0, session.break_left_ms - ms(now - prev.ended_at))
    return Snapshot(now, "break", s.n, total, None, limit_ms, left, False, limit_ms)


# ── Transitions requested by the student ──


def start(session: Session, n: int, now: dt.datetime) -> None:
    advance(session, now)
    cur = session.current()
    if cur is None:
        raise ClockError("finished")
    s = cur
    if s.n != n:
        raise ClockError("not_current" if n > s.n else "section_ended")
    if s.started:
        return  # idempotent: a retried start keeps the original start time
    prev = session.previous(s)
    if prev is not None:
        assert prev.ended_at is not None
        session.break_used_ms += min(session.break_left_ms, max(0, ms(now - prev.ended_at)))
        session.break_changed = True
    _start(session, s, now)


def _running(session: Session, n: int, now: dt.datetime) -> Section:
    advance(session, now)
    s = session.current()
    if s is None:
        raise ClockError("finished")
    if s.n != n:
        raise ClockError("not_current" if n > s.n else "section_ended")
    if not s.started:
        raise ClockError("not_started")
    return s


def pause(session: Session, n: int, now: dt.datetime) -> None:
    if not session.pausable:
        raise ClockError("pause_exam_mode")
    s = _running(session, n, now)
    if s.paused_at is None:
        s.paused_at = now
        session.changed.add(s.n)


def resume(session: Session, n: int, now: dt.datetime) -> None:
    s = _running(session, n, now)
    if s.paused_at is None:
        return
    assert s.deadline_at is not None
    held = max(0, ms(now - s.paused_at))
    s.deadline_at = after(s.deadline_at, held)
    s.paused_total_ms += held
    s.paused_at = None
    session.changed.add(s.n)


def end(session: Session, n: int, now: dt.datetime) -> None:
    """The student ends section `n` early ("End block"); the break (or the end of the session) follows."""
    advance(session, now)
    s = session.section(n)
    if s.ended:
        return  # idempotent
    if session.current() is not s or not s.started:
        raise ClockError("not_started" if session.current() is s else "not_current")
    _end(session, s, now, "submitted")


def finish(session: Session, now: dt.datetime) -> None:
    """End the whole session now: the running section is submitted, sections never started end unstarted."""
    advance(session, now)
    for s in session.sections:
        if not s.ended:
            _end(session, s, now, "submitted")


# ── Answers ──


def answer_window(session: Session, index: int, now: dt.datetime) -> Section:
    """The section an answer for question `index` belongs to, if an answer may be recorded now, else raises."""
    advance(session, now)
    s = session.section_of(index)
    if s is None or not s.started:
        raise ClockError("not_started" if s is not None else "no_such_section")
    assert s.deadline_at is not None
    if s.ended:
        late_ok = s.ended_reason == "expired" and s.ended_at is not None and now <= after(s.ended_at, session.grace_ms)
        if not late_ok:
            raise ClockError("section_ended")
        return s
    if s.paused_at is not None:
        raise ClockError("paused")
    if now > after(s.deadline_at, session.grace_ms):
        raise ClockError("section_ended")
    return s


def elapsed_s(s: Section, now: dt.datetime) -> int:
    """Seconds the section has actually run (pauses excluded): the ceiling for one question's time."""
    if s.started_at is None or s.deadline_at is None:
        return 0
    stop = min(now, s.ended_at or now, s.deadline_at)
    if s.paused_at is not None:
        stop = min(stop, s.paused_at)
    return max(0, ms(stop - s.started_at) - s.paused_total_ms) // 1000


def copy(session: Session) -> Session:
    return dataclasses.replace(
        session, sections=[dataclasses.replace(s) for s in session.sections], changed=set(session.changed)
    )
