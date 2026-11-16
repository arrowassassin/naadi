"""langchain-openrouter 0.2.9 on the openrouter 1.3.179 SDK (uv override, review item 6), through llm.py with the
HTTP layer recorded (respx): streaming, tool calls, the PDF file-parser plugin and stream_options reach the
wire in the shape OpenRouter documents, and come back as LangChain messages. One live call is in
test_aegra_live.py; these pin the surface the override relies on without spending quota."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, SecretStr

from naadi_api.llm import LLMGateway
from naadi_api.llm_config import SECTION_MODELS, AgentConfig, RuntimeConfig, load_yaml_sections
from naadi_api.settings import Settings

from .fakes import MemLedger

CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"
URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "vendor/model:free"


class OneRole(RuntimeConfig):
    def __init__(self) -> None:
        super().__init__(None, CONFIG_DIR)
        s = load_yaml_sections(CONFIG_DIR)
        s["models"]["roles"]["reviewer"]["chain"] = [{"provider": "openrouter", "model": MODEL}]
        self._snap = AgentConfig(
            models=SECTION_MODELS["models"].model_validate(s["models"]),  # type: ignore[arg-type]
            budget=SECTION_MODELS["budget"].model_validate(s["budget"]),  # type: ignore[arg-type]
            search=SECTION_MODELS["search"].model_validate(s["search"]),  # type: ignore[arg-type]
            generation=SECTION_MODELS["generation"].model_validate(s["generation"]),  # type: ignore[arg-type]
            source=dict.fromkeys(s, "yaml"),
        )

    async def get(self) -> AgentConfig:
        return self._snap


def gw() -> LLMGateway:
    settings = Settings(
        _env_file=None,
        openrouter_api_key=SecretStr("sk-or-v1-test-only"),
        openrouter_app_url="https://naadi.example",
        openrouter_app_title="Naadi",
    )
    return LLMGateway(settings, OneRole(), MemLedger())


def sse(*events: dict[str, Any]) -> httpx.Response:
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def chunk(delta: dict[str, Any], finish: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "id": "gen-1",
        "object": "chat.completion.chunk",
        "created": 1_763_300_000,
        "model": MODEL,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        **extra,
    }


class Organ(BaseModel):
    """Report one organ."""

    name: str


@respx.mock
async def test_streaming_text_with_stream_options_and_attribution() -> None:
    route = respx.post(URL).mock(
        return_value=sse(
            chunk({"role": "assistant", "content": "The "}),
            chunk({"content": "kidney."}, finish="stop"),
            {
                "id": "gen-1",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": MODEL,
                "choices": [],
                "usage": {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
            },
        )
    )
    g = gw()
    model = g.chat_model("reviewer", g.context_for_run("job", "r1"))
    parts = [c async for c in model.astream([HumanMessage("Name an organ.")])]
    assert "".join(str(p.content) for p in parts) == "The kidney."
    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == MODEL and sent["stream"] is True
    assert sent["stream_options"] == {"include_usage": True}
    headers = route.calls.last.request.headers
    assert headers["http-referer"] == "https://naadi.example"
    assert headers["x-title"] == "Naadi"
    assert headers["authorization"] == "Bearer sk-or-v1-test-only"


@respx.mock
async def test_streamed_tool_call_comes_back_as_a_langchain_tool_call() -> None:
    route = respx.post(URL).mock(
        return_value=sse(
            chunk(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"index": 0, "id": "call_1", "type": "function", "function": {"name": "Organ", "arguments": ""}}
                    ],
                }
            ),
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"name": "kid'}}]}),
            chunk({"tool_calls": [{"index": 0, "function": {"arguments": 'ney"}'}}]}, finish="tool_calls"),
        )
    )
    g = gw()
    model = g.chat_model("reviewer", g.context_for_run("job", "r2")).bind_tools([Organ], tool_choice="Organ")
    final = None
    async for c in model.astream([HumanMessage("Call Organ.")]):
        final = c if final is None else final + c
    assert final is not None
    assert final.tool_calls == [{"name": "Organ", "args": {"name": "kidney"}, "id": "call_1", "type": "tool_call"}]
    sent = json.loads(route.calls.last.request.content)
    assert sent["tools"][0]["function"]["name"] == "Organ"
    assert sent["tool_choice"] == {"type": "function", "function": {"name": "Organ"}}


@respx.mock
async def test_pdf_sends_the_free_file_parser_engine() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "gen-2",
                "object": "chat.completion",
                "created": 1,
                "model": MODEL,
                "system_fingerprint": None,  # always present in OpenRouter responses (may be null); the SDK requires it
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "A summary."}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 3, "total_tokens": 53},
            },
        )
    )
    g = gw()
    msg = HumanMessage(
        content=[
            {"type": "text", "text": "Summarise this PDF."},
            {
                "type": "file",
                "file": {"filename": "notes.pdf", "file_data": "data:application/pdf;base64,JVBERi0xLjQK"},
            },
        ]
    )
    out = await g.ainvoke("reviewer", [msg], g.context_for_run("job", "r3"))
    assert out.content == "A summary."
    sent = json.loads(route.calls.last.request.content)
    assert sent["plugins"] == [{"id": "file-parser", "pdf": {"engine": "cloudflare-ai"}}]
    assert out.usage_metadata is not None and out.usage_metadata["input_tokens"] == 50


@respx.mock
async def test_http_429_falls_through_as_a_provider_failure() -> None:
    respx.post(URL).mock(return_value=httpx.Response(429, json={"error": {"code": 429, "message": "rate limited"}}))
    g = gw()
    from naadi_api.llm import LLMUnavailable

    with pytest.raises(LLMUnavailable) as e:
        await g.ainvoke("reviewer", [HumanMessage("x")], g.context_for_run("job", "r4"))
    assert e.value.attempts[0]["status"] == 429
