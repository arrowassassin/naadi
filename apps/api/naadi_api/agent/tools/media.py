"""Diagrams, videos and the canvas: `render_diagram`, `suggest_video`, `open_in_canvas`."""

from __future__ import annotations

import secrets
from typing import Any, Literal

import psycopg
from pydantic import BaseModel, Field

from ...assistant.content import InvalidContent, markdown_to_doc
from ...assistant.mermaid import InvalidMermaid, validate_mermaid
from ...db import DatabaseNotConfigured
from ...embeddings.engine import ModelsNotCached, get_engine
from ...embeddings.query import search_local
from ...embeddings.sources import STEP_LABEL
from ...settings import get_settings
from ...videos.query import COMMONS, names_for
from .executor import ToolRefused
from .registry import ToolContext, ToolResult, register_tool
from .untrusted import frame


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


MAX_SUGGESTED = 3  # videos shown at once
MAX_CANDIDATES = 8  # videos the model chooses from


class VideoArgs(BaseModel):
    query: str | None = Field(
        default=None, min_length=2, max_length=300, description="Step 1: what the student asked for, in their words"
    )
    media_ids: list[str] | None = Field(
        default=None,
        max_length=MAX_CANDIDATES,
        description="Step 2: media ids you picked from the step 1 candidates, best first (up to 3 are shown)",
    )


def video_block_data(row: dict[str, Any]) -> dict[str, Any]:
    """The `video` block / canvas data of a catalogue row: YouTube through the official player, Commons as a file."""
    base = {
        "media_id": row["media_id"],
        "title": (row["title"] or "")[:200],
        "start_s": None,
        "step": row["step"],
        "subject": row["subject"],
    }
    if row["provider"] == COMMONS:
        lic = row["licence"] if isinstance(row["licence"], dict) else {}
        return base | {
            "provider": "commons",
            "file_url": row["file_url"],
            "page_url": row["page_url"],
            "attribution": row["attribution"],
            "duration_s": row["duration_s"],
            "licence": {k: lic.get(k) for k in ("license_short_name", "license_url", "artist")} if lic else None,
        }
    return base | {"provider": "youtube", "video_id": row["youtube_id"]}


# Any approved video (a chapter's too, not only the catalogue's); step/subject only when it is in the catalogue.
_VIDEOS = """
select m.id as media_id, m.title, m.youtube_id, m.url as file_url, m.page_url, m.licence, m.attribution,
       m.duration_s, m.channel_id, case when m.kind = 'video_file' then 'commons' else 'youtube' end as provider,
       c.step, c.subject
from public.media_assets m left join public.video_catalog c on c.media_id = m.id
where m.id = any(%s) and m.kind in ('video_youtube', 'video_file') and m.review_status = 'approved'
  and m.embeddable is not false
"""


def _usable(row: dict[str, Any]) -> bool:
    return bool(row["file_url"]) if row["provider"] == COMMONS else bool(row["youtube_id"])


async def _visible_videos(ctx: ToolContext, ids: list[str]) -> dict[str, dict[str, Any]]:
    """The playable rows among `ids` that this student may see (RLS: approved media only)."""
    async with ctx.user_tx() as conn:
        cur = await conn.execute(_VIDEOS, (ids,))
        return {r["media_id"]: dict(r) for r in await cur.fetchall() if _usable(r)}


def _channel(r: dict[str, Any], names: dict[str, str]) -> str:
    return names.get(COMMONS if r["provider"] == COMMONS else r["channel_id"] or "", "")


def _candidate(h: Any, names: dict[str, str]) -> str:
    r = h.extra
    topics = "; ".join(r["topics"] or [])
    parts = [
        h.title,
        _channel(r, names),
        r["subject"],
        STEP_LABEL.get(r["step"], r["step"]),
        f"topics: {topics}" if topics else "",
    ]
    return f"- media_id {h.ref_id}: " + " · ".join(p for p in parts if p)


async def suggest_video(ctx: ToolContext, args: VideoArgs) -> ToolResult:
    """Find and show videos from Naadi's Videos library (YouTube through the official player, open-licence Wikimedia
    Commons files) in two calls. First call with `query` (the student's request as they put it): it returns up to 8
    candidates (media id, title, channel, subject, step, topics) and shows nothing. Pick the ones that really fit
    the request, then call again with their `media_ids` (best first, at most 3) to show them. If none fit, show
    nothing and say so."""
    if not ctx.flags.get("videos"):
        return ToolResult(content="No video library available.")
    if args.media_ids:
        ids = list(dict.fromkeys(args.media_ids))[:MAX_SUGGESTED]
        rows = await _visible_videos(ctx, ids)  # never trust the model's ids: only what RLS lets this student see
        shown = [rows[i] for i in ids if i in rows]
        if not shown:
            raise ToolRefused("unknown_media_ids")
        names = names_for(get_settings().config_dir)
        lines = [" · ".join(x for x in (r["title"] or r["media_id"], _channel(r, names)) if x) for r in shown]
        skipped = len(ids) - len(shown)
        note = f"\n{skipped} unknown media id(s) skipped." if skipped else ""
        return ToolResult(
            content="Videos shown to the student:\n"
            + frame("\n".join(f"- {line}" for line in lines), source="video_catalog")
            + note,
            blocks=[{"type": "video", "data": video_block_data(r)} for r in shown],
        )
    if not args.query or not args.query.strip():
        raise ToolRefused("query_required")
    try:
        engine = get_engine(get_settings().config_dir)
        res = await search_local(ctx.db, engine, ctx.user, args.query, scope="videos", limit=MAX_CANDIDATES)
    except (ModelsNotCached, DatabaseNotConfigured, psycopg.OperationalError, TimeoutError):
        return ToolResult(content="[error: search_unavailable]", status="error")
    if not res.hits:
        return ToolResult(content="No approved video on this topic in the library.")
    names = names_for(get_settings().config_dir)
    listing = frame("\n".join(_candidate(h, names) for h in res.hits), source="video_catalog")
    return ToolResult(
        content="Candidate videos (nothing is shown yet; call suggest_video with the media_ids that fit, or say"
        f" none fits):\n{listing}"
    )


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
            cur = await conn.execute(_VIDEOS, ([args.video_media_id],))
            row = await cur.fetchone()
        if row is None or not _usable(row):
            raise ToolRefused("not_found")
        data = video_block_data(row)
        ref = canvas_ref("video", data, title=args.title, ref={"type": "media", "id": row["media_id"]})
    return ToolResult(
        content=f"Opened in the canvas: {args.title}.",
        canvas=[ref],
        blocks=[{"type": "canvas", "data": ref}],
    )


register_tool("render_diagram", render_diagram, writes=False)
register_tool("suggest_video", suggest_video, writes=False)
register_tool("open_in_canvas", open_in_canvas, writes=False)
