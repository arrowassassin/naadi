"""Framework-free core of the tutor tools `generate_image` and `generate_video` (segment 15b).

S14's registry adapter (`agent/tools/generation.py`, registered with `requires_flag="generation"`, `writes=True`)
is a thin wrapper over these functions, so the contract is tested here without LangGraph:

- `prepare_preview(...)`: what the confirm chip shows (no write): kind, scrubbed prompt, seconds, remaining quota.
  Refusals (prompt rules, cap, pause, not configured) raise `GenerationError` before the student is asked.
- `run_tool(...)`: after Accept, runs the generation and returns what the registry needs: model-facing `content`
  (stable codes only, never prose or provider text), the `image`/`video` answer block, and the `generated_media`
  canvas ref. Progress goes to `on_event` as `{"stage", "media_id", "queue_position"}` (custom `progress` events).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from .client import Kind
from .common import GenerationError
from .service import EventFn, GenerationService, Preview


@dataclass
class ToolOutcome:
    status: Literal["ok", "error", "refused"]
    content: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    canvas: list[dict[str, Any]] = field(default_factory=list)


async def prepare_preview(
    service: GenerationService, user_id: uuid.UUID, kind: Kind, prompt: str, seconds: int | None = None
) -> Preview:
    return await service.prepare(user_id, kind, prompt, seconds)


def _refusal(exc: GenerationError) -> ToolOutcome:
    kind: Literal["error", "refused"] = "refused" if exc.code.startswith("prompt_") else "error"
    until = f" until={exc.resets_at.isoformat()}" if exc.resets_at else ""
    return ToolOutcome(kind, f"{exc.code}{until}")


async def run_tool(
    service: GenerationService,
    user_id: uuid.UUID,
    kind: Kind,
    prompt: str,
    seconds: int | None = None,
    on_event: EventFn | None = None,
) -> ToolOutcome:
    try:
        item = await service.run(user_id, kind, prompt, seconds, on_event)
    except GenerationError as exc:
        return _refusal(exc)
    data = {"media_id": str(item.id), "kind": item.kind}
    return ToolOutcome(
        "ok",
        f"generated {item.kind} id={item.id} (AI-generated illustration, not a clinical reference)",
        blocks=[{"type": item.kind, "data": data}],
        canvas=[
            {
                "key": f"gm_{item.id}",
                "kind": "generated_media",
                "origin": "assistant",
                "title_key": "generation.canvas_title",
                "title": None,
                "ref": {"type": "generated_media", "id": str(item.id)},
                "data": data,
            }
        ],
    )


__all__ = ["ToolOutcome", "prepare_preview", "run_tool"]
