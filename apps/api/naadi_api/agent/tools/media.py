"""Diagrams, videos and the canvas: `render_diagram`, `suggest_video`, `open_in_canvas`."""

from __future__ import annotations

import secrets
from typing import Any, Literal

from pydantic import BaseModel, Field

from ...assistant.content import InvalidContent, markdown_to_doc
from ...assistant.mermaid import InvalidMermaid, validate_mermaid
from .executor import ToolRefused
from .registry import ToolContext, ToolResult, register_tool


def canvas_ref(
    kind: str, data: dict[str, Any], *, title: str | None, ref: dict[str, str] | None = None
) -> dict[str, Any]:
    """A CanvasRef (canvas contract, ledger 20261117T033403)."""
    return {
        "key": "cv_" + secrets.token_hex(6),
        "kind": kind,
        "origin": "assistant",
        "title_key": None,
        "title": (title or "")[:120] or None,
        "ref": ref,
        "data": data,
    }


class DiagramArgs(BaseModel):
    mermaid: str = Field(min_length=10, max_length=4000, description="Mermaid source (flowchart TD preferred)")
    title: str | None = Field(default=None, max_length=120)


async def render_diagram(ctx: ToolContext, args: DiagramArgs) -> ToolResult:
    """Draw a diagram (mechanism, decision tree, pathway) from Mermaid source; it is checked on the server and shown
    as a flowchart block. Prefer this over images for mechanisms. Keep it under 30 nodes."""
    try:
        src = validate_mermaid(args.mermaid)
    except InvalidMermaid as exc:
        return ToolResult(
            content=f"[error: invalid_mermaid:{exc}] Fix the Mermaid source and try again.", status="error"
        )
    return ToolResult(content="Diagram shown to the student.", blocks=[{"type": "flowchart", "data": {"mermaid": src}}])


class VideoArgs(BaseModel):
    topic: str = Field(min_length=2, max_length=120)


async def suggest_video(ctx: ToolContext, args: VideoArgs) -> ToolResult:
    """Suggest one approved video on a topic from Naadi's library (YouTube through the official player)."""
    words = [w for w in args.topic.lower().split() if len(w) > 2][:6]
    if not words:
        raise ToolRefused("empty_topic")
    pattern = "%(" + "|".join(words) + ")%"
    async with ctx.user_tx() as conn:
        cur = await conn.execute(
            "select m.id, m.title, m.youtube_id, m.duration_s, m.attribution from public.media_assets m"
            " where m.kind = 'video_youtube' and m.review_status = 'approved' and m.embeddable is not false"
            " and m.youtube_id is not null"
            " and (lower(coalesce(m.title, '')) similar to %s or lower(coalesce(m.caption, '')) similar to %s)"
            " order by m.title limit 1",
            (pattern, pattern),
        )
        row = await cur.fetchone()
    if row is None:
        return ToolResult(content="No approved video on this topic in the library.")
    data = {
        "provider": "youtube",
        "video_id": row["youtube_id"],
        "media_id": row["id"],
        "title": (row["title"] or "")[:200],
        "start_s": None,
    }
    return ToolResult(content=f"Video shown: {row['title']}.", blocks=[{"type": "video", "data": data}])


class CanvasArgs(BaseModel):
    kind: Literal["document", "diagram", "table", "video"] = Field(
        description="document (a draft the student can keep as a note), diagram (Mermaid), table, video"
    )
    title: str = Field(min_length=1, max_length=120)
    markdown: str | None = Field(default=None, max_length=20_000, description="document: the draft in markdown")
    mermaid: str | None = Field(default=None, max_length=4000, description="diagram: Mermaid source")
    columns: list[str] | None = Field(default=None, max_length=12, description="table: column headings")
    rows: list[list[str]] | None = Field(default=None, max_length=200, description="table: rows of cells")
    video_media_id: str | None = Field(default=None, max_length=128, description="video: a media id from suggest_video")


async def open_in_canvas(ctx: ToolContext, args: CanvasArgs) -> ToolResult:
    """Open a larger piece of work in the canvas beside the chat: a document draft (the student can edit it and keep
    it as their note), a diagram, a table, or a library video."""
    if args.kind == "document":
        if not args.markdown:
            raise ToolRefused("markdown_required")
        try:
            doc = markdown_to_doc(args.markdown)
        except InvalidContent as exc:
            raise ToolRefused(f"invalid_document:{exc}") from exc
        anchor = None
        if ctx.page.type in ("section", "question", "video") and (ctx.page.id or ctx.page.question_id):
            anchor = {"type": ctx.page.type, "id": ctx.page.question_id or ctx.page.id}
        ref = canvas_ref("document", {"doc": doc, "anchor": anchor}, title=args.title)
    elif args.kind == "diagram":
        try:
            src = validate_mermaid(args.mermaid or "")
        except InvalidMermaid as exc:
            return ToolResult(content=f"[error: invalid_mermaid:{exc}]", status="error")
        ref = canvas_ref("diagram", {"mermaid": src}, title=args.title)
    elif args.kind == "table":
        cols = [c[:80] for c in (args.columns or [])]
        if not cols or not args.rows:
            raise ToolRefused("table_required")
        keys = [f"c{i}" for i in range(len(cols))]
        rows = [{k: (r[i][:300] if i < len(r) else None) for i, k in enumerate(keys)} for r in args.rows]
        data: dict[str, Any] = {
            "columns": [{"key": k, "label_key": None, "label": c} for k, c in zip(keys, cols, strict=True)],
            "rows": rows,
        }
        ref = canvas_ref("table", data, title=args.title)
    else:
        if not args.video_media_id:
            raise ToolRefused("video_required")
        async with ctx.user_tx() as conn:
            cur = await conn.execute(
                "select id, title, youtube_id from public.media_assets where id = %s and kind = 'video_youtube'"
                " and review_status = 'approved' and youtube_id is not null",
                (args.video_media_id,),
            )
            row = await cur.fetchone()
        if row is None:
            raise ToolRefused("not_found")
        data = {"provider": "youtube", "video_id": row["youtube_id"], "media_id": row["id"], "start_s": None}
        ref = canvas_ref("video", data, title=args.title, ref={"type": "media", "id": row["id"]})
    return ToolResult(
        content=f"Opened in the canvas: {args.title}.",
        canvas=[ref],
        blocks=[{"type": "canvas", "data": ref}],
    )


register_tool("render_diagram", render_diagram, writes=False)
register_tool("suggest_video", suggest_video, writes=False)
register_tool("open_in_canvas", open_in_canvas, writes=False)
