"""A scripted, OpenAI/OpenRouter-compatible chat endpoint for the e2e suite (loopback only, never in production).

The e2e API server points OpenRouter at it (`NAADI_OPENROUTER_BASE_URL`, honoured only outside prod and only for a
loopback URL), so the tutor runs end to end through the real gateway, ChatOpenRouter streaming, tools, interrupts
and the Aegra runtime, with deterministic answers chosen by a keyword in the student's latest message:

- `e2e:answer` → calls search_knowledge, then streams an answer citing [1];
- `e2e:save`   → calls save_note (the confirm chip), then confirms or acknowledges the cancel;
- `e2e:canvas` → calls open_in_canvas with a document draft;
- anything else → a short text answer.

Requests that are not the tutor (no tutor system prompt: the ping self-test, the router, the summariser) get 404
"model not found": the gateway skips that model at once (the SDK would retry a 5xx with backoff), so the agent
runtime's own e2e checks keep seeing "unavailable" exactly as without a provider.

    uv run python tests/e2e_fake_llm.py --port 57863
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

TUTOR_MARK = "You are Naadi, a study assistant"


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return ""


def decide(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """{"text": str} or {"tool": name, "args": {...}}."""
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    user = _text(messages[last_user].get("content")) if last_user >= 0 else ""
    after = messages[last_user + 1 :]
    tool_results = [_text(m.get("content")) for m in after if m.get("role") == "tool"]
    if "e2e:answer" in user:
        if not tool_results:
            return {"tool": "search_knowledge", "args": {"query": "renal tubular acidosis"}}
        return {"text": "Distal (type 1) RTA cannot acidify the urine, so urine pH stays above 5.5 [1]."}
    if "e2e:save" in user:
        if not tool_results:
            return {
                "tool": "save_note",
                "args": {"markdown": "**Winters' formula**: expected PCO2 = 1.5 x HCO3 + 8 (+/- 2)"},
            }
        if any("declined" in r for r in tool_results):
            return {"text": "Okay, I did not save anything."}
        return {"text": "Saved the note on this page."}
    if "e2e:canvas" in user:
        if not tool_results:
            return {
                "tool": "open_in_canvas",
                "args": {
                    "kind": "document",
                    "title": "Acid-base summary",
                    "markdown": "## Acid-base\n\n- Read the pH\n- Find the primary process\n- Check compensation",
                },
            }
        return {"text": "I opened a draft in the canvas. Keep it to save it as your note."}
    return {"text": "Here is a short answer for your question."}


def _chunk(cid: str, model: str, delta: dict[str, Any], finish: str | None = None) -> str:
    body = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(body)}\n\n"


async def completions(request: Request) -> Response:
    body = await request.json()
    messages = body.get("messages") or []
    system = " ".join(_text(m.get("content")) for m in messages if m.get("role") == "system")
    if TUTOR_MARK not in system:
        return JSONResponse({"error": {"code": 404, "message": "not found"}}, status_code=404)
    model = str(body.get("model") or "e2e/fake:free")
    plan = decide(messages)
    cid = "chatcmpl-" + uuid.uuid4().hex[:12]
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    tools_bound = bool(body.get("tools"))
    if "tool" in plan and not tools_bound:
        plan = {"text": "Here is what I found."}
    if not body.get("stream"):
        if "tool" in plan:
            message: dict[str, Any] = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_" + uuid.uuid4().hex[:8],
                        "type": "function",
                        "function": {"name": plan["tool"], "arguments": json.dumps(plan["args"])},
                    }
                ],
            }
            finish = "tool_calls"
        else:
            message, finish = {"role": "assistant", "content": plan["text"]}, "stop"
        return JSONResponse(
            {
                "id": cid,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": usage,
            }
        )

    async def stream() -> AsyncIterator[str]:
        yield _chunk(cid, model, {"role": "assistant", "content": ""})
        if "tool" in plan:
            call = {
                "index": 0,
                "id": "call_" + uuid.uuid4().hex[:8],
                "type": "function",
                "function": {"name": plan["tool"], "arguments": json.dumps(plan["args"])},
            }
            yield _chunk(cid, model, {"tool_calls": [call]})
            yield _chunk(cid, model, {}, "tool_calls")
        else:
            words = plan["text"].split(" ")
            for i, w in enumerate(words):
                yield _chunk(cid, model, {"content": w + (" " if i < len(words) - 1 else "")})
            yield _chunk(cid, model, {}, "stop")
        final = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [],
            "usage": usage,
        }
        yield f"data: {json.dumps(final)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


async def health(_: Request) -> Response:
    return JSONResponse({"ok": True})


app = Starlette(
    routes=[
        Route("/api/v1/chat/completions", completions, methods=["POST"]),
        Route("/chat/completions", completions, methods=["POST"]),
        Route("/health", health),
    ]
)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, required=True)
    args = p.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
