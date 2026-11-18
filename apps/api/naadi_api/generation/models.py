"""Pydantic shapes for the generation API (exported to OpenAPI → zod). Structured fields only, no sentences."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

GeneratedKind = Literal["image", "video"]
GeneratedStatus = Literal["queued", "generating", "ready", "failed", "paused"]
GenerationState = Literal["not_configured", "disabled", "ready", "paused"]
Stage = Literal["queued", "generating", "saving"]

LIST_LIMIT_DEFAULT = 24
LIST_LIMIT_MAX = 60


class GeneratedItem(BaseModel):
    """One generated image or clip. Always AI-generated: the client shows the label chip, alt text and caption."""

    id: uuid.UUID
    kind: GeneratedKind
    status: GeneratedStatus
    title: str | None = Field(default=None, max_length=200)
    prompt: str = Field(description="The scrubbed prompt that was sent (never the raw input)")
    model: str
    width: int | None = None
    height: int | None = None
    duration_s: float | None = None
    url: str | None = Field(default=None, description="Signed URL (10 minutes) for a ready item; None otherwise")
    ai_generated: Literal[True] = True
    error: str | None = Field(default=None, description="Stable error code of a failed or paused item")
    paused_until: datetime | None = Field(default=None, description="When a quota pause ends")
    published: bool = Field(description="True once an admin approved it as a shared chapter figure")
    created_at: datetime


class GeneratedList(BaseModel):
    items: list[GeneratedItem]
    next_before: datetime | None = Field(default=None, description="Pass as `before` for the next page")


class UsageOut(BaseModel):
    used: int = Field(ge=0)
    cap: int = Field(ge=0)
    remaining: int = Field(ge=0)


class GenerationStatusOut(BaseModel):
    state: GenerationState
    resets_at: datetime | None = Field(default=None, description="End of the pause, or the next daily-cap reset")
    image: UsageOut
    video: UsageOut
    max_video_seconds: int
    video_enabled: bool


class StubRequest(BaseModel):
    """Non-production stub only: run a generation against the in-process fake."""

    kind: GeneratedKind = "image"
    prompt: str = Field(min_length=1, max_length=600)
    seconds: int = Field(default=3, ge=1, le=5)


class ProgressEvent(BaseModel):
    """Custom stream event `progress` (S14 forwards it to the client)."""

    stage: Stage
    media_id: uuid.UUID
    queue_position: int | None = None
