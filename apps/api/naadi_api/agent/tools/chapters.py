"""`edit_chapter_section`: block-level edits of a chapter section, behind a diff confirm.

Ops are `replace | insert_after | delete` on top-level blocks of the section body, each new block written as
markdown (one block per op) and validated against S06's node schema subset the assistant may write.

- An admin editing a shared chapter: on accept, the previous body is kept as a `chapter_section_revisions` row
  (`origin = 'assistant'`, rollback in admin) and the section is updated (`admin_edited_at` set, so a later seed
  import keeps it), all in one transaction that first checks the body did not change since the preview.
- Everyone else: a private overlay in the student's page note on that section (S13's notes store), holding the
  proposed blocks under a "Suggested edit" heading; the shared chapter is never touched.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from ...assistant.content import InvalidContent, markdown_to_doc, validate_node
from ...cards.routes import as_service
from .executor import ToolRefused
from .registry import Proposal, ToolContext, ToolResult, WriteTool, register_tool

MAX_OPS = 12


class EditOp(BaseModel):
    op: Literal["replace", "insert_after", "delete"]
    index: int = Field(ge=-1, le=2000, description="0-based top-level block index (-1 with insert_after = at the top)")
    markdown: str | None = Field(default=None, max_length=8000, description="The new block (replace/insert_after)")
    node: dict[str, Any] | None = Field(default=None, description="(set by the student's edit; leave empty)")


class EditSectionArgs(BaseModel):
    section_id: str = Field(min_length=1, max_length=128)
    ops: list[EditOp] = Field(min_length=1, max_length=MAX_OPS)


def _hash(body: Any) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _new_node(op: EditOp) -> dict[str, Any]:
    try:
        if op.node is not None:
            node: dict[str, Any] = validate_node(op.node)
            return node
        if not op.markdown:
            raise ToolRefused("markdown_required")
        doc = markdown_to_doc(op.markdown)
    except InvalidContent as exc:
        raise ToolRefused(f"invalid_block:{exc}") from exc
    blocks: list[dict[str, Any]] = doc["content"]
    if len(blocks) != 1:
        raise ToolRefused("one_block_per_op")
    return blocks[0]


def apply_ops(blocks: list[dict[str, Any]], ops: list[tuple[str, int, dict[str, Any] | None]]) -> list[dict[str, Any]]:
    """Apply ops whose indexes refer to the ORIGINAL body (processed from the highest index down)."""
    out = list(blocks)
    for kind, index, node in sorted(ops, key=lambda o: o[1], reverse=True):
        if kind == "replace":
            out[index] = node  # type: ignore[assignment]
        elif kind == "delete":
            del out[index]
        else:
            out.insert(index + 1, node)  # type: ignore[arg-type]
    return out


async def _prepare(ctx: ToolContext, args: EditSectionArgs) -> Proposal:
    """Propose edits to a chapter section, block by block (replace, insert after, delete). The student sees a diff
    and confirms. For an admin on a shared chapter this revises the chapter; for everyone else it saves a private
    overlay note on that section."""
    async with ctx.user_tx() as conn:
        cur = await conn.execute(
            "select s.id, s.heading, s.body, c.owner_user_id, c.kind from public.chapter_sections s"
            " join public.chapters c on c.id = s.chapter_id where s.id = %s",
            (args.section_id,),
        )
        row = await cur.fetchone()
    if row is None or row["kind"] == "mock_exam":
        raise ToolRefused("section_not_found")
    body = row["body"] if isinstance(row["body"], dict) else None
    if body is None:
        raise ToolRefused("section_not_editable")
    blocks = list(body.get("content") or [])
    seen: set[int] = set()
    planned: list[tuple[str, int, dict[str, Any] | None]] = []
    preview_ops: list[dict[str, Any]] = []
    for op in args.ops:
        if op.op != "insert_after" and not 0 <= op.index < len(blocks):
            raise ToolRefused("index_out_of_range")
        if op.op == "insert_after" and not -1 <= op.index < len(blocks):
            raise ToolRefused("index_out_of_range")
        if op.op != "insert_after":
            if op.index in seen:
                raise ToolRefused("conflicting_ops")
            seen.add(op.index)
        node = None if op.op == "delete" else _new_node(op)
        planned.append((op.op, op.index, node))
        before = blocks[op.index] if op.op != "insert_after" else None
        preview_ops.append({"op": op.op, "index": op.index, "before": before, "after": node})
    shared = row["owner_user_id"] is None
    target = "revision" if (shared and ctx.is_admin) else "overlay_note"
    preview = {
        "kind": "chapter_diff",
        "section_id": row["id"],
        "heading": row["heading"],
        "target": target,
        "ops": preview_ops,
    }
    return Proposal(
        preview=preview,
        data={"target": target, "planned": planned, "hash": _hash(body), "blocks": blocks},
    )


async def _apply(ctx: ToolContext, args: EditSectionArgs, p: Proposal) -> ToolResult:
    planned = p.data["planned"]
    if p.data["target"] == "overlay_note":
        # The student's private overlay: the proposed blocks go into their page note on that section (created if
        # it has none), under a marker heading; the shared chapter is never touched.
        from .note_store import create

        blocks = [n for _, _, n in planned if n is not None]
        marker = {"type": "heading", "attrs": {"level": 3}, "content": [{"type": "text", "text": "Suggested edit"}]}
        removed = sum(1 for k, _, _ in planned if k == "delete")
        content = [marker, *blocks]
        if removed:
            content.append(
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": f"Remove {removed} block(s) of the section."}],
                }
            )
        note_id = await create(ctx, "section", args.section_id, {"type": "doc", "content": content})
        return ToolResult(content=f"Saved as a private overlay in the student's note on the section (id {note_id}).")
    if not ctx.is_admin:
        raise ToolRefused("admin_only")

    async def write(conn: Any) -> bool:
        cur = await conn.execute(
            "select s.body, s.body_html, c.owner_user_id from public.chapter_sections s"
            " join public.chapters c on c.id = s.chapter_id where s.id = %s for update of s",
            (args.section_id,),
        )
        row = await cur.fetchone()
        if row is None or row["owner_user_id"] is not None or _hash(row["body"]) != p.data["hash"]:
            return False
        await conn.execute(
            "insert into public.chapter_section_revisions (section_id, body, body_html, author_user_id, origin, note)"
            " values (%s, %s, %s, %s, 'assistant', 'replaced by an assistant edit')",
            (args.section_id, Jsonb(row["body"]), row["body_html"], ctx.user_id),
        )
        new_body = {"type": "doc", "content": apply_ops(p.data["blocks"], planned)}
        await conn.execute(
            "update public.chapter_sections set body = %s, admin_edited_at = now(), updated_at = now() where id = %s",
            (Jsonb(new_body), args.section_id),
        )
        return True

    if not await as_service(ctx.db, write):
        return ToolResult(content="[error: section_changed_since_preview]", status="error")
    return ToolResult(content="The section was revised; the previous version is kept for rollback.")


register_tool("edit_chapter_section", WriteTool(_prepare, _apply), writes=True)
