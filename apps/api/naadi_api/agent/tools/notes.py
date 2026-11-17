"""Write tools on the student's notes: `save_note`, `append_to_note`, `update_note` (each behind a confirm).

Written through S13's notes store (`note_store`), anchored to the page the student was on: a section (its page
note: a save appends when one exists), an answered question, or a video; else free. The model writes markdown; the
preview and the stored body are TipTap JSON. An edited preview comes back as `doc` and is validated again.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from ...assistant.content import InvalidContent, markdown_to_doc, validate_doc
from . import note_store
from .executor import ToolRefused
from .registry import Proposal, ToolContext, ToolResult, WriteTool, register_tool


async def _anchor(ctx: ToolContext) -> tuple[str, str | None, str | None]:
    """(anchor_type, anchor_id, label) from the verified page, checked the way the notes API checks it."""
    p = ctx.page
    if p.type == "section" and p.id:
        cand: tuple[str, str | None, str | None] = ("section", p.id, p.title)
    elif p.type == "question" and p.question_id and p.question_revealed:
        cand = ("question", p.question_id, p.title)
    elif p.video and p.video.get("media_id"):
        cand = ("video", str(p.video["media_id"]), p.title)
    else:
        return "free", None, None
    if await note_store.anchor_allowed(ctx, cand[0], cand[1]):
        return cand
    return "free", None, None


def _doc(markdown: str | None, doc: dict[str, Any] | None) -> dict[str, Any]:
    try:
        if doc is not None:
            return validate_doc(doc)
        if markdown:
            return markdown_to_doc(markdown)
    except InvalidContent as exc:
        raise ToolRefused(f"invalid_document:{exc}") from exc
    raise ToolRefused("empty_note")


class SaveNoteArgs(BaseModel):
    markdown: str | None = Field(default=None, max_length=20_000, description="The note, in markdown")
    doc: dict[str, Any] | None = Field(default=None, description="(set by the student's edit; leave empty)")


async def _save_prepare(ctx: ToolContext, args: SaveNoteArgs) -> Proposal:
    """Save a note for the student, pinned to the page they are on (a section, an answered question or a video).
    The student confirms before anything is saved."""
    doc = _doc(args.markdown, args.doc)
    a_type, a_id, label = await _anchor(ctx)
    existing = await note_store.section_note(ctx, a_id) if a_type == "section" and a_id else None
    preview = {
        "kind": "note",
        "mode": "append" if existing else "create",
        "note_id": str(existing) if existing else None,
        "anchor": {"type": a_type, "id": a_id, "label": label},
        "doc": doc,
    }
    return Proposal(preview=preview, data={"doc": doc, "anchor": (a_type, a_id)})


async def _save_apply(ctx: ToolContext, args: SaveNoteArgs, p: Proposal) -> ToolResult:
    a_type, a_id = p.data["anchor"]
    note_id = await note_store.create(ctx, a_type, a_id, p.data["doc"])
    return ToolResult(content=f"Saved the note (id {note_id}).")


class NoteEditArgs(BaseModel):
    note_id: uuid.UUID = Field(description="The note's id")
    markdown: str | None = Field(default=None, max_length=20_000)
    doc: dict[str, Any] | None = Field(default=None, description="(set by the student's edit; leave empty)")


def _anchor_of(note: dict[str, Any]) -> dict[str, Any]:
    return {"type": note.get("anchor_type") or "free", "id": note.get("anchor_id"), "label": note.get("title")}


async def _append_prepare(ctx: ToolContext, args: NoteEditArgs) -> Proposal:
    """Add text to the end of one of the student's existing notes (they confirm first)."""
    note = await note_store.own_note(ctx, args.note_id)
    addition = _doc(args.markdown, args.doc)
    preview = {
        "kind": "note",
        "mode": "append",
        "note_id": str(args.note_id),
        "anchor": _anchor_of(note),
        "doc": addition,
    }
    return Proposal(preview=preview, data={"doc": addition})


async def _append_apply(ctx: ToolContext, args: NoteEditArgs, p: Proposal) -> ToolResult:
    await note_store.append(ctx, args.note_id, p.data["doc"])
    return ToolResult(content="Added to the note.")


async def _update_prepare(ctx: ToolContext, args: NoteEditArgs) -> Proposal:
    """Replace the text of one of the student's existing notes with a revised version (they confirm first)."""
    note = await note_store.own_note(ctx, args.note_id)
    doc = _doc(args.markdown, args.doc)
    preview = {"kind": "note", "mode": "update", "note_id": str(args.note_id), "anchor": _anchor_of(note), "doc": doc}
    return Proposal(preview=preview, data={"doc": doc})


async def _update_apply(ctx: ToolContext, args: NoteEditArgs, p: Proposal) -> ToolResult:
    await note_store.replace(ctx, args.note_id, p.data["doc"])
    return ToolResult(content="Updated the note.")


register_tool("save_note", WriteTool(_save_prepare, _save_apply), writes=True)
register_tool("append_to_note", WriteTool(_append_prepare, _append_apply), writes=True)
register_tool("update_note", WriteTool(_update_prepare, _update_apply), writes=True)
