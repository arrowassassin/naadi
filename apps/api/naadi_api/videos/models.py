"""Models of the Videos API (OpenAPI -> zod). Codes, never English sentences."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,127}$"
Step = Literal["step1", "step2ck"]
Style = Literal["sketch", "whiteboard", "animation", "lecture"]
Provider = Literal["youtube", "commons"]
Coverage = Literal["topic", "chapter", "gap"]


class VideoTopicRef(BaseModel):
    id: str
    title: str
    chapter: str | None


class LicenceOut(BaseModel):
    license_short_name: str | None = None
    license_url: str | None = None
    artist: str | None = None


class VideoOut(BaseModel):
    media_id: str
    provider: Provider
    youtube_id: str | None = Field(description="Set for provider youtube (played through the official player)")
    file_url: str | None = Field(description="Set for provider commons (an open-licence file served by its host)")
    page_url: str | None = Field(description="The file's own page (commons)")
    licence: LicenceOut | None
    title: str | None
    channel_id: str | None
    channel_name: str | None
    duration_s: int | None
    style: Style
    subject: str
    step: Step
    core: bool
    topics: list[VideoTopicRef] = Field(description="The topics this video teaches directly")
    attribution: str | None
    watched: bool


class VideosOut(BaseModel):
    items: list[VideoOut]
    next: str | None = Field(description="Cursor for the next page, null at the end")
    total: int


class SubjectCounts(BaseModel):
    step: Step
    subject: str
    videos: int
    topics: int
    topics_direct: int = Field(description="Topics with a video that teaches them directly")
    topics_chapter: int = Field(description="Topics without one but with a related video from the same chapter")
    watched: int


class ChannelCount(BaseModel):
    id: str = Field(description="The channel id, or 'commons' for Wikimedia Commons files")
    name: str
    videos: int


class VideosOverview(BaseModel):
    subjects: list[SubjectCounts] = Field(description="Ordered: step 1 then step 2 CK, subjects in syllabus order")
    channels: list[ChannelCount]


class VideoTopicOut(BaseModel):
    id: str
    title: str
    subject: str
    unit: str | None
    chapter: str | None
    step: Step
    core: bool
    media_ids: list[str] = Field(description="Videos that teach the topic directly")
    chapter_media_ids: list[str] = Field(description="Related videos from the same subject, step and chapter")
    coverage: Coverage


class VideoTopicsOut(BaseModel):
    items: list[VideoTopicOut]
