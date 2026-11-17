"""Models of the admin side of the YouTube queue (OpenAPI -> zod). Codes, never English sentences."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

CandidateStatus = Literal["pending", "approved", "rejected", "stale"]
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}$"


class CandidateOut(BaseModel):
    candidate_id: str
    media_id: str
    youtube_id: str
    title: str | None = Field(description="null once the metadata was dropped (stale or rejected)")
    channel_id: str | None
    duration_s: int | None
    score: float
    matched_terms: list[str]
    section_id: str | None
    section_heading: str | None
    chapter_id: str | None
    taxonomy_ids: list[str]
    status: CandidateStatus
    created_at: dt.datetime


class CandidatesOut(BaseModel):
    items: list[CandidateOut]
    pending: int


class ApproveIn(BaseModel):
    section_id: str | None = Field(default=None, pattern=ID_PATTERN, description="Default: the candidate's section")
    start_s: int | None = Field(default=None, ge=0, le=86_400 * 2)
    end_s: int | None = Field(default=None, ge=1, le=86_400 * 2)


class ApproveOut(BaseModel):
    media_id: str
    section_id: str
    start_s: int | None
    end_s: int | None


class AddIn(BaseModel):
    url: str = Field(min_length=11, max_length=300, description="A YouTube link or the 11-character video id")
    section_id: str = Field(pattern=ID_PATTERN)
    approve: bool = False
    start_s: int | None = Field(default=None, ge=0, le=86_400 * 2)
    end_s: int | None = Field(default=None, ge=1, le=86_400 * 2)


class RunReportOut(BaseModel):
    channels: int
    listed: int
    new_videos: int
    candidates_added: int
    refreshed: int
    dropped: int
    quota_used: int
    stopped_early: bool
