"""Request and response models of the Cards API (OpenAPI -> zod in @naadi/shared)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .docs import MAX_FACE_CHARS, normalize_text

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}$"
DECK_MAX = 60
Origin = Literal["seed", "user", "assistant", "anki_import"]


class FsrsSchedulerConfig(BaseModel):
    """The scheduler parameters the server uses (app_config `fsrs`), so a client preview is the server result."""

    weights: list[float] = Field(min_length=21, max_length=21)
    desired_retention: float
    learning_steps_s: list[int]
    relearning_steps_s: list[int]
    maximum_interval: int
    enable_fuzz: bool


class CardSource(BaseModel):
    """Where the card came from, for "Open source page". Only ids the student may open."""

    kind: Literal["section", "question"]
    id: str
    chapter_id: str | None = Field(description="The section's chapter (section links)")


class CardSchedule(BaseModel):
    """The student's scheduling state of the card; `state` 0 = new (never reviewed)."""

    state: int = Field(ge=0, le=3, description="0 new, 1 learning, 2 review, 3 relearning")
    step: int | None
    stability: float | None
    difficulty: float | None
    due: dt.datetime | None = Field(description="null for a new card (due now)")
    last_review: dt.datetime | None
    reps: int
    lapses: int


class CardOut(BaseModel):
    id: uuid.UUID
    deck: str
    origin: Origin
    editable: bool = Field(description="The student's own card (origin user): may be edited and deleted")
    front: dict[str, Any] = Field(description="TipTap document for ContentRenderer")
    back: dict[str, Any] = Field(description="TipTap document for ContentRenderer")
    front_text: str = Field(description="The face as plain text (for editing)")
    back_text: str
    source: CardSource | None
    taxonomy_ids: list[str]
    created_at: dt.datetime
    updated_at: dt.datetime
    schedule: CardSchedule


class DueResponse(BaseModel):
    now: dt.datetime = Field(description="Server time the list was computed at")
    cards: list[CardOut] = Field(description="Due reviews (oldest due first), then new cards (oldest first)")
    due_count: int = Field(description="Cards due now, of which `cards` holds at most `limit`")
    next_due: dt.datetime | None = Field(description="The earliest due time after now, when nothing else is due")
    scheduler: FsrsSchedulerConfig


def _face(v: str) -> str:
    t = normalize_text(v)
    if not t:
        raise ValueError("empty")
    if len(t) > MAX_FACE_CHARS:
        raise ValueError("too long")
    return t


class CardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    front_text: str = Field(max_length=MAX_FACE_CHARS * 2)
    back_text: str = Field(max_length=MAX_FACE_CHARS * 2)
    deck: str | None = Field(default=None, max_length=DECK_MAX, description="Default 'My cards'")
    question_id: str | None = Field(
        default=None, pattern=ID_PATTERN, description="Link to a question; needs your graded attempt on it"
    )
    section_id: str | None = Field(default=None, pattern=ID_PATTERN, description="Link to a section you can read")

    @field_validator("front_text", "back_text")
    @classmethod
    def _faces(cls, v: str) -> str:
        return _face(v)

    @field_validator("deck")
    @classmethod
    def _deck(cls, v: str | None) -> str | None:
        if v is None:
            return None
        t = " ".join(v.split())
        return t or None


class CardPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    front_text: str | None = Field(default=None, max_length=MAX_FACE_CHARS * 2)
    back_text: str | None = Field(default=None, max_length=MAX_FACE_CHARS * 2)
    deck: str | None = Field(default=None, max_length=DECK_MAX)

    @field_validator("front_text", "back_text")
    @classmethod
    def _faces(cls, v: str | None) -> str | None:
        return None if v is None else _face(v)

    @field_validator("deck")
    @classmethod
    def _deck(cls, v: str | None) -> str | None:
        if v is None:
            return None
        t = " ".join(v.split())
        if not t:
            raise ValueError("empty")
        return t


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_review_id: uuid.UUID = Field(description="Idempotency key: the same id returns the first result")
    rating: Literal[1, 2, 3, 4] = Field(description="1 Again, 2 Hard, 3 Good, 4 Easy")
    duration_ms: int | None = Field(default=None, ge=0, le=3_600_000)
    reviewed_at: dt.datetime | None = Field(
        default=None, description="When the student rated (offline reviews); default now, never in the future"
    )

    @field_validator("reviewed_at")
    @classmethod
    def _aware(cls, v: dt.datetime | None) -> dt.datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("needs a time zone offset")
        return v


class ReviewOut(BaseModel):
    card_id: uuid.UUID
    client_review_id: uuid.UUID
    replayed: bool = Field(description="true when this client_review_id was already applied")
    rating: int
    reviewed_at: dt.datetime
    scheduled_days: int
    schedule: CardSchedule


class RetentionOut(BaseModel):
    reviews: int = Field(description="Reviews of cards in the review state, last 30 days")
    recalled: int = Field(description="Of those, rated Hard, Good or Easy")
    rate: float | None = Field(description="recalled / reviews, null with no reviews")


class CardStats(BaseModel):
    zone: str = Field(description="The IANA time zone the day counts use")
    total: int
    new: int = Field(description="Cards never reviewed")
    due_now: int
    due_today: int = Field(description="Due before the end of today (local), overdue and new included")
    due_tomorrow: int = Field(description="Falling due during tomorrow (local)")
    reviewed_today: int
    streak_days: int
    retention: RetentionOut
    next_due: dt.datetime | None
