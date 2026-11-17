"""API models for ingest and the Library (pydantic -> OpenAPI -> zod in packages/shared).

Every field is structured (codes, counts, ids, titles from the student's own document); the client words them
with `ui_copy` (`ingest.*`, `library.*`). No answer of any question appears in a preview.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ItemStatus = Literal["received", "extracting", "needs_confirmation", "placing", "ready", "failed", "vaulted"]
Stage = Literal["upload", "receive", "vault", "extract", "understand", "propose", "confirm", "place", "done", "vaulted"]
InputKind = Literal[
    "pdf",
    "docx",
    "pptx",
    "xlsx",
    "csv",
    "txt",
    "md",
    "html",
    "epub",
    "rtf",
    "image",
    "audio",
    "video",
    "link",
    "anki",
    "zip",
    "paste",
]
LIST_LIMIT_MAX = 100


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UploadCreate(_In):
    name: str = Field(min_length=1, max_length=255, description="The file name as the student chose it")
    size: int = Field(ge=1, description="Bytes")
    content_type: str = Field(min_length=3, max_length=120, description="The MIME type the browser will upload with")
    batch_id: uuid.UUID | None = Field(default=None, description="Files dropped together share one batch")


class UploadBatchCreate(_In):
    """Many files dropped together (one request: S22's heavy-route limit is 30/min)."""

    files: list[UploadCreate] = Field(min_length=1, max_length=200)


class UploadTicket(BaseModel):
    item_id: uuid.UUID
    upload_id: uuid.UUID
    bucket: str
    path: str = Field(description="Object path inside the bucket ({user_id}/{item_id}/original)")
    token: str = Field(description="Signed-upload token for supabase-js uploadToSignedUrl")
    expires_in: int


class UploadBatchOut(BaseModel):
    batch_id: uuid.UUID
    tickets: list[UploadTicket]


class UrlCreate(_In):
    url: str = Field(min_length=8, max_length=2048, pattern=r"^https://")
    batch_id: uuid.UUID | None = None


class PasteCreate(_In):
    text: str = Field(min_length=1, max_length=2_000_000)
    title: str | None = Field(default=None, max_length=200)


class PlacementChoice(_In):
    option_id: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_]{1,39}:\d{1,2}$")


class ConfirmBody(_In):
    """Exactly one: the placement chosen on the confirm card, or the answer to the Vault prompt."""

    placement: PlacementChoice | None = None
    vault: Literal["vault", "continue", "discard"] | None = None


class BatchConfirmItem(_In):
    item_id: uuid.UUID
    option_id: str = Field(min_length=3, max_length=80, pattern=r"^[a-z][a-z0-9_]{1,39}:\d{1,2}$")


class BatchConfirm(_In):
    items: list[BatchConfirmItem] = Field(min_length=1, max_length=200)


class OptionOut(BaseModel):
    option_id: str
    kind: str = Field(description="Placement kind (ui_copy ingest.placement.<kind>)")
    recommended: bool
    preview: dict[str, Any] = Field(description="Counts, titles and target ids; never an answer")


class GateOut(BaseModel):
    signals: list[str] = Field(description="Sensitivity signal codes (ingest.signal.<code>)")
    doc_type: str | None


class PlacedOut(BaseModel):
    kind: str
    chapter_id: str | None = None
    rows: int
    can_undo: bool


class Counts(BaseModel):
    model_config = ConfigDict(extra="ignore")
    pages: int | None = None
    words: int | None = None
    sections: int | None = None
    figures: int | None = None
    qa_pairs: int | None = None
    mcqs: int | None = None
    scanned: int | None = None
    duration_s: float | None = None


class IngestItemOut(BaseModel):
    id: uuid.UUID
    status: ItemStatus
    stage: Stage
    error: str | None = Field(description="Reason code when failed (ingest.error.<code>)")
    progress: int = Field(ge=0, le=100)
    input_kind: InputKind | None
    name: str | None = Field(description="File name, page title or link host")
    url: str | None
    upload_id: uuid.UUID | None
    bytes: int | None
    pages: int | None
    batch_id: uuid.UUID | None
    duplicate_of: uuid.UUID | None
    duplicate: Literal["exact", "near"] | None
    chapter_id: str | None = Field(description="The private chapter this item became (or duplicates)")
    gate: GateOut | None = Field(description="Set while the Vault prompt waits")
    options: list[OptionOut] = Field(description="The confirm card's placements, best first")
    placed: PlacedOut | None
    counts: Counts | None
    bundle_files: int | None = None
    retryable: bool
    created_at: datetime
    updated_at: datetime


class IngestItemList(BaseModel):
    items: list[IngestItemOut]
    next_before: datetime | None


class StorageOut(BaseModel):
    used_bytes: int
    limit_bytes: int
    remaining_bytes: int


class LibraryChapter(BaseModel):
    id: str
    kind: Literal["user_upload", "user_kb"]
    title: str
    sections: int
    item_id: uuid.UUID | None
    updated_at: datetime


class LibraryKb(BaseModel):
    id: uuid.UUID
    kind: Literal["url", "file", "paste"]
    title: str | None
    url: str | None
    status: Literal["processing", "ready", "failed", "needs_confirmation"]
    chapter_id: str | None
    item_id: uuid.UUID | None
    created_at: datetime


class LibraryLimits(BaseModel):
    max_file_mb: int
    max_paste_chars: int
    enabled: bool
    content_types: list[str] = Field(
        description="Content types an upload may declare (the drop zone refuses others before uploading)"
    )


class LibraryOut(BaseModel):
    items: list[IngestItemOut] = Field(description="Files, links and pastes, newest first (bundles' members included)")
    chapters: list[LibraryChapter]
    kb: list[LibraryKb]
    storage: StorageOut
    accepts: list[str] = Field(description="Extra input kinds a registered placement takes (e.g. anki once S20 lands)")
    limits: LibraryLimits


class FileOut(BaseModel):
    upload_id: uuid.UUID
    kind: InputKind
    name: str | None
    url: str = Field(description="Signed URL, valid for expires_in seconds")
    expires_in: int
    pages: int | None
    mime: str | None


class PageTextOut(BaseModel):
    upload_id: uuid.UUID
    page: Annotated[int, Field(ge=1)]
    pages: int
    text: str


__all__ = [
    "BatchConfirm",
    "ConfirmBody",
    "FileOut",
    "IngestItemList",
    "IngestItemOut",
    "LibraryOut",
    "PageTextOut",
    "PasteCreate",
    "UploadCreate",
    "UploadTicket",
    "UrlCreate",
]
