"""`remember` / `forget`: the student's long-term memory (Aegra store via langmem), each behind a confirm.

Only short study facts the student approves (weak topics, preferences, exam timing). The fact is scrubbed before
the preview, so what the student approves is exactly what is stored; Settings → Assistant lists and deletes them.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ...assistant import memory as mem
from .executor import ToolRefused
from .registry import Proposal, ToolContext, ToolResult, WriteTool, register_tool


class RememberArgs(BaseModel):
    fact: str = Field(min_length=3, max_length=mem.MAX_FACT_CHARS, description="One short study fact")


async def _remember_prepare(ctx: ToolContext, args: RememberArgs) -> Proposal:
    """Remember a short fact about how the student studies (a weak topic, a preference, their exam date) for later
    conversations. Never names, contact details, documents or health information. The student confirms first."""
    if ctx.store is None:
        raise ToolRefused("memory_unavailable")
    try:
        fact = await mem.clean_fact(ctx.user_id, args.fact)
    except ValueError as exc:
        raise ToolRefused(str(exc)) from exc
    return Proposal(preview={"kind": "memory", "key": "", "fact": fact}, data={"fact": fact})


async def _remember_apply(ctx: ToolContext, args: RememberArgs, p: Proposal) -> ToolResult:
    try:
        m = await mem.remember(ctx.store, ctx.user_id, p.data["fact"], max_items=ctx.memory_max_items)
    except mem.MemoryFull:
        return ToolResult(content="[refused: memory_full]", status="refused")
    except ValueError as exc:
        return ToolResult(content=f"[refused: {exc}]", status="refused")
    return ToolResult(content=f"Remembered (key {m.key}).")


class ForgetArgs(BaseModel):
    key: str = Field(min_length=36, max_length=36, description="The memory's key, as shown in [m:key]")


async def _forget_prepare(ctx: ToolContext, args: ForgetArgs) -> Proposal:
    """Forget one remembered fact (by its key). The student confirms first."""
    if ctx.store is None:
        raise ToolRefused("memory_unavailable")
    m = await mem.get_memory(ctx.store, ctx.user_id, args.key)
    if m is None:
        raise ToolRefused("memory_not_found")
    return Proposal(preview={"kind": "forget", "key": m.key, "fact": m.fact}, editable=False)


async def _forget_apply(ctx: ToolContext, args: ForgetArgs, p: Proposal) -> ToolResult:
    ok = await mem.forget(ctx.store, ctx.user_id, args.key)
    return ToolResult(content="Forgotten." if ok else "[error: memory_not_found]", status="ok" if ok else "error")


register_tool("remember", WriteTool(_remember_prepare, _remember_apply), writes=True)
register_tool("forget", WriteTool(_forget_prepare, _forget_apply), writes=True)
