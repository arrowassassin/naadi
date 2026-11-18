"""Page context (HANDOFF §6.3) and attachments, as the server accepts them.

The client sends ids and display hints; every *state* is the server's: `exam_mode` is ignored (the guard reads
`active_exam`), `question.answered` / `question.chosen` are replaced by the student's revealed attempt, and
`user_id` never comes from here. Selection text and images are attachments of one turn, size-capped; selection
text is never stored in `assistant_threads.page_ref`.
"""

from __future__ import annotations

import base64
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..agent.tools.registry import PageRef
from ..db import Database
from .questions import revealed_choice, revealed_questions

PageType = Literal[
    "section", "question", "flashcard", "note", "search", "today", "practice", "cards", "page", "video", "pdf_page"
]
_ID = r"^[A-Za-z0-9][A-Za-z0-9_:.\-]{0,127}$"
MAX_SELECTION = 4000
MAX_IMAGE_BYTES = 1_500_000
_DATA_URL = re.compile(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\s]+)$")
_MAGIC = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff", "image/webp": b"RIFF"}


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class VideoIn(_Loose):
    media_id: str = Field(pattern=_ID)
    t: float = Field(ge=0, le=86_400)


class QuestionIn(_Loose):
    id: str = Field(pattern=_ID)


class PageContextIn(_Loose):
    route: str | None = Field(default=None, max_length=300)
    type: PageType = "page"
    id: str | None = Field(default=None, pattern=_ID)
    title: str | None = Field(default=None, max_length=200)
    chapter_id: str | None = Field(default=None, pattern=_ID)
    video: VideoIn | None = None
    question: QuestionIn | None = None


class Surface(_Loose):
    type: str = Field(pattern=r"^[a-z_]{2,32}$")
    id: str = Field(pattern=_ID)


class AttachmentIn(_Loose):
    kind: Literal["selection", "image"]
    text: str | None = Field(default=None, max_length=MAX_SELECTION)
    data_url: str | None = None
    surface: Surface | None = None


class InvalidAttachment(ValueError):
    pass


@dataclass
class TurnAttachments:
    selections: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)  # validated data URLs, this turn only (cleared at turn end)
    image_texts: list[str] = field(default_factory=list)


def parse_page(raw: Any) -> PageContextIn:
    if not isinstance(raw, dict):
        return PageContextIn()
    try:
        return PageContextIn.model_validate(raw)
    except ValidationError:
        # A malformed page context is dropped, not trusted in part.
        return PageContextIn()


def _image(data_url: str) -> str:
    if len(data_url) > int(MAX_IMAGE_BYTES * 1.37) + 64:
        raise InvalidAttachment("image_too_large")
    m = _DATA_URL.match(data_url.strip())
    if not m:
        raise InvalidAttachment("image_type")
    try:
        raw = base64.b64decode(m.group(2), validate=False)
    except (ValueError, TypeError) as exc:
        raise InvalidAttachment("image_data") from exc
    if len(raw) > MAX_IMAGE_BYTES:
        raise InvalidAttachment("image_too_large")
    if not raw.startswith(_MAGIC[m.group(1)]):
        raise InvalidAttachment("image_type")
    return f"data:{m.group(1)};base64,{base64.b64encode(raw).decode()}"


def parse_attachments(raw: Any, max_items: int) -> TurnAttachments:
    out = TurnAttachments()
    if not isinstance(raw, list):
        return out
    if len(raw) > max_items:
        raise InvalidAttachment("too_many")
    for item in raw:
        try:
            a = AttachmentIn.model_validate(item)
        except ValidationError as exc:
            raise InvalidAttachment("invalid") from exc
        if a.kind == "selection":
            text = " ".join((a.text or "").split())
            if text:
                out.selections.append(text[:MAX_SELECTION])
        else:
            if not a.data_url:
                raise InvalidAttachment("image_missing")
            out.images.append(_image(a.data_url))
            if a.text:
                out.image_texts.append(" ".join(a.text.split())[:MAX_SELECTION])
    return out


async def verify_page(db: Database, user_id: uuid.UUID, page: PageContextIn, ask_kind: str | None) -> PageRef:
    question_id = page.question.id if page.question else (page.id if page.type == "question" else None)
    revealed = False
    chosen = None
    if question_id:
        revealed = question_id in await revealed_questions(db, user_id, [question_id])
        chosen = await revealed_choice(db, user_id, question_id) if revealed else None
    return PageRef(
        route=page.route,
        type=page.type,
        id=page.id,
        title=page.title,
        chapter_id=page.chapter_id,
        video=page.video.model_dump() if page.video else None,
        question_id=question_id,
        question_revealed=revealed,
        chosen=chosen,
        ask_kind=ask_kind,
    )


def page_ref_row(page: PageRef) -> dict[str, str] | None:
    """What `assistant_threads.page_ref` keeps: route, type and id only."""
    out = {k: v for k, v in (("route", page.route), ("type", page.type), ("id", page.id)) if v}
    return {k: v[:180] for k, v in out.items()} or None


def page_to_state(page: PageRef) -> dict[str, Any]:
    return {
        "route": page.route,
        "type": page.type,
        "id": page.id,
        "title": page.title,
        "chapter_id": page.chapter_id,
        "video": page.video,
        "question_id": page.question_id,
        "question_revealed": page.question_revealed,
        "ask_kind": page.ask_kind,
    }


def page_from_state(d: Any) -> PageRef:
    if not isinstance(d, dict):
        return PageRef()
    return PageRef(
        route=d.get("route"),
        type=d.get("type"),
        id=d.get("id"),
        title=d.get("title"),
        chapter_id=d.get("chapter_id"),
        video=d.get("video"),
        question_id=d.get("question_id"),
        question_revealed=bool(d.get("question_revealed")),
        ask_kind=d.get("ask_kind"),
    )
