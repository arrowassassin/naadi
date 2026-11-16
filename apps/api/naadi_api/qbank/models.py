"""Request and response models of the Qbank API (OpenAPI -> zod in @naadi/shared)."""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}$"
EXAM_PATTERN = r"^[a-z0-9]{2,16}$"
TAXONOMY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,127}$"
OPTION_PATTERN = r"^[A-J]$"

Mode = Literal["tutor", "timed", "exam"]
Status = Literal["all", "unused", "incorrect", "flagged", "correct"]
Difficulty = Literal["easy", "medium", "hard"]

# ── Pool ──


class PoolFacet(BaseModel):
    id: str
    label: str
    count: int = Field(description="Questions that would match if this were chosen, given the other filters")


class BlockLimits(BaseModel):
    min_questions: int
    max_questions: int
    default_questions: int


class ExamChoice(BaseModel):
    id: str
    label: str


class ModeChoice(BaseModel):
    id: Mode
    available: bool = Field(description="false while the screens for the mode have not shipped (S09)")


class PoolResponse(BaseModel):
    exam: str
    exams: list[ExamChoice]
    limits: BlockLimits
    modes: list[ModeChoice]
    total: int = Field(description="Questions matching every filter")
    systems: list[PoolFacet]
    disciplines: list[PoolFacet] = Field(description="Disciplines (Step 1) or clinical sciences (Step 2 CK)")
    status: dict[str, int] = Field(description="Counts per status filter: all, unused, incorrect, flagged, correct")
    difficulty: dict[str, int] = Field(description="Counts per difficulty: easy, medium, hard, all")


# ── Blocks ──


class BlockCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exam: str = Field(pattern=EXAM_PATTERN)
    systems: list[str] = Field(default_factory=list, max_length=40)
    disciplines: list[str] = Field(default_factory=list, max_length=40)
    status: Status = "all"
    difficulty: Difficulty | None = None
    count: int = Field(ge=1, le=80, description="Bounded by app_config.exam_rules.block")
    mode: Mode = "tutor"
    seed: int | None = Field(default=None, ge=0, le=2**62, description="Same seed + filters give the same questions")

    @field_validator("systems", "disciplines")
    @classmethod
    def _ids(cls, v: list[str]) -> list[str]:
        if any(not re.match(TAXONOMY_PATTERN, i) for i in v):
            raise ValueError("invalid taxonomy id")
        return sorted(set(v))


class WeakSpotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exam: str = Field(pattern=EXAM_PATTERN)
    count: int | None = Field(default=None, ge=1, le=80, description="Default 20, at most the questions available")
    mode: Mode = "tutor"
    seed: int | None = Field(default=None, ge=0, le=2**62)


class SingleBlockCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=ID_PATTERN)


class BlockConfig(BaseModel):
    exam: str
    mode: Mode
    status: Status
    difficulty: Difficulty | None
    systems: list[str]
    disciplines: list[str]
    seed: int
    weak_spot: bool


class BlockSummary(BaseModel):
    id: uuid.UUID
    created_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    config: BlockConfig
    total: int
    answered: int
    correct: int | None = Field(description="Right answers so far; only in tutor mode or once the block is finished")
    score: float | None = Field(description="Percent correct, set when the block finishes")


class BlockQuestionState(BaseModel):
    n: int = Field(description="1-based position in the block")
    answered: bool
    flagged: bool
    correct: bool | None = Field(description="Only in tutor mode or once the block is finished")


class BlockDetail(BlockSummary):
    questions: list[BlockQuestionState]
    next_n: int = Field(description="First unanswered question, else 1")


class WeakCellOut(BaseModel):
    id: str
    label: str
    attempts: int
    correct: int


class WeakSpotBlock(BaseModel):
    block: BlockDetail
    cells: list[WeakCellOut] = Field(description="The weakest topics the block was built from")


class BlockList(BaseModel):
    blocks: list[BlockSummary]


# ── Questions and attempts ──


class QuestionOption(BaseModel):
    label: str
    html: str


class QuestionBody(BaseModel):
    """A question as the client may see it before answering: never the answer or any explanation."""

    id: str
    stem_html: str
    options: list[QuestionOption]
    difficulty: Difficulty | None
    topics: list[str] = Field(description="Labels of the question's systems and disciplines")


class LessonLink(BaseModel):
    chapter_id: str
    title: str


class Explanation(BaseModel):
    answer: str = Field(description="The correct option label")
    answer_html: str | None
    explanation_html: str | None
    why_not_html: str | None
    educational_objective: str | None


class AttemptState(BaseModel):
    chosen: str
    seconds: int | None
    correct: bool | None = Field(description="Only in tutor mode (with the explanation)")


class QuestionView(BaseModel):
    block_id: uuid.UUID
    n: int
    total: int
    mode: Mode
    question: QuestionBody
    lesson: LessonLink | None
    flagged: bool
    attempt: AttemptState | None
    explanation: Explanation | None = Field(
        description="Present only after the student's graded attempt, and only in tutor mode"
    )


class AttemptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(pattern=ID_PATTERN)
    chosen: str = Field(pattern=OPTION_PATTERN)
    seconds: int = Field(default=0, ge=0, le=7200)
    flagged: bool = False


class AttemptOut(BaseModel):
    question_id: str
    n: int
    chosen: str
    correct: bool | None = Field(description="Only in tutor mode (with the explanation)")
    flagged: bool
    seconds: int | None
    explanation: Explanation | None
    answered: int = Field(description="Questions answered in the block, this one included")
    total: int
    finished: bool


class FlagIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flagged: bool


class FlagOut(BaseModel):
    question_id: str
    n: int
    flagged: bool


# ── Lab values ──


class LabRowOut(BaseModel):
    analyte: str
    range: str
    si: str | None


class LabGroupOut(BaseModel):
    id: str
    label: str
    rows: list[LabRowOut]


class SourceRef(BaseModel):
    id: str
    name: str
    license: str
    url: str | None


class LabValuesOut(BaseModel):
    groups: list[LabGroupOut]
    source: SourceRef = Field(description="Where this table comes from (Naadi original)")
    official: SourceRef = Field(description="The official table, linked to rather than copied")
