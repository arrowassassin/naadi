"""Notes API models (segment 13): notes, highlights, notebook pages and notebooks.

Pydantic is the contract source: `pnpm gen:contracts` exports these to packages/shared (OpenAPI -> zod). Responses
carry structured fields only; the web words them with ui_copy (`notes.*`, `notebooks.*`, `highlights.*`).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

NoteKind = Literal["note", "highlight", "page"]
AnchorType = Literal["section", "question", "video", "pdf", "free"]
HighlightColor = Literal["lime", "violet", "amber"]
Paper = Literal["ruled", "dotted", "grid", "cornell", "blank"]
Canvas = Literal["paper", "whiteboard"]
# Colour token names of packages/ui/src/tokens.css (`--color-<name>`); migration 0017 checks the same set.
NotebookCover = Literal["lime", "violet", "amber", "paper", "surface-2", "ink-violet"]

MAX_BODY_BYTES = 1_000_000
MAX_REQUEST_BYTES = MAX_BODY_BYTES + 64 * 1024
MAX_TITLE = 200
MAX_QUOTE = 2000
MAX_CONTEXT = 64
MAX_NOTES_PER_USER = 50_000
MAX_NOTEBOOKS_PER_USER = 500
MAX_PAGES_PER_NOTEBOOK = 1000
LIST_LIMIT_DEFAULT = 50
LIST_LIMIT_MAX = 200
ANCHOR_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:\-]{0,199}$"

Title = Annotated[str, Field(max_length=MAX_TITLE)]
AnchorId = Annotated[str, Field(pattern=ANCHOR_ID_PATTERN)]
Position = Annotated[int, Field(ge=0, le=100_000_000)]
Sort = Annotated[int, Field(ge=0, le=100_000)]

_IN = ConfigDict(extra="forbid", populate_by_name=True)


class VideoPos(BaseModel):
    """Where a video note sits: seconds into the video (`anchor_type 'video'`, segment 20's player)."""

    model_config = _IN
    t: Annotated[int, Field(ge=0, le=86_400 * 2, description="Seconds from the start of the video")]


class AnchorPos(BaseModel):
    """Where a highlight sits. Section anchors: ProseMirror positions in the section body. Question anchors: UTF-16
    offsets into the text of the explanation. `quote` (+ `prefix`/`suffix`) re-anchors it when positions drift;
    `revision` is the web's fingerprint of the content it was made on. Stored as sent."""

    model_config = _IN
    from_: Position = Field(alias="from")
    to: Position
    quote: str = Field(min_length=1, max_length=MAX_QUOTE)
    prefix: str = Field(default="", max_length=MAX_CONTEXT)
    suffix: str = Field(default="", max_length=MAX_CONTEXT)
    revision: str | None = Field(default=None, max_length=128)
    detached: bool | None = Field(
        default=None, description="Set by the web when the quote can no longer be found (listed as Detached)"
    )

    @model_validator(mode="after")
    def _range(self) -> Self:
        if self.to < self.from_:
            raise ValueError("`to` must not be before `from`")
        return self


AnyPos = AnchorPos | VideoPos


def parse_pos(raw: Any) -> AnyPos | None:
    """A stored `anchor_pos` (jsonb): `{t}` for a video note, else the text-range shape."""
    if not raw:
        return None
    return VideoPos.model_validate(raw) if "t" in raw and "from" not in raw else AnchorPos.model_validate(raw)


class NoteSource(BaseModel):
    """What a note's anchor points at, for source chips and links (structured; the web words it). Only what the
    caller may see: a section of a chapter they can read, a question they attempted (never stem or answer)."""

    type: Literal["section", "question", "video", "pdf"]
    chapter_id: str | None = None
    chapter_title: str | None = None
    section_id: str | None = None
    section_heading: str | None = None
    section_revision: str | None = Field(
        default=None, description="md5 of the section's current body (the ink API's content revision)"
    )
    question_id: str | None = None
    block_id: str | None = Field(default=None, description="Block of the caller's latest graded attempt")
    question_number: int | None = Field(default=None, description="1-based position of the question in that block")
    media_id: str | None = None
    upload_id: str | None = None
    title: str | None = Field(default=None, description="Video title or PDF file name")


class NoteOut(BaseModel):
    id: uuid.UUID
    kind: NoteKind
    title: str | None
    notebook_id: uuid.UUID | None
    anchor_type: AnchorType | None
    anchor_id: str | None
    anchor_pos: AnyPos | None
    highlight_color: HighlightColor | None
    body: dict[str, Any] | None = Field(
        description="TipTap JSON (canvas paper) or an Excalidraw scene (canvas whiteboard); null in lists for "
        "whiteboards (`body_omitted`)"
    )
    body_omitted: bool = Field(default=False, description="true when `body` was left out (fetch the note)")
    body_text: str = Field(description="Plain text of the body, computed by the server")
    excerpt: str = Field(description="body_text on one line, at most ~280 characters")
    paper: Paper | None = Field(description="A page's paper override; null = the notebook's paper")
    canvas: Canvas
    sort: int
    source: NoteSource | None = Field(description="null when unanchored or the anchor is no longer visible")
    has_ink: bool = Field(description="A notebook page with live ink items on its notebook_page layer")
    created_at: dt.datetime
    updated_at: dt.datetime


class ColorCounts(BaseModel):
    lime: int
    violet: int
    amber: int


class AnchorCounts(BaseModel):
    section: int
    question: int
    video: int
    pdf: int
    free: int


class NoteCounts(BaseModel):
    """Over all of the caller's notes (not filtered)."""

    notes: int
    highlights: int
    pages: int
    by_color: ColorCounts = Field(description="Highlights per colour")
    by_anchor: AnchorCounts = Field(description="Notes and highlights per anchor type (pages excluded)")


class NoteList(BaseModel):
    items: list[NoteOut]
    counts: NoteCounts
    next_offset: int | None = Field(description="Pass as `offset` for the next page; null on the last page")


class NoteCreate(BaseModel):
    """`kind 'page'` is created with POST /api/notebooks/{id}/pages. Replaying a create with the same `id` returns
    the stored note (200); so does a second `kind 'note'` on a section that already has one."""

    model_config = _IN
    id: uuid.UUID | None = Field(
        default=None, description="Client-made id (offline queue): makes the create idempotent"
    )
    kind: Literal["note", "highlight"] = "note"
    title: Title | None = None
    notebook_id: uuid.UUID | None = None
    anchor_type: AnchorType | None = None
    anchor_id: AnchorId | None = None
    anchor_pos: AnyPos | None = None
    highlight_color: HighlightColor | None = None
    body: dict[str, Any] | None = None
    canvas: Canvas = "paper"

    @model_validator(mode="after")
    def _shape(self) -> Self:
        check_shape(
            kind=self.kind,
            anchor_type=self.anchor_type,
            anchor_id=self.anchor_id,
            anchor_pos=self.anchor_pos,
            color=self.highlight_color,
            notebook_id=self.notebook_id,
            canvas=self.canvas,
        )
        return self


class NotePatch(BaseModel):
    """Partial update: only the fields sent change; `null` clears a nullable field. `kind` never changes."""

    model_config = _IN
    title: Title | None = None
    body: dict[str, Any] | None = None
    anchor_type: AnchorType | None = None
    anchor_id: AnchorId | None = None
    anchor_pos: AnyPos | None = None
    highlight_color: HighlightColor | None = None
    notebook_id: uuid.UUID | None = None
    paper: Paper | None = None
    canvas: Canvas | None = None
    sort: Sort | None = None
    if_updated_at: dt.datetime | None = Field(
        default=None, description="Refuse (409 note_conflict) unless the stored note still has this updated_at"
    )

    @model_validator(mode="after")
    def _pairs(self) -> Self:
        sent = self.model_fields_set
        if ("anchor_type" in sent) != ("anchor_id" in sent):
            raise ValueError("send anchor_type and anchor_id together")
        if "canvas" in sent and self.canvas is None:
            raise ValueError("canvas cannot be null")
        if "sort" in sent and self.sort is None:
            raise ValueError("sort cannot be null")
        return self


def check_pos_kind(anchor_type: str | None, pos: AnyPos | None) -> None:
    """A video anchor takes `{t}`, every other anchor the text-range shape (migration-free: both are jsonb objects)."""
    if pos is None:
        return
    if (anchor_type == "video") != isinstance(pos, VideoPos):
        raise ValueError("anchor_pos does not fit the anchor type")


def check_shape(
    *,
    kind: str,
    anchor_type: str | None,
    anchor_id: str | None,
    anchor_pos: AnyPos | None,
    color: str | None,
    notebook_id: uuid.UUID | None,
    canvas: str,
) -> None:
    """The kind rules (migration 0017 checks the same in SQL): raises ValueError (-> 422)."""
    check_pos_kind(anchor_type, anchor_pos)
    free = anchor_type in (None, "free")
    if free and anchor_id is not None:
        raise ValueError("a free note has no anchor_id")
    if not free and anchor_id is None:
        raise ValueError(f"an anchor of type {anchor_type} needs anchor_id")
    if kind == "highlight":
        if anchor_type not in ("section", "question"):
            raise ValueError("a highlight is anchored to a section or a question")
        if anchor_pos is None or color is None:
            raise ValueError("a highlight needs anchor_pos and highlight_color")
        if notebook_id is not None or canvas != "paper":
            raise ValueError("a highlight is not in a notebook and has no whiteboard")
    elif color is not None:
        raise ValueError("only highlights have a highlight_color")


class NotebookOut(BaseModel):
    id: uuid.UUID
    title: str
    cover: NotebookCover | None
    paper: Paper
    page_count: int
    note_count: int = Field(description="Typed notes filed in this notebook (pages not counted)")
    created_at: dt.datetime
    updated_at: dt.datetime


class NotebookList(BaseModel):
    items: list[NotebookOut]


class NotebookDetail(NotebookOut):
    pages: list[NoteOut] = Field(description="kind 'page' notes in order (bodies of whiteboards omitted)")


class NotebookCreate(BaseModel):
    """Creates the notebook with its first page."""

    model_config = _IN
    id: uuid.UUID | None = Field(default=None, description="Client-made id: makes the create idempotent")
    first_page_id: uuid.UUID | None = Field(default=None, description="Client-made id of page 1")
    title: Title = Field(min_length=1)
    cover: NotebookCover | None = None
    paper: Paper = "ruled"


class NotebookPatch(BaseModel):
    model_config = _IN
    title: Title | None = Field(default=None, min_length=1)
    cover: NotebookCover | None = None
    paper: Paper | None = None

    @model_validator(mode="after")
    def _no_nulls(self) -> Self:
        for f in ("title", "paper"):
            if f in self.model_fields_set and getattr(self, f) is None:
                raise ValueError(f"{f} cannot be null")
        return self


class PageCreate(BaseModel):
    model_config = _IN
    id: uuid.UUID | None = Field(default=None, description="Client-made id: makes the create idempotent")
    title: Title | None = None
    paper: Paper | None = None
    canvas: Canvas = "paper"
    body: dict[str, Any] | None = None
    position: Sort | None = Field(default=None, description="0-based place among the pages; default: at the end")
    anchor_type: Literal["section", "question", "video", "pdf"] | None = None
    anchor_id: AnchorId | None = None

    @model_validator(mode="after")
    def _anchor(self) -> Self:
        if (self.anchor_type is None) != (self.anchor_id is None):
            raise ValueError("send anchor_type and anchor_id together")
        return self


class PageOrder(BaseModel):
    model_config = _IN
    page_ids: list[uuid.UUID] = Field(
        max_length=MAX_PAGES_PER_NOTEBOOK, description="Every page of the notebook exactly once, in the new order"
    )


__all__ = [
    "LIST_LIMIT_DEFAULT",
    "LIST_LIMIT_MAX",
    "MAX_BODY_BYTES",
    "MAX_NOTEBOOKS_PER_USER",
    "MAX_NOTES_PER_USER",
    "MAX_PAGES_PER_NOTEBOOK",
    "MAX_REQUEST_BYTES",
    "AnchorCounts",
    "AnchorPos",
    "AnchorType",
    "AnyPos",
    "Canvas",
    "ColorCounts",
    "HighlightColor",
    "NoteCounts",
    "NoteCreate",
    "NoteKind",
    "NoteList",
    "NoteOut",
    "NotePatch",
    "NoteSource",
    "NotebookCover",
    "NotebookCreate",
    "NotebookDetail",
    "NotebookList",
    "NotebookOut",
    "NotebookPatch",
    "PageCreate",
    "PageOrder",
    "Paper",
    "VideoPos",
    "check_shape",
]
