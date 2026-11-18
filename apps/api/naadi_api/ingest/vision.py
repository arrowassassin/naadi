"""Scanned pages and photos through the `vision` role (`naadi_api/llm.py` only).

What is sent: one rendered page image (PNG, rasterized in the sandbox) or one converted photo, plus a fixed
instruction. Never a PDF file, never document text, never a file name. Pages where the sensitivity gate found an
identifier are never sent (the caller filters them). Each call uses its own CallContext with purpose `job` and the
student's id, so the gateway scrubs text parts, enforces the job reserve and records `llm_usage` under the run id
`ingest:<item id>`, which is what the per-student daily vision cap counts.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from typing import Any, Literal, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..llm import CallContext

ROLE = "vision"
RUN_PREFIX = "ingest:"
MAX_TEXT = 40_000

PAGE_SYSTEM = (
    "You read one scanned page of a medical student's study material. Transcribe its text in reading order, keeping "
    "headings on their own lines and list items as lines. Include handwriting you can read. Do not summarise, "
    "translate, explain or add anything; skip text you cannot read. Reply with JSON only: "
    '{"text": "<the page text>", "handwritten": <true if most of the page is handwriting>, '
    '"has_diagram": <true if the page is mostly a diagram or table>}.'
)
IMAGE_SYSTEM = (
    "You read one photo or screenshot a medical student uploaded to their study app. Transcribe any text or "
    "handwriting in reading order. If it is a diagram, chart or table, also give a one-sentence neutral caption of "
    "what it shows. Do not explain the content or add facts. Reply with JSON only: "
    '{"text": "<transcribed text>", "handwritten": <true|false>, "has_diagram": <true|false>, '
    '"caption": "<one sentence or empty>"}.'
)
USER_PROMPT = "Read this image."
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class Gateway(Protocol):
    async def ainvoke(self, role: str, messages: list[BaseMessage], ctx: CallContext, **kwargs: Any) -> Any: ...


class UnreadableReply(ValueError):
    pass


class VisionText(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(default="", max_length=MAX_TEXT * 2)
    handwritten: bool = False
    has_diagram: bool = False
    caption: str = Field(default="", max_length=600)

    def normalised(self) -> VisionText:
        clean = _CONTROL.sub("", self.text.replace("\r\n", "\n")).strip()[:MAX_TEXT]
        cap = _CONTROL.sub("", self.caption.replace("\n", " ")).strip()[:300]
        return VisionText(text=clean, handwritten=self.handwritten, has_diagram=self.has_diagram, caption=cap)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else (p if isinstance(p, str) else "") for p in content)
    return ""


def parse_reply(content: Any) -> VisionText:
    raw = _FENCE.sub("", _content_text(content).strip())
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise UnreadableReply("no JSON object")
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise UnreadableReply("not JSON") from exc
    if not isinstance(data, dict) or "text" not in data:
        raise UnreadableReply("not the expected shape")
    try:
        return VisionText.model_validate(data).normalised()
    except ValidationError as exc:
        raise UnreadableReply("invalid fields") from exc


def messages_for(
    image: bytes, mime: Literal["image/png", "image/webp"], kind: Literal["page", "image"]
) -> list[BaseMessage]:
    url = f"data:{mime};base64," + base64.b64encode(image).decode("ascii")
    system = PAGE_SYSTEM if kind == "page" else IMAGE_SYSTEM
    return [
        SystemMessage(content=system),
        HumanMessage(content=[{"type": "text", "text": USER_PROMPT}, {"type": "image_url", "image_url": {"url": url}}]),
    ]


async def read_image(
    gateway: Gateway,
    image: bytes,
    *,
    mime: Literal["image/png", "image/webp"],
    kind: Literal["page", "image"],
    user_id: uuid.UUID,
    item_id: uuid.UUID,
) -> VisionText:
    """One vision call; BudgetExhausted / LLMUnavailable propagate (the caller falls back to local OCR)."""
    ctx = CallContext(purpose="job", user_id=user_id, run_id=f"{RUN_PREFIX}{item_id}")
    msg = await gateway.ainvoke(ROLE, messages_for(image, mime, kind), ctx)
    return parse_reply(getattr(msg, "content", msg))


__all__ = ["ROLE", "RUN_PREFIX", "UnreadableReply", "VisionText", "messages_for", "parse_reply", "read_image"]
