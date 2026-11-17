"""Runs one tool call for the tutor graph: checks, the confirm interrupt for writes, and the leak checks.

Interrupt rule: a graph node that raised `interrupt()` runs again from the top when it is resumed, so the tutor's
`tools` node runs at most ONE write tool per pass (`plan_pass`). Read tools before it run again on resume (they
are idempotent); the write's `apply` runs exactly once, after the student's decision.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from langgraph.types import interrupt
from pydantic import BaseModel, ValidationError

from ...assistant.leak import LeakBlocked, check_payload, scrub_payload
from ...logs import get_logger
from .registry import ToolContext, ToolResult, ToolSpec, get_tool, parse_args

log = get_logger("naadi.agent.tools")

Decision = Literal["accept", "edit", "cancel"]


class ToolRefused(PermissionError):
    """Raised by a tool (prepare/read) when the request is not allowed; `reason` is a structured code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def plan_pass(calls: Sequence[Mapping[str, Any]], done_ids: set[str]) -> list[Mapping[str, Any]]:
    """The pending calls to run in this pass: every read up to and including the first write."""
    out: list[Mapping[str, Any]] = []
    for c in calls:
        if c.get("id") in done_ids:
            continue
        out.append(c)
        spec = get_tool(str(c.get("name")))
        if spec is not None and spec.writes:
            break
    return out


def _decision(answer: Any) -> tuple[Decision, dict[str, Any]]:
    if isinstance(answer, Mapping):
        d = answer.get("decision")
        if d in ("accept", "edit", "cancel"):
            args = answer.get("args")
            return d, dict(args) if isinstance(args, Mapping) else {}
    return "cancel", {}  # anything unexpected is a "no": nothing is written


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _status_result(status: Literal["error", "refused", "declined"], code: str) -> ToolResult:
    # The model reads a short machine-style line; the client shows nothing from it.
    return ToolResult(content=f"[{status}: {code}]", status=status)


async def run_tool_call(ctx: ToolContext, name: str, raw_args: Mapping[str, Any], allowed: set[str]) -> ToolResult:
    spec: ToolSpec | None = get_tool(name)
    if spec is None or name not in allowed:
        return _status_result("error", "unknown_tool")
    if ctx.exam_active:
        return _status_result("refused", "exam_mode")
    try:
        args: BaseModel = parse_args(spec, raw_args)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        return _status_result("error", "invalid_arguments:" + ",".join(fields)[:200])
    try:
        if not spec.writes:
            assert spec.read is not None
            result = await spec.read(ctx, args)
        else:
            assert spec.write is not None
            proposal = await spec.write.prepare(ctx, args)
            check_payload(proposal.preview, where=f"{name}.preview")
            answer = interrupt(
                {
                    "type": "confirm",
                    "tool": name,
                    "action": name,
                    "preview": scrub_payload(proposal.preview),
                    "editable": proposal.editable,
                }
            )
            decision, edited = _decision(answer)
            if decision == "cancel":
                log.info("tool.declined", tool=name, run_id=ctx.run_id)
                return ToolResult(content="[declined: the student cancelled; nothing was saved]", status="declined")
            if decision == "edit":
                if not proposal.editable:
                    return ToolResult(content="[declined: this change cannot be edited]", status="declined")
                try:
                    args = parse_args(spec, {**args.model_dump(mode="json"), **edited})
                except ValidationError:
                    return _status_result("error", "invalid_edit")
                proposal = await spec.write.prepare(ctx, args)
            result = await spec.write.apply(ctx, args, proposal)
            log.info("tool.applied", tool=name, run_id=ctx.run_id, edited=decision == "edit")
        _check_result(name, result)
    except ToolRefused as exc:
        return _status_result("refused", exc.reason)
    except LeakBlocked as exc:
        log.error("tool.leak_blocked", tool=name, where=str(exc).split(" in ", 1)[-1])
        return _status_result("error", "blocked")
    result.content = _truncate(scrub_payload(result.content), ctx.tool_result_max_chars)
    result.blocks = scrub_payload(result.blocks)
    result.citations = scrub_payload(result.citations)
    result.canvas = scrub_payload(result.canvas)
    return result


def _check_result(name: str, result: ToolResult) -> None:
    for i, b in enumerate(result.blocks):
        check_payload(b, where=f"{name}.blocks[{i}]")
    for i, c in enumerate(result.citations):
        check_payload(c, where=f"{name}.citations[{i}]")
    for i, c in enumerate(result.canvas):
        check_payload(c, where=f"{name}.canvas[{i}]")
    check_payload(result.content, where=f"{name}.content")
