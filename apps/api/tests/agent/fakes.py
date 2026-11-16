"""Unit-test doubles for the LLM gateway (recorded behaviour only; integration tests use the real APIs)."""

from __future__ import annotations

import itertools
from collections.abc import AsyncIterator, Iterator
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field, SecretStr

from naadi_api.llm_budget import UsageRow


class HTTPStatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class ScriptedModel(BaseChatModel):
    """Answers `reply`, or raises `HTTPStatusError(fail_status)`; records every prompt it receives."""

    reply: str = "ok"
    fail_status: int | None = None
    seen: list[list[BaseMessage]] = Field(default_factory=list)
    seen_kwargs: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _check(self, messages: list[BaseMessage], kwargs: dict[str, Any]) -> None:
        self.seen.append(list(messages))
        self.seen_kwargs.append(dict(kwargs))
        if self.fail_status is not None:
            raise HTTPStatusError(self.fail_status)

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        self._check(messages, kw)
        msg = AIMessage(content=self.reply, usage_metadata={"input_tokens": 3, "output_tokens": 1, "total_tokens": 4})
        return ChatResult(generations=[ChatGeneration(message=msg)])

    async def _agenerate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        return self._generate(messages, stop, None, **kw)

    def _stream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> Iterator[ChatGenerationChunk]:
        self._check(messages, kw)
        for word in self.reply.split(" "):
            yield ChatGenerationChunk(message=AIMessageChunk(content=word + " "))

    async def _astream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> AsyncIterator[ChatGenerationChunk]:
        for c in self._stream(messages, stop, None, **kw):
            yield c


class FakeClients:
    """Stand-in for llm._Clients: one ScriptedModel per (provider, model)."""

    def __init__(self, models: dict[tuple[str, str], ScriptedModel], keys: dict[str, bool] | None = None) -> None:
        self.models = models
        self.keys = keys or {"nvidia": True, "openrouter": True}
        self.calls: list[tuple[str, str]] = []

    def key_for(self, provider: str) -> SecretStr | None:
        return SecretStr("k") if self.keys.get(provider) else None

    async def get(
        self, provider: str, model: str, temperature: Any, max_tokens: Any, timeout_s: float
    ) -> BaseChatModel:
        self.calls.append((provider, model))
        m = self.models.get((provider, model))
        if m is None:
            m = ScriptedModel(reply=f"{provider}:{model}", seen=[], seen_kwargs=[])
            self.models[(provider, model)] = m
        return m


class MemLedger:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}
        self._ids = itertools.count(1)

    async def reserve(self, row: UsageRow, cap: int) -> int | None:
        used = sum(
            1 for r in self.rows.values() if r["row"].provider == row.provider and r["row"].purpose == row.purpose
        )
        if used >= cap:
            return None
        i = next(self._ids)
        self.rows[i] = {"row": row, "ok": None}
        return i

    async def finish(self, usage_id: int, **kw: Any) -> None:
        self.rows[usage_id].update(kw)

    async def count_today(self, provider: str, purpose: str | None = None) -> int:
        return sum(
            1
            for r in self.rows.values()
            if r["row"].provider == provider and (purpose is None or r["row"].purpose == purpose)
        )


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.t += s
