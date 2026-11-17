"""The tutor's note writes through S13's notes store (notes are API-written since migration 0017): anchor checks as
the student (RLS: a hidden section is refused, a question needs a graded attempt), body validated and its plain
text computed exactly as `POST /api/notes`, writes on the API's connection filtered on the run's user, then S07's
index reconcile for the note. A section keeps one page note per student, so a save on a section that already has
one appends to it."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import psycopg

from ...notes import routes as notes_api
from ...notes import store as notes
from ...notes.text import body_text
from .executor import ToolRefused
from .registry import ToolContext


def _checked(doc: dict[str, Any], ctx: ToolContext) -> str:
    try:
        return notes_api.checked_body(doc, "paper", ctx.user_id)[1]
    except notes.NotesError as exc:
        raise ToolRefused(exc.code) from exc


async def anchor_allowed(ctx: ToolContext, anchor_type: str, anchor_id: str | None) -> bool:
    if anchor_type == "free":
        return True

    async def run(conn: psycopg.AsyncConnection[Any]) -> None:
        await notes.check_anchor(conn, anchor_type, anchor_id)

    try:
        await notes_api.as_user(ctx.db, ctx.user, run)
    except notes.NotesError:
        return False
    return True


async def section_note(ctx: ToolContext, section_id: str) -> uuid.UUID | None:
    """The student's page note on this section, if there is one."""

    async def run(conn: psycopg.AsyncConnection[Any]) -> Any:
        cur = await conn.execute(
            "select id from public.notes where user_id = %s and kind = 'note' and anchor_type = 'section'"
            " and anchor_id = %s",
            (ctx.user_id, section_id),
        )
        return await cur.fetchone()

    row = await notes_api.as_service(ctx.db, run)
    return uuid.UUID(str(row["id"])) if row else None


async def own_note(ctx: ToolContext, note_id: uuid.UUID) -> dict[str, Any]:
    async def run(conn: psycopg.AsyncConnection[Any]) -> dict[str, Any] | None:
        return await notes.get_note(conn, ctx.user_id, note_id, body=notes.FULL_BODY)

    try:
        row = await notes_api.as_user(ctx.db, ctx.user, run)
    except notes.NotesError as exc:
        raise ToolRefused(exc.code) from exc
    if row is None:
        raise ToolRefused("note_not_found")
    if row.get("canvas") == "whiteboard" or row.get("kind") == "highlight":
        raise ToolRefused("note_not_editable")
    return row


def _reindex(ctx: ToolContext, note_ids: list[str]) -> None:
    """S07's incremental index reconcile, when the runtime hosts the app (the Aegra process)."""
    host = sys.modules.get("naadi_api.agent.host")
    app = getattr(host, "naadi_app", None) if host is not None else None
    if app is not None and note_ids:
        from ...embeddings.jobs import schedule_reindex

        schedule_reindex(app, ("notes",), owner=ctx.user_id, ref_ids=note_ids)


async def create(ctx: ToolContext, anchor_type: str, anchor_id: str | None, doc: dict[str, Any]) -> uuid.UUID:
    """A new note; on a section that already has its page note, the blocks are appended to that note."""
    text = _checked(doc, ctx)
    note = notes.NewNote(
        id=uuid.uuid4(),
        kind="note",
        title=None,
        notebook_id=None,
        anchor_type=None if anchor_type == "free" else anchor_type,
        anchor_id=anchor_id,
        anchor_pos=None,
        highlight_color=None,
        body=doc,
        body_text=text,
        paper=None,
        canvas="paper",
        sort=0,
    )

    async def write(conn: psycopg.AsyncConnection[Any]) -> uuid.UUID:
        if await notes.insert_note(conn, ctx.user_id, note):
            return note.id
        existing = await notes.resolve_replay(conn, ctx.user_id, note)
        await _append_locked(conn, ctx, existing, doc)
        return existing

    try:
        note_id = await notes_api.as_service(ctx.db, write)
    except notes.NotesError as exc:
        raise ToolRefused(exc.code) from exc
    _reindex(ctx, [str(note_id)])
    return note_id


async def _append_locked(
    conn: psycopg.AsyncConnection[Any], ctx: ToolContext, note_id: uuid.UUID, doc: dict[str, Any]
) -> None:
    cur = await notes.lock_note(conn, ctx.user_id, note_id)
    body = (
        cur["body"]
        if isinstance(cur["body"], dict) and cur["body"].get("type") == "doc"
        else {"type": "doc", "content": []}
    )
    merged = {"type": "doc", "content": [*(body.get("content") or []), *doc["content"]]}
    text = _checked(merged, ctx)
    await notes.update_note(conn, ctx.user_id, note_id, {"body": merged, "body_text": text})


async def append(ctx: ToolContext, note_id: uuid.UUID, doc: dict[str, Any]) -> None:
    async def write(conn: psycopg.AsyncConnection[Any]) -> None:
        await _append_locked(conn, ctx, note_id, doc)

    try:
        await notes_api.as_service(ctx.db, write)
    except notes.NotesError as exc:
        raise ToolRefused(exc.code) from exc
    _reindex(ctx, [str(note_id)])


async def replace(ctx: ToolContext, note_id: uuid.UUID, doc: dict[str, Any]) -> None:
    text = _checked(doc, ctx)

    async def write(conn: psycopg.AsyncConnection[Any]) -> None:
        await notes.lock_note(conn, ctx.user_id, note_id)
        await notes.update_note(conn, ctx.user_id, note_id, {"body": doc, "body_text": text})

    try:
        await notes_api.as_service(ctx.db, write)
    except notes.NotesError as exc:
        raise ToolRefused(exc.code) from exc
    _reindex(ctx, [str(note_id)])


__all__ = ["anchor_allowed", "append", "body_text", "create", "own_note", "replace", "section_note"]
