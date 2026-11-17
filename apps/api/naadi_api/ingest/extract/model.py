"""The extracted document: what every extractor returns and every placement reads.

It crosses a process boundary (the extraction sandbox writes it as JSON), so it is validated here with hard limits
before the pipeline trusts any of it. Text is the student's own material: it is stored (gzip, server-only
`ingest_extracts`) and placed, never logged.
"""

from __future__ import annotations

import gzip
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_TEXT = 4_000_000  # characters of text in one document
MAX_SECTION_TEXT = 400_000
MAX_SECTIONS = 2000
MAX_FIGURES = 80
MAX_TABLES = 200
MAX_TABLE_ROWS = 5000
MAX_QA = 5000
MAX_MCQ = 2000

TextSource = Literal["text", "local_ocr", "vision", "transcript", "markup", "none"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page(_M):
    number: int = Field(ge=1)
    text: str = Field(default="", max_length=MAX_SECTION_TEXT)
    source: TextSource = "text"
    scanned: bool = False  # under 50 characters of embedded text: an image of a page


class Section(_M):
    heading: str = Field(default="", max_length=300)
    text: str = Field(default="", max_length=MAX_SECTION_TEXT)
    level: int = Field(default=1, ge=1, le=6)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    start_s: float | None = Field(default=None, ge=0)  # transcripts


class Figure(_M):
    key: str = Field(pattern=r"^[a-z0-9_-]{1,40}$")  # file name of the WebP the sandbox wrote (fig-3)
    page: int | None = Field(default=None, ge=1)
    width: int = Field(ge=1, le=20_000)
    height: int = Field(ge=1, le=20_000)
    caption: str = Field(default="", max_length=500)
    bytes: int = Field(default=0, ge=0)
    storage_path: str | None = None  # set by the pipeline once uploaded (owned, `user-uploads/{uid}/{item}/…`)


class Table(_M):
    rows: list[list[str]] = Field(default_factory=list, max_length=MAX_TABLE_ROWS)
    page: int | None = Field(default=None, ge=1)


class QA(_M):
    front: str = Field(max_length=4000)
    back: str = Field(max_length=8000)


class MCQ(_M):
    stem: str = Field(max_length=8000)
    options: list[str] = Field(min_length=2, max_length=10)
    answer: str | None = Field(default=None, pattern=r"^[A-J]$")
    explanation: str = Field(default="", max_length=8000)


class Media(_M):
    duration_s: float | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    has_audio: bool = False
    has_video: bool = False
    thumbnail: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,40}$")


class ExtractedDoc(_M):
    kind: str
    title: str = Field(default="", max_length=300)
    pages: list[Page] = Field(default_factory=list, max_length=10_000)
    sections: list[Section] = Field(default_factory=list, max_length=MAX_SECTIONS)
    figures: list[Figure] = Field(default_factory=list, max_length=MAX_FIGURES)
    tables: list[Table] = Field(default_factory=list, max_length=MAX_TABLES)
    qa_pairs: list[QA] = Field(default_factory=list, max_length=MAX_QA)
    mcqs: list[MCQ] = Field(default_factory=list, max_length=MAX_MCQ)
    media: Media = Field(default_factory=Media)
    url: str | None = Field(default=None, max_length=2048)
    page_count: int = Field(default=0, ge=0)
    handwriting_likely: bool = False
    image_key: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,40}$")  # a single image upload (WebP)

    # ── derived ──
    def full_text(self) -> str:
        if self.sections:
            parts = [f"{s.heading}\n{s.text}" if s.heading else s.text for s in self.sections]
        else:
            parts = [p.text for p in self.pages]
        return "\n\n".join(p for p in parts if p)[:MAX_TEXT]

    @property
    def word_count(self) -> int:
        return len(self.full_text().split())

    def scanned_pages(self) -> list[int]:
        return [p.number for p in self.pages if p.scanned]

    def dumps(self) -> bytes:
        return gzip.compress(self.model_dump_json(exclude_defaults=True).encode("utf-8"), compresslevel=6)

    @classmethod
    def loads(cls, data: bytes) -> ExtractedDoc:
        raw = gzip.decompress(data)
        if len(raw) > 64 * 1024 * 1024:
            raise ValueError("extract too large")
        return cls.model_validate(json.loads(raw))


__all__ = ["MCQ", "QA", "ExtractedDoc", "Figure", "Media", "Page", "Section", "Table"]
