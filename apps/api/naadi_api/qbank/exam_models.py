"""Request and response models of timed/exam blocks and Results (segment 09; OpenAPI -> zod in @naadi/shared).

Responses carry structured fields only (codes, numbers, ids, labels from the taxonomy): every sentence the
student reads is a ui_copy template filled by the client.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ClockStateName = Literal["not_started", "running", "paused", "break", "finished"]


class SectionLayout(BaseModel):
    """One section of a timed/exam block: a consecutive slice of `question_ids`."""

    n: int = Field(ge=1, le=12)
    first_index: int = Field(ge=0)
    count: int = Field(ge=1, le=80)
    time_limit_s: int = Field(ge=1, le=86400)


class BlockClock(BaseModel):
    """The server's clock for a timed/exam block. The client shows `deadline_at - (Date.now() + offset)`,
    where offset is estimated from `server_now`; it never counts down on its own."""

    server_now: dt.datetime
    state: ClockStateName
    section_n: int | None = Field(description="The section running, paused, or next after a break; null when finished")
    sections: int = Field(description="Number of sections in the session")
    deadline_at: dt.datetime | None = Field(description="When the running section ends (moves while paused)")
    remaining_ms: int | None = Field(description="Time left in the section (frozen while paused; full before start)")
    break_remaining_ms: int | None = Field(description="Break time left in the shared pool, during a break only")
    break_left_ms: int = Field(description="The shared break pool still unused (counting down during a break)")
    paused: bool
    time_limit_ms: int | None = Field(description="The current section's full length")
    pausable: bool = Field(description="false in exam mode")
    grace_ms: int = Field(description="Answers sent this long after the deadline are still recorded")


class MockBlockCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: Literal["mock1", "mock2"]


class ActiveExam(BaseModel):
    """The caller's timed/exam session that has not finished (the shell and the assistant guard read it)."""

    block_id: uuid.UUID
    mode: Literal["timed", "exam"]
    clock: BlockClock


class ActiveExamOut(BaseModel):
    active: ActiveExam | None


# ── Results ──

QuestionOutcome = Literal["correct", "incorrect", "omitted"]
ChangeKind = Literal["right_to_wrong", "wrong_to_right", "wrong_to_wrong"]


class ResultQuestion(BaseModel):
    n: int
    section_n: int
    question_id: str
    outcome: QuestionOutcome
    flagged: bool
    change: ChangeKind | None = Field(description="How the final answer differs from the first choice, if it does")
    seconds: int | None


class TimeStats(BaseModel):
    mean_s: float | None
    median_s: float | None
    target_s: int | None = Field(description="exam_rules.modes[mode].seconds_per_question")
    total_s: int


class ChangeStats(BaseModel):
    changed: int
    right_to_wrong: int
    wrong_to_right: int
    wrong_to_wrong: int


class FlagStats(BaseModel):
    flagged: int
    flagged_correct: int


class TopicStat(BaseModel):
    id: str
    label: str
    kind: Literal["system", "discipline", "competency", "clinical_science"]
    total: int
    correct: int
    percent: float
    weak: bool = Field(description="Below the weak threshold (60 %)")


class WeightOverlay(BaseModel):
    id: str
    label: str
    weight_min: float | None = Field(description="Official share of the exam, percent (low end)")
    weight_max: float | None
    share: float = Field(description="This block's share of questions in the system, percent")
    total: int
    correct: int
    percent: float | None = Field(description="Accuracy in the system; null when the block had none")


class TrendPoint(BaseModel):
    block_id: uuid.UUID
    finished_at: dt.datetime
    score: float
    mode: Literal["timed", "exam"]
    current: bool


InsightKind = Literal[
    "weakest_topic",
    "strongest_topic",
    "pace_slow",
    "pace_fast",
    "changes_hurt",
    "changes_helped",
    "flags_right",
    "underweighted",
    "trend_up",
    "trend_down",
]


class Insight(BaseModel):
    """One "What Naadi sees" sentence, as a ui_copy template key (`results.insight.<kind>`) plus its values."""

    kind: InsightKind
    vars: dict[str, str | int | float]


class BlockResults(BaseModel):
    block_id: uuid.UUID
    exam: str
    mode: Literal["timed", "exam"]
    mock: str | None
    finished_at: dt.datetime
    total: int
    correct: int
    omitted: int
    percent: float
    time: TimeStats
    changes: ChangeStats
    flags: FlagStats
    questions: list[ResultQuestion]
    systems: list[TopicStat]
    disciplines: list[TopicStat] = Field(description="Disciplines (Step 1) or clinical sciences (Step 2 CK)")
    competencies: list[TopicStat]
    weights: list[WeightOverlay]
    trend: list[TrendPoint] = Field(description="The caller's last 10 finished timed/exam blocks, oldest first")
    insights: list[Insight]
