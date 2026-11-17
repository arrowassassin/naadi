"""Request/response models for onboarding, the plan, Today and coach suggestions (structured fields only: the
client words every sentence from `ui_copy`)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ExamId = Literal["step1", "step2ck", "step3"]
Material = Literal["qbank", "review_book", "anki", "video_course", "own_pdfs", "nothing"]
MATERIALS: tuple[Material, ...] = ("qbank", "review_book", "anki", "video_course", "own_pdfs", "nothing")
ItemKind = Literal["read", "questions", "assessment", "cards", "video", "admin", "rest"]
ItemStatus = Literal["planned", "now", "done", "skipped", "behind"]
RefType = Literal["chapter", "qbank", "cards", "mock", "external", "weak_spot", "qbank_incorrect", "section", "block"]
CoachKind = Literal["catch_up_block", "review_misses", "cards_catchup", "reread_section"]


class ExamOption(BaseModel):
    id: ExamId
    label: str = Field(description="The exam's short name from exam_rules (a proper noun, not a sentence)")


class HoursRange(BaseModel):
    min: float
    max: float
    step: float
    default: float


class DiagnosticState(BaseModel):
    available: bool = Field(description="false until timed blocks ship: the onboarding diagnostic step is hidden")
    questions: int
    open_block_id: uuid.UUID | None = None


class OnboardingValues(BaseModel):
    exam: ExamId
    exam_date: dt.date | None
    medical_school: str | None
    daily_hours: float
    study_days: list[int]
    materials: list[Material]


class OnboardingState(BaseModel):
    exams: list[ExamOption] = Field(description="Only exams that have a plan template")
    materials: list[Material]
    hours: HoursRange
    min_study_days: int
    max_days: int = Field(description="The furthest exam date offered is today + max_days")
    today: dt.date = Field(description="Today in the student's time zone (the plan starts here)")
    values: OnboardingValues = Field(description="Saved answers, or the defaults for a new student")
    completed_at: dt.datetime | None
    diagnostic: DiagnosticState


class OnboardingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exam: ExamId
    exam_date: dt.date
    medical_school: str | None = Field(default=None, max_length=120)
    daily_hours: float = Field(ge=1, le=24)
    study_days: list[int] = Field(min_length=1, max_length=7)
    materials: list[Material] = Field(default_factory=list, max_length=len(MATERIALS))
    timezone: str | None = Field(
        default=None, max_length=64, description="The device's IANA time zone; the plan starts on today there"
    )

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v is None:
            return None
        from ..auth_routes import _valid_timezone

        return _valid_timezone(v)

    @field_validator("study_days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if any(d < 1 or d > 7 for d in v):
            raise ValueError("study days are ISO weekdays 1 (Monday) to 7 (Sunday)")
        return sorted(set(v))

    @field_validator("materials")
    @classmethod
    def _materials(cls, v: list[Material]) -> list[Material]:
        return [m for m in MATERIALS if m in v]

    @field_validator("medical_school")
    @classmethod
    def _school(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = " ".join(v.split())
        return v or None


class PlanSummaryOut(BaseModel):
    study_days: int
    weeks: int
    days_per_week: int
    first_day: dt.date
    last_day: dt.date
    assessments: int
    extra_review_days: int
    merged_days: int


class PlanPreview(BaseModel):
    ok: bool
    problem: Literal["too_few_study_days", "too_many_days", "exam_date_not_after_start", "no_template"] | None = None
    available_days: int = Field(description="Dates from today to the day before the exam on a study weekday")
    needed_days: int
    max_days: int
    summary: PlanSummaryOut | None = None


class OnboardingOut(BaseModel):
    plan_id: uuid.UUID
    summary: PlanSummaryOut
    completed_at: dt.datetime


class PlanItemOut(BaseModel):
    id: uuid.UUID
    day: dt.date
    kind: ItemKind
    title: str = Field(description="The plan template's own text, verbatim")
    ref_type: str | None
    ref_id: str | None
    target_minutes: int | None
    target_count: int | None
    status: ItemStatus
    origin: Literal["generator", "user", "assistant", "seed"]
    sort: int | None
    plan_day: int | None


class PlanDayMeta(BaseModel):
    day_no: int
    date: dt.date
    weekday: str
    week: int
    phase: str | None
    chapter_ref: str | None
    chapter_ids: list[str]
    topic: str | None
    target_hours: float | None
    template_day: int
    merged_days: list[int]
    extra: bool
    assessment: bool
    status: str


class PlanDayOut(PlanDayMeta):
    items: list[PlanItemOut]


class PlanOut(BaseModel):
    id: uuid.UUID
    template_id: str
    created_at: dt.datetime
    inputs: dict[str, Any]
    summary: PlanSummaryOut
    days: list[PlanDayOut]


class ItemStatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["planned", "done", "skipped"]


class DiagnosticOut(BaseModel):
    block_id: uuid.UUID
    questions: int
    created: bool = Field(description="false when an open diagnostic already existed and was returned")


# ── Today ──


class ResumeReading(BaseModel):
    kind: Literal["reading"]
    chapter_id: str
    chapter_title: str
    section_id: str
    section_heading: str
    section_n: int


class ResumeBlock(BaseModel):
    kind: Literal["block"]
    block_id: uuid.UUID
    mode: Literal["tutor", "timed", "exam"]
    answered: int
    total: int
    next_n: int
    diagnostic: bool


class ReadinessOut(BaseModel):
    score: int | None = Field(description="Whole percent; null until enough first attempts and coverage")
    coverage: int
    answered: int
    needed_answered: int
    needed_coverage: int
    days: int = Field(description="The look-back window the score is computed over (`coach.readiness_days`)")


class SystemOut(BaseModel):
    id: str
    label: str
    attempts: int
    correct: int
    percent: int | None
    state: Literal["ok", "weak", "insufficient"]


class SystemsOut(BaseModel):
    window_days: int
    min_attempts: int
    weak_below: int = Field(description="Whole percent")
    items: list[SystemOut]


class CardsOut(BaseModel):
    due: int
    reviewable: bool = Field(description="true when the Cards screen is available to link to")


class CoachSuggestionOut(BaseModel):
    id: uuid.UUID
    for_date: dt.date
    kind: CoachKind
    source: Literal["llm", "rules"]
    title: str | None = Field(description="Assistant-written (source llm); null for rules (client uses ui_copy)")
    body: str | None
    facts: dict[str, Any]
    action: dict[str, Any]
    status: Literal["new", "accepted", "dismissed"]


class PathwayStageOut(BaseModel):
    key: str
    title: str | None
    done: int
    total: int


class TodayPlan(BaseModel):
    plan_id: uuid.UUID
    day: PlanDayMeta | None = Field(description="Today's plan day; null on a non-study day")
    items: list[PlanItemOut] = Field(description="Today's items in plan order (generator first, then added ones)")
    next_day: dt.date | None
    now_item: uuid.UUID | None = Field(description="The first item of today not done or skipped")


class TodayOut(BaseModel):
    today: dt.date
    exam: ExamId
    exam_date: dt.date | None
    days_left: int | None
    onboarding_complete: bool
    plan: TodayPlan | None
    resume: ResumeReading | ResumeBlock | None = Field(discriminator="kind")
    readiness: ReadinessOut
    systems: SystemsOut
    cards: CardsOut
    coach: list[CoachSuggestionOut]
    pathway: list[PathwayStageOut] | None = Field(description="Only while the pathway flag is on")


class CoachActionOut(BaseModel):
    suggestion: CoachSuggestionOut
    added_items: list[PlanItemOut]
