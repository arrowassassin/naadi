"""The prompt never overflows the model's context window (segment 14, lead decision).

Before every tutor model call the graph builds the budgeted view of the thread:

    system prompt + page context + retrieved memories   (always kept)
    rolling summary of older turns                      (state.summary, written by the `long_context` role)
    the latest turns, verbatim                          (newest first until the budget is used)

`available = window × (1 − margin) − answer reserve − bound tool schemas`. Older turns are folded into the rolling
summary by langmem's `asummarize_messages` only once the history crosses `summary_trigger × available` (one
`long_context` call, counted in the turn's 4-call cap); otherwise nothing extra is called. Tool results are already
cut to their cited passages by the tool executor.

`fit(messages, window)` is the deterministic part: llm.py runs it again for every candidate in the chain, so a
fallback to a model with a smaller window gets a prompt sized for it (it trims; it never calls a model).

Tokens: the providers publish no tokenizer for these models, so counting is a conservative character estimate
(3 characters per token, plus per-message overhead; images a flat 1,100 tokens each).
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

CHARS_PER_TOKEN = 3.0
MESSAGE_OVERHEAD = 6
IMAGE_TOKENS = 1100
MIN_TOOL_CHARS = 400
SUMMARY_TOKENS = 512


def _content_tokens(content: Any) -> int:
    if isinstance(content, str):
        return math.ceil(len(content) / CHARS_PER_TOKEN)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("image_url", "image"):
                total += IMAGE_TOKENS
            elif isinstance(part, dict):
                total += math.ceil(len(str(part.get("text", ""))) / CHARS_PER_TOKEN)
            else:
                total += math.ceil(len(str(part)) / CHARS_PER_TOKEN)
        return total
    return math.ceil(len(str(content)) / CHARS_PER_TOKEN)


def count_tokens(messages: Sequence[BaseMessage] | BaseMessage) -> int:
    if isinstance(messages, BaseMessage):
        messages = [messages]
    total = 0
    for m in messages:
        total += MESSAGE_OVERHEAD + _content_tokens(m.content)
        if isinstance(m, AIMessage) and m.tool_calls:
            total += math.ceil(len(json.dumps([tc.get("args") for tc in m.tool_calls], default=str)) / CHARS_PER_TOKEN)
            total += 8 * len(m.tool_calls)
    return total


def schema_tokens(schemas: Sequence[dict[str, Any]]) -> int:
    return math.ceil(len(json.dumps(list(schemas), default=str)) / CHARS_PER_TOKEN) if schemas else 0


@dataclass(frozen=True)
class Budget:
    window: int
    margin: float
    answer_reserve: int
    tools: int = 0

    @property
    def available(self) -> int:
        return max(512, int(self.window * (1 - self.margin)) - self.answer_reserve - self.tools)


def _drop_orphans(history: list[BaseMessage]) -> list[BaseMessage]:
    """No tool message without its AI call, no AI tool call without its results, never start on a tool/AI turn."""
    call_ids = {tc["id"] for m in history if isinstance(m, AIMessage) for tc in m.tool_calls if tc.get("id")}
    result_ids = {m.tool_call_id for m in history if isinstance(m, ToolMessage)}
    out: list[BaseMessage] = []
    for m in history:
        if isinstance(m, ToolMessage) and m.tool_call_id not in call_ids:
            continue
        if isinstance(m, AIMessage) and m.tool_calls and not all(tc.get("id") in result_ids for tc in m.tool_calls):
            # An unfinished call (the turn cap stopped it) is kept only as its text.
            if m.content:
                out.append(AIMessage(content=m.content, id=m.id))
            continue
        out.append(m)
    while out and not isinstance(out[0], HumanMessage):
        out.pop(0)
    return out


def _truncate_tool(m: ToolMessage, chars: int) -> ToolMessage:
    text = m.content if isinstance(m.content, str) else str(m.content)
    if len(text) <= chars:
        return m
    return ToolMessage(content=text[: chars - 1] + "…", tool_call_id=m.tool_call_id, name=m.name, id=m.id)


def fit(
    messages: Sequence[BaseMessage], window: int, *, margin: float = 0.15, answer_reserve: int = 2048
) -> list[BaseMessage]:
    """Trim to fit `window`: keep the leading system messages and the newest human turn; shrink the largest tool
    results first, then drop the oldest turns. Deterministic; never calls a model."""
    budget = Budget(window, margin, answer_reserve).available
    msgs = list(messages)
    head: list[BaseMessage] = []
    while msgs and isinstance(msgs[0], SystemMessage):
        head.append(msgs.pop(0))
    if count_tokens(head) + count_tokens(msgs) <= budget:
        return [*head, *msgs]
    # 1. Shrink tool results, largest first, down to MIN_TOOL_CHARS.
    for _ in range(len(msgs)):
        if count_tokens(head) + count_tokens(msgs) <= budget:
            return [*head, *msgs]
        tools = [
            (i, m) for i, m in enumerate(msgs) if isinstance(m, ToolMessage) and len(str(m.content)) > MIN_TOOL_CHARS
        ]
        if not tools:
            break
        i, m = max(tools, key=lambda t: len(str(t[1].content)))
        msgs[i] = _truncate_tool(m, max(MIN_TOOL_CHARS, len(str(m.content)) // 2))
    # 2. Drop the oldest messages, keeping at least the newest human turn and what follows it.
    last_human = max((i for i, m in enumerate(msgs) if isinstance(m, HumanMessage)), default=0)
    while count_tokens(head) + count_tokens(msgs) > budget and last_human > 0:
        msgs.pop(0)
        last_human -= 1
        msgs = _drop_orphans(msgs)
        last_human = max((i for i, m in enumerate(msgs) if isinstance(m, HumanMessage)), default=0)
    # 3. Still too big (one huge turn): shrink the head's long parts (page/context system message) last.
    if count_tokens(head) + count_tokens(msgs) > budget and len(head) > 1:
        spare = max(1, budget - count_tokens([head[0], *msgs]))
        rest = "\n".join(str(h.content) for h in head[1:])
        head = [head[0], SystemMessage(content=rest[: int(spare * CHARS_PER_TOKEN)])]
    if count_tokens(head) + count_tokens(msgs) > budget:
        # The newest message alone is larger than the window: keep its tail.
        last = msgs[-1] if msgs else None
        if isinstance(last, HumanMessage) and isinstance(last.content, str):
            spare = max(64, budget - count_tokens(head) - MESSAGE_OVERHEAD)
            msgs = [HumanMessage(content=last.content[-int(spare * CHARS_PER_TOKEN) :], id=last.id)]
    return [*head, *msgs]


def history_for_model(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """The thread's messages as the model may see them (orphan tool calls removed, summaries not yet applied)."""
    return _drop_orphans([m for m in messages if not isinstance(m, SystemMessage)])


def summary_message(summary: str | None) -> list[SystemMessage]:
    if not summary:
        return []
    return [SystemMessage(content="Summary of the earlier conversation (for context):\n" + summary)]
