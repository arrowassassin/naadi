"""One `vision` call through `naadi_api/llm.py` per image, and the validation of its reply.

The messages hold a fixed instruction and the image, nothing else: no surface text, no title, no name, no typed text
box (PII never reaches the prompt; the gateway also scrubs every text part for user-scoped calls). The reply must be
JSON `{text, lines[]}`; anything else is `UnreadableReply` (the caller marks the layer `failed`, never stores prose).
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from typing import Any, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ...llm import CallContext
from ...llm_config import Purpose

ROLE = "vision"
MAX_TEXT = 20_000
MAX_LINES = 400
MAX_LINE = 2_000

SYSTEM_PROMPT = (
    "You transcribe handwriting. The image shows only a student's handwritten ink, black on a white page. "
    "Transcribe the words, numbers, units and chemical or mathematical notation you can read, line by line, top to "
    "bottom. Do not describe drawings or arrows, do not guess words you cannot read, do not add or explain "
    'anything. Reply with JSON only: {"text": "<the lines joined with newlines>", "lines": ["<line 1>", "..."]}. '
    'If nothing is legible reply {"text": "", "lines": []}.'
)
USER_PROMPT = "Transcribe the handwriting in this image."

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class Gateway(Protocol):
    async def ainvoke(self, role: str, messages: list[BaseMessage], ctx: CallContext, **kwargs: Any) -> Any: ...


class UnreadableReply(ValueError):
    """The model did not answer with the JSON shape asked for."""


def _clean(s: str) -> str:
    return _CONTROL.sub("", s.replace("\r\n", "\n").replace("\r", "\n")).strip()


class OcrResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(default="", max_length=MAX_TEXT * 2)
    lines: list[str] = Field(default_factory=list, max_length=MAX_LINES)

    @field_validator("lines")
    @classmethod
    def _lines(cls, v: list[str]) -> list[str]:
        return [ln for ln in (_clean(x)[:MAX_LINE] for x in v) if ln]

    def normalised(self) -> OcrResult:
        text = _clean(self.text)[:MAX_TEXT]
        lines = self.lines
        if not text and lines:
            text = "\n".join(lines)[:MAX_TEXT]
        if text and not lines:
            lines = [ln.strip() for ln in text.split("\n") if ln.strip()][:MAX_LINES]
        return OcrResult(text=text, lines=lines)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else (p if isinstance(p, str) else "") for p in content)
    return ""


def parse_reply(content: Any) -> OcrResult:
    """`{text, lines}` from a model reply (a fenced or bare JSON object); raises UnreadableReply."""
    raw = _FENCE.sub("", _content_text(content).strip())
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise UnreadableReply("no JSON object")
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise UnreadableReply("not JSON") from exc
    if not isinstance(data, dict) or not ({"text", "lines"} & set(data)):
        raise UnreadableReply("not the {text, lines} shape")
    try:
        return OcrResult.model_validate(data).normalised()
    except ValidationError as exc:
        raise UnreadableReply("invalid fields") from exc


def messages_for(png: bytes) -> list[BaseMessage]:
    url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=[{"type": "text", "text": USER_PROMPT}, {"type": "image_url", "image_url": {"url": url}}]),
    ]


async def recognize(gateway: Gateway, png: bytes, *, user_id: uuid.UUID, run_id: str, purpose: Purpose) -> OcrResult:
    """One vision call (purpose `job` for OCR, `chat` for an interactive convert). Budget errors propagate."""
    ctx = CallContext(purpose=purpose, user_id=user_id, run_id=run_id)
    msg = await gateway.ainvoke(ROLE, messages_for(png), ctx)
    return parse_reply(getattr(msg, "content", msg))


__all__ = ["ROLE", "SYSTEM_PROMPT", "OcrResult", "UnreadableReply", "messages_for", "parse_reply", "recognize"]
