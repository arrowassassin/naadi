"""Unit harness for the tutor graph: a real LLMGateway over scripted chat models, an in-memory checkpointer and
store, and the graph's database touch points replaced by in-test fakes (no network, no database)."""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from pydantic import Field, SecretStr

from naadi_api.agent.graphs import tutor as tutor_mod
from naadi_api.agent.tools.registry import PageRef
from naadi_api.assistant.store_index import hashed_vector
from naadi_api.config_api.schemas import AssistantConfig
from naadi_api.llm import LLMGateway, set_gateway
from naadi_api.llm_privacy import PrivacyContext
from naadi_api.settings import Settings

from ..agent.fakes import Clock, MemLedger
from ..agent.test_llm_unit import StaticRuntime, sections

STUDENT_NAME = "Asha Rao"
STUDENT_EMAIL = "asha.rao@example.com"


class ToolModel(BaseChatModel):
    """Replies from a script: each entry is a string (text) or a list of (tool name, args) calls. Past the end it
    repeats the last entry. Records every prompt and kwargs."""

    script: list[Any] = Field(default_factory=list)
    seen: list[list[BaseMessage]] = Field(default_factory=list)
    seen_kwargs: list[dict[str, Any]] = Field(default_factory=list)
    fail_status: int | None = None

    @property
    def _llm_type(self) -> str:
        return "tool-script"

    def _next(self, messages: list[BaseMessage], kwargs: dict[str, Any]) -> AIMessage:
        self.seen.append(list(messages))
        self.seen_kwargs.append(dict(kwargs))
        if self.fail_status is not None:
            from ..agent.fakes import HTTPStatusError

            raise HTTPStatusError(self.fail_status)
        i = min(len(self.seen) - 1, len(self.script) - 1)
        step = self.script[i] if self.script else "ok"
        if isinstance(step, str):
            return AIMessage(content=step)
        calls = [{"name": n, "args": a, "id": f"call_{uuid.uuid4().hex[:8]}", "type": "tool_call"} for n, a in step]
        if not kwargs.get("tools"):
            return AIMessage(content="(answer without tools)")
        return AIMessage(content="", tool_calls=calls)

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages, kw))])

    async def _agenerate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        return self._generate(messages, stop, None, **kw)

    def _stream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> Iterator[ChatGenerationChunk]:
        msg = self._next(messages, kw)
        if msg.tool_calls:
            chunks = [
                {"name": tc["name"], "args": json.dumps(tc["args"]), "id": tc["id"], "index": i}
                for i, tc in enumerate(msg.tool_calls)
            ]
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=chunks))
            return
        for word in str(msg.content).split(" "):
            yield ChatGenerationChunk(message=AIMessageChunk(content=word + " "))

    async def _astream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> AsyncIterator[ChatGenerationChunk]:
        for c in self._stream(messages, stop, None, **kw):
            yield c


class Clients:
    def __init__(self, models: dict[tuple[str, str], BaseChatModel]) -> None:
        self.models = models
        self.calls: list[tuple[str, str]] = []

    def key_for(self, provider: str) -> SecretStr | None:
        return SecretStr("k")

    async def get(self, provider: str, model: str, *a: Any) -> BaseChatModel:
        self.calls.append((provider, model))
        m = self.models.get((provider, model))
        if m is None:
            m = ToolModel(script=["router-or-summary"])
            self.models[(provider, model)] = m
        return m


class FakePrivacy:
    async def get(self, user_id: Any) -> PrivacyContext:
        return PrivacyContext(names=(STUDENT_NAME,), emails=(STUDENT_EMAIL,))


@dataclass
class World:
    """Everything the graph would read from the database, in memory."""

    exam_active: bool = False
    flags: dict[str, bool] = field(default_factory=lambda: {"assistant": True})
    revealed: set[str] = field(default_factory=set)
    cfg: AssistantConfig = field(default_factory=AssistantConfig)
    thread_rows: list[dict[str, Any]] = field(default_factory=list)


def tutor_chain(*windows: int) -> list[dict[str, Any]]:
    return [
        {"provider": "openrouter", "model": f"lab/tutor-{i}:free", "context_tokens": w} for i, w in enumerate(windows)
    ]


def make_gateway(
    models: dict[tuple[str, str], BaseChatModel], *, tutor_windows: tuple[int, ...] = (200_000,), **patch: Any
) -> tuple[LLMGateway, Clients, MemLedger]:
    values = sections(**patch)
    values = copy.deepcopy(values)
    values["models"]["roles"]["tutor"]["chain"] = tutor_chain(*tutor_windows)
    for role in ("router", "long_context", "structured"):
        values["models"]["roles"][role]["chain"] = [{"provider": "openrouter", "model": f"lab/{role}:free"}]
    clients = Clients(models)
    clock = Clock()
    ledger = MemLedger()
    gw = LLMGateway(
        Settings(_env_file=None),
        StaticRuntime(values),
        ledger,
        clients=clients,
        clock=clock,
        sleep=clock.sleep,
        privacy=FakePrivacy(),  # type: ignore[arg-type]
    )
    return gw, clients, ledger


def tutor_model(clients: Clients, i: int = 0) -> ToolModel:
    m = clients.models[("openrouter", f"lab/tutor-{i}:free")]
    assert isinstance(m, ToolModel)
    return m


USER = uuid.UUID("00000000-0000-4000-8000-0000000000a1")


def run_config(thread: str | None = None, *, admin: bool = False) -> dict[str, Any]:
    tid = thread or str(uuid.uuid4())
    return {
        "configurable": {
            "thread_id": tid,
            "run_id": str(uuid.uuid4()),
            "langgraph_auth_user": {"identity": str(USER), "permissions": ["admin"] if admin else []},
        },
        "recursion_limit": 40,
    }


def install(monkeypatch: pytest.MonkeyPatch, world: World, gw: LLMGateway) -> None:
    set_gateway(gw)

    async def _config() -> tuple[AssistantConfig, dict[str, Any]]:
        return world.cfg, {}

    async def _flags_for(who: Any) -> dict[str, bool]:
        return dict(world.flags)

    async def _exam_active(user_id: Any, rules: Any) -> bool:
        return world.exam_active

    async def verify_page(db: Any, user_id: Any, page: Any, ask_kind: Any) -> PageRef:
        qid = page.question.id if page.question else (page.id if page.type == "question" else None)
        return PageRef(
            route=page.route,
            type=page.type,
            id=page.id,
            title=page.title,
            chapter_id=page.chapter_id,
            video=page.video.model_dump() if page.video else None,
            question_id=qid,
            question_revealed=bool(qid and qid in world.revealed),
            ask_kind=ask_kind,
        )

    async def record_turn(db: Any, **kw: Any) -> bool:
        world.thread_rows.append(kw)
        return False

    monkeypatch.setattr(tutor_mod, "_config", _config)
    monkeypatch.setattr(tutor_mod, "_flags_for", _flags_for)
    monkeypatch.setattr(tutor_mod, "_exam_active", _exam_active)
    monkeypatch.setattr(tutor_mod, "verify_page", verify_page)
    monkeypatch.setattr(tutor_mod.threads, "record_turn", record_turn)
    monkeypatch.setattr(tutor_mod, "agent_db", lambda: object())


def compiled() -> Any:
    store = InMemoryStore(index={"dims": 384, "embed": lambda texts: [hashed_vector(t) for t in texts]})
    return tutor_mod.build().compile(checkpointer=InMemorySaver(), store=store)
