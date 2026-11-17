"""Long-term memory on the Aegra store, through langmem (segment 14; lead decision: no mem0, no Naadi table).

Namespaces:
- ("memories", user_id): facts the student approved (the `remember` confirm, or "Keep" in Settings). The tutor
  retrieves the few most relevant per turn by semantic search (store index: local fastembed, see agent/host.py).
- ("memory_suggestions", user_id): what langmem's background memory manager proposes after a turn. Nothing there
  reaches a prompt until the student keeps it in Settings → Assistant.

Writes use langmem's manage-memory tool and reads its search tool's store query, so the stored shape is langmem's
own ({"content": ...}). Every fact is scrubbed (the student's identifiers + generic PII) and checked for vault
material before it is stored, so a memory can never carry a name, an e-mail or vault text into a later prompt.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from langchain_core.messages import BaseMessage
from langgraph.store.base import BaseStore, Item
from langmem import create_manage_memory_tool, create_memory_store_manager

from ..llm_budget import TurnCounter
from ..llm_privacy import PrivacyDirectory, scrub_generic
from ..logs import get_logger
from .leak import LeakBlocked, check_payload

log = get_logger("naadi.assistant.memory")

MEMORIES = "memories"
SUGGESTIONS = "memory_suggestions"
MAX_FACT_CHARS = 300
_ID = re.compile(r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b")

REFLECT_INSTRUCTIONS = (
    "You keep short study memories for one medical student preparing for USMLE. From the conversation, propose at "
    "most three short facts worth remembering across sessions: weak topics, study preferences, exam timing, how they "
    "like explanations. One sentence each, under 200 characters. Never record names, e-mail addresses, phone numbers, "
    "identity or immigration documents, health information about the student, or anything copied from a book. "
    "Skip anything already stored."
)


class MemoryFull(RuntimeError):
    pass


@dataclass(frozen=True)
class Memory:
    key: str
    fact: str
    created_at: datetime | None


def ns(kind: str, user_id: uuid.UUID) -> tuple[str, str]:
    return (kind, str(user_id))


def fact_of(value: Any) -> str:
    """langmem stores {"content": str} (manage tool) or {"kind": ..., "content": {"content": str}} (manager)."""
    content = value.get("content") if isinstance(value, dict) else None
    if isinstance(content, dict):
        content = content.get("content")
    return str(content or "").strip()


def _memory(item: Item) -> Memory:
    return Memory(item.key, fact_of(item.value), item.created_at)


_privacy: PrivacyDirectory | None = None


def set_privacy(directory: PrivacyDirectory | None) -> None:
    global _privacy
    _privacy = directory


async def clean_fact(user_id: uuid.UUID, fact: str) -> str:
    """The fact as it may be stored: one line, scrubbed of the student's identifiers, no vault material."""
    text = " ".join(fact.split())[:MAX_FACT_CHARS]
    try:
        check_payload(text, where="memory")
    except LeakBlocked as exc:
        raise ValueError("memory_rejected") from exc
    text = (await _privacy.get(user_id)).scrub(text) if _privacy is not None else scrub_generic(text)
    if not text.strip():
        raise ValueError("memory_empty")
    return text


async def list_memories(store: BaseStore, user_id: uuid.UUID, kind: str = MEMORIES, limit: int = 500) -> list[Memory]:
    items = await store.asearch(ns(kind, user_id), limit=limit)
    return sorted((_memory(i) for i in items), key=lambda m: m.created_at or datetime.min, reverse=True)


async def retrieve(store: BaseStore, user_id: uuid.UUID, query: str, k: int) -> list[Memory]:
    """The `k` approved memories most relevant to `query` (semantic search on the store index)."""
    if k <= 0 or not query.strip():
        return []
    items = await store.asearch(ns(MEMORIES, user_id), query=query[:500], limit=k)
    return [_memory(i) for i in items if fact_of(i.value)]


async def remember(store: BaseStore, user_id: uuid.UUID, fact: str, *, max_items: int) -> Memory:
    text = await clean_fact(user_id, fact)
    existing = await list_memories(store, user_id)
    for m in existing:
        if m.fact.casefold() == text.casefold():
            return m
    if len(existing) >= max_items:
        raise MemoryFull("memory_full")
    tool = create_manage_memory_tool(ns(MEMORIES, user_id), store=store, actions_permitted=("create",))
    out = str(await tool.ainvoke({"content": text, "action": "create"}))
    found = _ID.search(out)
    key = found.group(1) if found else ""
    log.info("memory.remembered")
    return Memory(key, text, None)


async def forget(store: BaseStore, user_id: uuid.UUID, key: str, kind: str = MEMORIES) -> bool:
    try:
        uuid.UUID(key)
    except ValueError:
        return False
    item = await store.aget(ns(kind, user_id), key)
    if item is None:
        return False
    tool = create_manage_memory_tool(ns(kind, user_id), store=store, actions_permitted=("delete",))
    await tool.ainvoke({"action": "delete", "id": key})
    return True


async def get_memory(store: BaseStore, user_id: uuid.UUID, key: str, kind: str = MEMORIES) -> Memory | None:
    try:
        uuid.UUID(key)
    except ValueError:
        return None
    item = await store.aget(ns(kind, user_id), key)
    return _memory(item) if item is not None else None


async def keep_suggestion(store: BaseStore, user_id: uuid.UUID, key: str, *, max_items: int) -> bool:
    """The student kept a suggested memory: move it to the approved namespace."""
    s = await get_memory(store, user_id, key, SUGGESTIONS)
    if s is None:
        return False
    await remember(store, user_id, s.fact, max_items=max_items)
    await store.adelete(ns(SUGGESTIONS, user_id), key)
    return True


async def reflect(
    store: BaseStore, user_id: uuid.UUID, messages: Sequence[BaseMessage], *, gateway: Any, run_id: str | None
) -> int:
    """langmem's background memory manager over a finished turn: proposes suggestions (never approved memories).
    Its model calls go through llm.py as a `job` (30 % reserve, PII scrubbed), with their own per-run cap, so the
    student's 4-call turn budget is never touched. Returns how many suggestions it wrote or updated."""
    ctx = gateway.context_for_run("job", None, user_id=user_id)
    ctx.run_id = run_id
    ctx.turn = TurnCounter()
    model = gateway.chat_model("structured", ctx)
    manager = create_memory_store_manager(
        model,
        instructions=REFLECT_INSTRUCTIONS,
        namespace=ns(SUGGESTIONS, user_id),
        store=store,
        enable_inserts=True,
        enable_deletes=False,
        query_limit=5,
    )
    out = await manager.ainvoke({"messages": list(messages), "max_steps": 1})  # type: ignore[arg-type]
    written = 0
    for put in out or []:
        key = str(put.get("key") or "") if isinstance(put, dict) else ""
        value = put.get("value") if isinstance(put, dict) else None
        if not key or not isinstance(value, dict):
            continue
        try:
            cleaned = await clean_fact(user_id, fact_of(value))
        except ValueError:
            await store.adelete(ns(SUGGESTIONS, user_id), key)
            continue
        if cleaned != fact_of(value):
            content = value.get("content")
            fixed = {**value, "content": {**content, "content": cleaned} if isinstance(content, dict) else cleaned}
            await store.aput(ns(SUGGESTIONS, user_id), key, fixed)
        written += 1
    return written
