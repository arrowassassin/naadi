"""Study-plan generator: a pure function from a plan template and the student's inputs to dated plan days.

No I/O. The routes read the template from `plan_template_days` and store the result in `study_plan_days` and
`plan_items`; tests feed it the CSV directly.

Rules (docs/segments/11-onboarding-today-coach.md):
- Dates D = start .. exam_date − 1; available A = the dates in D whose ISO weekday is a study day; n = |A|.
- The template's last day (the rest day) always lands on exam_date − 1. Every other template day maps, in
  order, onto the remaining available dates A′ = A \\ {exam_date − 1}.
- More dates than template days: extra copies of the template's weekly "Review" row are inserted evenly
  through the whole plan (both phases), each taking the phase of the day before it.
- Fewer dates: drop "Review" rows, then "Mixed review" rows (each evenly spaced), then merge adjacent topic days
  of the same chapter (topics joined with " + "), then, only if still needed, adjacent topic days of different
  chapters. Day 1 (the baseline), assessment days (`self_assessment` set) and the rest day are never dropped or
  merged, and the order of what remains never changes.
- Week = (date − start) // 7 + 1, weekday from the date, target hours = template × daily_hours / reference_hours
  rounded to 0.5 with a minimum of 1 (the rest day keeps its own value).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal

GENERATOR_VERSION = 1
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
REVIEW_REF = "Review"
MIXED_PREFIX = "Mixed review"
JOIN = " + "
NOT_STARTED = "Not started"

# The CSV's columns, in order (data/study_plan_step1_60d.csv). Status and score are the student's own.
CSV_HEADER = (
    "Day",
    "Date",
    "Weekday",
    "Week",
    "Phase",
    "Book chapter",
    "Topic / focus",
    "Book questions",
    "UWorld Step 1",
    "Self-assessment",
    "Admin / application task",
    "Extra resource",
    "Target hours",
    "Status",
    "Score / notes",
)
# How the CSV spells an empty cell: the qbank column uses an em dash, every other column is blank.
QBANK_EMPTY = "—"


@dataclass(frozen=True, slots=True)
class TemplateDay:
    """One row of `plan_template_days` (or of the CSV)."""

    day: int
    phase: str | None = None
    chapter_ref: str | None = None
    chapter_ids: tuple[str, ...] = ()
    topic: str | None = None
    chapter_questions: str | None = None
    qbank: str | None = None
    self_assessment: str | None = None
    admin_task: str | None = None
    extra_resource: str | None = None
    target_hours: float | None = None

    @property
    def is_review(self) -> bool:
        return self.chapter_ref == REVIEW_REF

    @property
    def is_mixed_review(self) -> bool:
        return bool(self.topic and self.topic.startswith(MIXED_PREFIX))

    @property
    def is_assessment(self) -> bool:
        return self.self_assessment is not None


@dataclass(frozen=True, slots=True)
class PlanInputs:
    start: dt.date
    exam_date: dt.date
    study_days: frozenset[int]  # ISO weekdays, 1 = Monday … 7 = Sunday
    daily_hours: float
    reference_hours: float = 10.0
    min_study_days: int = 21


@dataclass(frozen=True, slots=True)
class PlanDay:
    day_no: int
    date: dt.date
    weekday: str
    week: int
    phase: str | None
    chapter_ref: str | None
    chapter_ids: tuple[str, ...]
    topic: str | None
    chapter_questions: str | None
    qbank: str | None
    self_assessment: str | None
    admin_task: str | None
    extra_resource: str | None
    target_hours: float | None
    template_day: int  # the first template day this row came from
    merged_days: tuple[int, ...] = ()  # every template day folded into this row (merges), in order
    extra: bool = False  # an inserted Review day
    status: str = NOT_STARTED
    score_notes: str = ""


class PlanError(ValueError):
    """Inputs that cannot produce a plan. `code` is structured (the client formats the message)."""

    def __init__(self, code: str, **facts: int | str) -> None:
        super().__init__(code)
        self.code = code
        self.facts = facts


@dataclass(slots=True)
class _Slot:
    """A body row while the plan is being shaped."""

    t: TemplateDay
    days: list[int] = field(default_factory=list)
    extra: bool = False

    @property
    def protected(self) -> bool:
        return self.t.day == 1 or self.t.is_assessment

    @property
    def mergeable(self) -> bool:
        return not (self.protected or self.extra or self.t.is_review or self.t.is_mixed_review)


def available_dates(inputs: PlanInputs) -> list[dt.date]:
    out: list[dt.date] = []
    d = inputs.start
    while d < inputs.exam_date:
        if d.isoweekday() in inputs.study_days:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def scaled_hours(template_hours: float | None, inputs: PlanInputs) -> float | None:
    if template_hours is None:
        return None
    raw = Decimal(str(template_hours)) * Decimal(str(inputs.daily_hours)) / Decimal(str(inputs.reference_hours))
    halves = (raw * 2).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return max(1.0, float(halves / 2))


def _evenly(candidates: Sequence[int], k: int) -> list[int]:
    """k of the candidate positions, spread evenly (deterministic)."""
    if k >= len(candidates):
        return list(candidates)
    step = len(candidates) / k
    return [candidates[int(step * i + step / 2)] for i in range(k)]


def _join(values: Iterable[str | None]) -> str | None:
    seen: list[str] = []
    for v in values:
        if v and v not in seen:
            seen.append(v)
    return JOIN.join(seen) if seen else None


def _merge(a: _Slot, b: _Slot) -> _Slot:
    ta, tb = a.t, b.t
    hours = [h for h in (ta.target_hours, tb.target_hours) if h is not None]
    merged = replace(
        ta,
        chapter_ref=_join([ta.chapter_ref, tb.chapter_ref]),
        chapter_ids=tuple(dict.fromkeys([*ta.chapter_ids, *tb.chapter_ids])),
        topic=_join([ta.topic, tb.topic]),
        chapter_questions=_join([ta.chapter_questions, tb.chapter_questions]),
        qbank=ta.qbank or tb.qbank,
        admin_task=_join([ta.admin_task, tb.admin_task]),
        extra_resource=_join([ta.extra_resource, tb.extra_resource]),
        target_hours=max(hours) if hours else None,
    )
    return _Slot(merged, [*a.days, *b.days])


def _drop(body: list[_Slot], need: int, pick: str) -> int:
    idx = [i for i, s in enumerate(body) if not s.protected and getattr(s.t, pick)]
    gone = set(_evenly(idx, min(need, len(idx))))
    body[:] = [s for i, s in enumerate(body) if i not in gone]
    return len(gone)


def _merge_pass(body: list[_Slot], need: int, *, same_chapter: bool) -> int:
    done = 0
    while done < need:
        pairs = [
            i
            for i in range(len(body) - 1)
            if body[i].mergeable
            and body[i + 1].mergeable
            and body[i].t.phase == body[i + 1].t.phase
            and (not same_chapter or body[i].t.chapter_ref == body[i + 1].t.chapter_ref)
        ]
        if not pairs:
            break
        # Merge the lightest pair first (fewest days already folded in), earliest on a tie, so merges spread out
        # instead of piling three or four topics onto one day while others stay single.
        i = min(pairs, key=lambda j: (len(body[j].days) + len(body[j + 1].days), j))
        body[i : i + 2] = [_merge(body[i], body[i + 1])]
        done += 1
    return done


def shape(template: Sequence[TemplateDay], target: int) -> list[_Slot]:
    """The template's body (everything but the rest day) stretched or squeezed to `target` rows."""
    body = [_Slot(t, [t.day]) for t in template[:-1]]
    if target > len(body):
        review = next((t for t in template if t.is_review), None)
        if review is None:
            raise PlanError("template_has_no_review_day")
        extra = target - len(body)
        n = len(body)
        # Insert after body positions spread evenly over the plan; never before day 1.
        positions = [max(1, round((k + 1) * n / (extra + 1))) for k in range(extra)]
        out: list[_Slot] = []
        for i, slot in enumerate(body):
            out.append(slot)
            for _ in range(positions.count(i + 1)):
                out.append(_Slot(replace(review, phase=out[-1].t.phase), [review.day], extra=True))
        return out
    need = len(body) - target
    for step in ("is_review", "is_mixed_review"):
        if need > 0:
            need -= _drop(body, need, step)
    if need > 0:
        need -= _merge_pass(body, need, same_chapter=True)
    if need > 0:
        need -= _merge_pass(body, need, same_chapter=False)
    if need > 0:
        raise PlanError("too_few_study_days", available=target + 1, needed=target + 1 + need)
    return body


def generate(template: Sequence[TemplateDay], inputs: PlanInputs) -> list[PlanDay]:
    if not template:
        raise PlanError("template_empty")
    if not inputs.study_days or not inputs.study_days <= set(range(1, 8)):
        raise PlanError("no_study_days")
    if inputs.exam_date <= inputs.start:
        raise PlanError("exam_date_not_after_start")
    dates = available_dates(inputs)
    if len(dates) < inputs.min_study_days:
        raise PlanError("too_few_study_days", available=len(dates), needed=inputs.min_study_days)
    last = inputs.exam_date - dt.timedelta(days=1)
    body_dates = [d for d in dates if d != last]
    body = shape(template, len(body_dates))
    rest = template[-1]
    rows: list[tuple[dt.date, _Slot]] = [*zip(body_dates, body, strict=True), (last, _Slot(rest, [rest.day]))]
    out: list[PlanDay] = []
    for n, (date, slot) in enumerate(rows, start=1):
        t = slot.t
        is_rest = slot.t.day == rest.day and not slot.extra
        out.append(
            PlanDay(
                day_no=n,
                date=date,
                weekday=WEEKDAYS[date.weekday()],
                week=(date - inputs.start).days // 7 + 1,
                phase=t.phase,
                chapter_ref=t.chapter_ref,
                chapter_ids=t.chapter_ids,
                topic=t.topic,
                chapter_questions=t.chapter_questions,
                qbank=t.qbank,
                self_assessment=t.self_assessment,
                admin_task=t.admin_task,
                extra_resource=t.extra_resource,
                target_hours=t.target_hours if is_rest else scaled_hours(t.target_hours, inputs),
                template_day=slot.days[0],
                merged_days=tuple(slot.days),
                extra=slot.extra,
            )
        )
    return out


def format_hours(h: float | None) -> str:
    if h is None:
        return ""
    return f"{h:g}"


def to_csv(days: Iterable[PlanDay]) -> bytes:
    """The plan in the template CSV's exact shape (CRLF, UTF-8, hours as `8` not `8.0`)."""
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_HEADER)
    for d in days:
        w.writerow(
            [
                d.day_no,
                d.date.isoformat(),
                d.weekday,
                d.week,
                d.phase or "",
                d.chapter_ref or "",
                d.topic or "",
                d.chapter_questions or "",
                d.qbank or QBANK_EMPTY,
                d.self_assessment or "",
                d.admin_task or "",
                d.extra_resource or "",
                format_hours(d.target_hours),
                d.status,
                d.score_notes,
            ]
        )
    return buf.getvalue().encode("utf-8")


@dataclass(frozen=True, slots=True)
class PlanSummary:
    """Structured preview ("11 weeks, six days a week"); the client words it."""

    study_days: int
    weeks: int
    days_per_week: int
    first_day: dt.date
    last_day: dt.date
    assessments: int
    extra_review_days: int
    merged_days: int


def summarize(days: Sequence[PlanDay], inputs: PlanInputs) -> PlanSummary:
    return PlanSummary(
        study_days=len(days),
        weeks=days[-1].week,
        days_per_week=len(inputs.study_days),
        first_day=days[0].date,
        last_day=days[-1].date,
        assessments=sum(1 for d in days if d.self_assessment),
        extra_review_days=sum(1 for d in days if d.extra),
        merged_days=sum(1 for d in days if len(d.merged_days) > 1),
    )
