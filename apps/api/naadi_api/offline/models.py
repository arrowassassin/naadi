"""The offline week manifest (segment 18): what "Download this week" stores on the device.

Every field reuses the online endpoint's own model and serializer (ChapterDetail, ChapterPositions, QuestionView,
DueResponse, NoteList, NotebookDetail, InkLayerOut, PlanItemOut), so the web's per-screen offline fallbacks store
them as they are: the device holds exactly what it would have fetched online and nothing more, so no answer,
explanation, why-not or objective of a question the student has not answered. `parts` carries the size of each
part's JSON (UTF-8 bytes) and its item count, for the size preview; media files are listed by URL and downloaded
separately.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from ..cards.models import DueResponse
from ..courses import ChapterDetail, ChapterPositions
from ..ink.models import InkLayerOut
from ..notes.models import NotebookDetail, NoteList
from ..plan.models import PlanDayMeta, PlanItemOut
from ..qbank.models import BlockDetail, QuestionView

PartName = Literal["plan", "chapters", "figures", "videos", "questions", "due", "notes", "notebooks", "ink", "pathway"]


class OfflineDay(BaseModel):
    date: dt.date
    plan_day: PlanDayMeta | None = Field(description="The active plan's day on this date, if any")
    items: list[PlanItemOut] = Field(description="Every plan item on this date (generator, user, assistant)")


class OfflinePlan(BaseModel):
    plan_id: uuid.UUID | None = Field(description="The active plan; null when the student has none")
    days: list[OfflineDay] = Field(description="The 7 dates from `start`, in order")


class SectionRevision(BaseModel):
    chapter_id: str
    section_id: str
    revision: str = Field(description="md5 of the section body (the ink and notes content revision)")
    updated_at: dt.datetime


class OfflineFigure(BaseModel):
    media_id: str
    url: str = Field(description="Public URL in the content-public bucket")


class OfflineVideo(BaseModel):
    media_id: str
    url: str
    height: int = Field(description="Pixels; never above 1080")


class SkippedMedia(BaseModel):
    media_id: str
    reason: Literal["youtube", "over_1080p", "size_unknown", "no_url", "not_public", "not_offline"] = Field(
        description="youtube: never cached (IFrame player only); over_1080p / size_unknown: own videos are taken "
        "only at a known height of at most 1080; no_url: not servable; not_public: a figure outside content-public "
        "(signed URLs expire); not_offline: audio, PDF or link"
    )


class OfflineQuestion(BaseModel):
    block_id: uuid.UUID
    n: int = Field(description="1-based position in the block")
    view: QuestionView = Field(
        description="As GET /api/blocks/{block_id}/questions/{n}: an explanation only after a graded tutor answer, "
        "and none at all while a timed/exam session is active"
    )


class OfflineInkLayer(BaseModel):
    surface_type: Literal["section", "question", "notebook_page"]
    surface_id: str
    layer: InkLayerOut


class PathwayTaskStatus(BaseModel):
    task_id: str
    status: Literal["todo", "doing", "done", "na"]
    due: dt.date | None


class PartSize(BaseModel):
    part: PartName
    bytes: int = Field(description="Length of the part's JSON in bytes (UTF-8, compact)")
    count: int = Field(description="Items in the part (chapters, figures, questions, cards, notes, layers, …)")


class OfflineWeek(BaseModel):
    start: dt.date
    end: dt.date = Field(description="The last of the 7 dates (inclusive)")
    timezone: str = Field(description="The profile's time zone the dates are in (UTC when unset)")
    generated_at: dt.datetime
    server_now_ms: int = Field(description="Server clock (ms) for the device's clock offset")
    plan: OfflinePlan
    chapters: list[ChapterDetail] = Field(description="Chapters the week's plan reads, as GET /api/chapters/{id}")
    section_revisions: list[SectionRevision]
    positions: list[ChapterPositions] = Field(description="As GET /api/reading-position, one per chapter")
    figures: list[OfflineFigure]
    videos: list[OfflineVideo] = Field(description="Own videos at most 1080p; empty when there are none")
    skipped_media: list[SkippedMedia]
    blocks: list[BlockDetail] = Field(
        description="The student's unfinished tutor blocks (newest first), as GET /api/blocks/{id}"
    )
    questions: list[OfflineQuestion]
    exam_active: bool = Field(description="A timed/exam session is running: no explanation is included anywhere")
    due_by: dt.datetime = Field(description="`due` holds cards due up to the end of the week (student's time zone)")
    due: DueResponse = Field(description="As GET /api/cards/due (attempt gate applied); `now` is `due_by`")
    notes: NoteList = Field(description="As GET /api/notes: notes and highlights on the week's sections and questions")
    notebooks: list[NotebookDetail] = Field(description="As GET /api/notebooks/{id}: every notebook with its pages")
    ink: list[OfflineInkLayer] = Field(description="The student's ink on the week's sections, questions and pages")
    pathway: list[PathwayTaskStatus]
    parts: list[PartSize]
    bytes: int = Field(description="Sum of the parts' bytes")


__all__ = [
    "OfflineDay",
    "OfflineFigure",
    "OfflineInkLayer",
    "OfflinePlan",
    "OfflineQuestion",
    "OfflineVideo",
    "OfflineWeek",
    "PartName",
    "PartSize",
    "PathwayTaskStatus",
    "SectionRevision",
    "SkippedMedia",
]
