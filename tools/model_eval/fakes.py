"""Offline stand-ins for `--dry-run` (CI, unit tests): provider clients whose chat models answer from simple,
deterministic rules, so the whole pipeline (retrieval, gateway guards, safety checks, grading, report) runs with no
network and no model files. A dry run measures nothing about any real model and never claims to.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import SecretStr

from .grader import GRADER_MARK

Responder = Callable[[list[BaseMessage]], str]
_PASSAGE = re.compile(r'<passage id="([^"]+)">\n(.*?)\n</passage>', re.S)
_OPTION = re.compile(r"^([A-J])\. (.*)$", re.M)
_NAMES = re.compile(r"Criterion names: (\[.*\])")


def _text(m: BaseMessage) -> str:
    return m.content if isinstance(m.content, str) else json.dumps(m.content)


def dry_responder(messages: list[BaseMessage]) -> str:
    """Grader → valid JSON (score 4 on every criterion). MCQ → the option sharing most words with the passages.
    Tutor → a cautious, cited reply."""
    system = _text(messages[0]) if messages else ""
    human = _text(messages[-1]) if messages else ""
    if GRADER_MARK in system:
        found = _NAMES.search(_text(messages[1]) if len(messages) > 1 else human)
        names = json.loads(found.group(1)) if found else []
        return json.dumps(
            {
                "scores": {n: {"score": 4, "reason": "dry run: fixed score"} for n in names},
                "unsafe": False,
                "unsafe_reason": None,
            }
        )
    passages = _PASSAGE.findall(human)
    if "Answer: <letter>" in human:
        context = " ".join(t for _, t in passages).casefold()
        words = set(re.findall(r"[a-z0-9]+", context))
        best, best_score = "A", -1
        for letter, text in _OPTION.findall(human.split("Options:", 1)[-1]):
            score = len(set(re.findall(r"[a-z0-9]+", text.casefold())) & words)
            if score > best_score:
                best, best_score = letter, score
        return f"Dry run.\nAnswer: {best}"
    cite = f" [ctx:{passages[0][0]}]" if passages else ""
    return (
        "Dry run reply. Here is a hint about the concept rather than an answer" + cite + ". I don't know more than "
        "the course material provided says; for anything about your own health please see a doctor."
    )


class DryRunModel(BaseChatModel):
    responder: Callable[..., str] = dry_responder

    @property
    def _llm_type(self) -> str:
        return "model-eval-dry-run"

    def _result(self, messages: list[BaseMessage]) -> ChatResult:
        reply = self.responder(list(messages))
        msg = AIMessage(content=reply, usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        return self._result(messages)

    async def _agenerate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        return self._result(messages)

    def _stream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> Iterator[ChatGenerationChunk]:
        yield ChatGenerationChunk(message=AIMessageChunk(content=self.responder(list(messages))))

    async def _astream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> AsyncIterator[ChatGenerationChunk]:
        for c in self._stream(messages):
            yield c


class VirtualClock:
    """Monotonic clock + sleep for the gateway in a dry run: sleeping advances time instantly."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start
        self.slept = 0.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.t += max(0.0, s)
        self.slept += max(0.0, s)


class DryRunClients:
    """Stands in for `naadi_api.llm._Clients`: every (provider, model) answers through `responder`."""

    def __init__(self, responder: Responder = dry_responder, keys: dict[str, bool] | None = None) -> None:
        self.responder = responder
        self.keys = keys or {"nvidia": True, "openrouter": True}
        self.calls: list[tuple[str, str]] = []
        self.seen: list[list[BaseMessage]] = []

    def key_for(self, provider: str) -> SecretStr | None:
        return SecretStr("dry-run") if self.keys.get(provider) else None

    async def get(
        self, provider: str, model: str, temperature: Any, max_tokens: Any, timeout_s: float
    ) -> BaseChatModel:
        self.calls.append((provider, model))

        def respond(messages: list[BaseMessage]) -> str:
            self.seen.append(messages)
            return self.responder(messages)

        return DryRunModel(responder=respond)


__all__ = ["DryRunClients", "DryRunModel", "VirtualClock", "dry_responder"]
