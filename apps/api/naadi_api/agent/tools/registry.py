"""The tutor's tool registry (segment 14; contract on the ledger, 20261117T033406).

    register_tool(name, fn, *, writes, requires_flag=None)

- A read tool is `async def fn(ctx: ToolContext, args: ArgsModel) -> ToolResult`; the pydantic model is taken
  from the annotation of the second parameter and the first paragraph of the docstring is what the model reads.
- A write tool is a `WriteTool(prepare, apply)`. `prepare` builds a typed preview and writes nothing; the registry
  raises a LangGraph `interrupt()` with it, and only an `accept` (or an `edit`, re-validated and re-prepared) runs
  `apply`. A bare coroutine cannot be registered with `writes=True`, so no write can skip the confirm.
- `requires_flag` names a `feature_flags` key: the tool is neither bound to the model nor runnable unless the flag
  is on for the run's user, so a later segment's tool stays invisible until that segment ships.

Every result passes `leak.assert_clean` before the model or the client sees it (vault text, PII placeholders
aside, the hidden question fields): a new tool is covered without any extra code.
"""

from __future__ import annotations

import inspect
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal, get_type_hints

import psycopg
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, ValidationError

from ...auth import CurrentUser
from ...auth_db import user_tx
from ...db import Database
from ...llm import CallContext, LLMGateway

ToolStatus = Literal["ok", "error", "declined", "refused"]
NAME = re.compile(r"^[a-z][a-z0-9_]{1,47}$")


@dataclass
class ToolResult:
    """What a tool hands back: `content` is all the model reads; blocks/citations/canvas go to the client."""

    content: str
    blocks: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    canvas: list[dict[str, Any]] = field(default_factory=list)
    status: ToolStatus = "ok"
    # The best course-retrieval score this tool saw (search_knowledge), for the web-search guard.
    kb_best: float | None = None


@dataclass
class Proposal:
    """A write tool's preview. `preview` is shown on the confirm chip (protocol: Preview); `data` is private to
    the tool (resolved ids, the normalised document) and is never sent to the client or the model."""

    preview: dict[str, Any]
    editable: bool = True
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WriteTool:
    prepare: Callable[[ToolContext, Any], Awaitable[Proposal]]
    apply: Callable[[ToolContext, Any, Proposal], Awaitable[ToolResult]]


@dataclass
class PageRef:
    """The server-verified page: ids from the client, every state (answered, chosen) from the database."""

    route: str | None = None
    type: str | None = None
    id: str | None = None
    title: str | None = None
    chapter_id: str | None = None
    video: dict[str, Any] | None = None
    question_id: str | None = None
    # True only when the student has a graded, revealed attempt on `question_id` (tutor mode or finished block).
    question_revealed: bool = False
    chosen: str | None = None
    # The entry point's ask kind (hint / explain / ask / summarise / flashcards / quiz), when there is one.
    ask_kind: str | None = None


@dataclass
class ToolContext:
    user_id: uuid.UUID
    is_admin: bool
    run_id: str | None
    thread_id: str | None
    db: Database
    exam_active: bool
    page: PageRef
    gateway: LLMGateway
    call: CallContext
    flags: dict[str, bool]
    web_allowed: bool = False
    kb_low_score: float = 0.55
    tool_result_max_chars: int = 4000
    store: Any = None
    memory_max_items: int = 100
    # The pages web_extract may read this turn: URLs this turn's web_search returned and URLs the student typed
    # (never one the model built, which injected text could point anywhere). Normalised by `web_url_key`.
    web_urls: set[str] = field(default_factory=set)
    # Filled by tools (search_knowledge) so later tools in the same turn can apply the web guard.
    kb_best: float | None = None

    @property
    def user(self) -> CurrentUser:
        # Aegra's auth handler resolved this account (live session) when the run started.
        return CurrentUser(
            user_id=self.user_id, email=None, email_verified=True, is_admin=self.is_admin, account_checked=True
        )

    @asynccontextmanager
    async def user_tx(self) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        """A transaction as `authenticated` with this user's claims: RLS has the last word."""
        async with user_tx(self.db, self.user, deadline_s=8.0) as conn:
            yield conn

    async def revealed(self, question_ids: Iterable[str]) -> set[str]:
        from ...assistant.questions import revealed_questions

        return await revealed_questions(self.db, self.user_id, list(question_ids))


def web_url_key(url: str) -> str:
    """The form web_extract compares URLs in: scheme and host lower-cased, no fragment, no trailing slash."""
    from urllib.parse import urlsplit, urlunsplit

    u = urlsplit(url.strip())
    return urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path.rstrip("/"), u.query, ""))


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    writes: bool
    requires_flag: str | None
    read: Callable[[ToolContext, Any], Awaitable[ToolResult]] | None = None
    write: WriteTool | None = None

    def openai_schema(self) -> dict[str, Any]:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        return convert_to_openai_tool(
            {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": schema}}
        )


_REGISTRY: dict[str, ToolSpec] = {}


class ToolRegistrationError(TypeError):
    pass


def _args_model(fn: Callable[..., Any]) -> type[BaseModel]:
    params = list(inspect.signature(fn).parameters.values())
    if len(params) < 2:
        raise ToolRegistrationError(f"{fn.__qualname__} must take (ctx, args)")
    hints = get_type_hints(fn)
    model = hints.get(params[1].name)
    if not (isinstance(model, type) and issubclass(model, BaseModel)):
        raise ToolRegistrationError(f"{fn.__qualname__}: the second parameter must be annotated with a pydantic model")
    return model


def _description(fn: Callable[..., Any]) -> str:
    doc = inspect.getdoc(fn) or ""
    first = doc.split("\n\n", 1)[0].strip()
    if not first:
        raise ToolRegistrationError(f"{fn.__qualname__} needs a docstring: its first paragraph is the tool description")
    return " ".join(first.split())


def register_tool(
    name: str,
    fn: Callable[[ToolContext, Any], Awaitable[ToolResult]] | WriteTool,
    *,
    writes: bool,
    requires_flag: str | None = None,
) -> ToolSpec:
    if not NAME.match(name):
        raise ToolRegistrationError(f"tool name {name!r} must be snake_case")
    if name in _REGISTRY:
        raise ToolRegistrationError(f"tool {name!r} is already registered")
    if writes:
        if not isinstance(fn, WriteTool):
            raise ToolRegistrationError(
                f"write tool {name!r} must be a WriteTool(prepare, apply): writes need a confirm"
            )
        spec = ToolSpec(name, _description(fn.prepare), _args_model(fn.prepare), True, requires_flag, write=fn)
    else:
        if isinstance(fn, WriteTool) or not inspect.iscoroutinefunction(fn):
            raise ToolRegistrationError(f"read tool {name!r} must be an async function (ctx, args)")
        spec = ToolSpec(name, _description(fn), _args_model(fn), False, requires_flag, read=fn)
    _REGISTRY[name] = spec
    return spec


def unregister_tool(name: str) -> None:
    """Tests only."""
    _REGISTRY.pop(name, None)


def all_tools() -> list[ToolSpec]:
    return list(_REGISTRY.values())


def get_tool(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def tools_for(flags: Mapping[str, bool]) -> list[ToolSpec]:
    """The tools this user may see: every tool whose flag (if any) is on."""
    return [t for t in _REGISTRY.values() if t.requires_flag is None or flags.get(t.requires_flag, False)]


# ── page resolvers: page kinds other segments own, read by `get_page` ──

PageResolver = Callable[["ToolContext", str], Awaitable[ToolResult]]


@dataclass(frozen=True)
class PageResolverSpec:
    ref_type: str
    fn: PageResolver
    requires_flag: str | None


_PAGE_RESOLVERS: dict[str, PageResolverSpec] = {}
_BUILTIN_PAGE_KINDS = frozenset({"current", "section", "question", "flashcard", "note", "video"})
_PAGE_KIND = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def register_page_resolver(ref_type: str, fn: PageResolver, *, requires_flag: str | None = None) -> None:
    """Let `get_page` read a page kind another segment owns (S15: `pdf_page`, id "{upload_id}:{page}").

    `fn(ctx, ref_id)` reads only through `ctx.user_tx()` (RLS), refuses what the student may not see, and returns
    text within `ctx.tool_result_max_chars` plus its citation. The executor applies the same leak check to its
    result as to every tool. With `requires_flag`, the kind exists only while that feature is on."""
    if not _PAGE_KIND.match(ref_type) or ref_type in _BUILTIN_PAGE_KINDS:
        raise ToolRegistrationError(f"page kind {ref_type!r} is reserved or malformed")
    if ref_type in _PAGE_RESOLVERS and _PAGE_RESOLVERS[ref_type].fn is not fn:
        raise ToolRegistrationError(f"page kind {ref_type!r} is already registered")
    _PAGE_RESOLVERS[ref_type] = PageResolverSpec(ref_type, fn, requires_flag)


def unregister_page_resolver(ref_type: str) -> None:
    """Tests only."""
    _PAGE_RESOLVERS.pop(ref_type, None)


def page_resolver(ref_type: str, flags: Mapping[str, bool]) -> PageResolverSpec | None:
    spec = _PAGE_RESOLVERS.get(ref_type)
    if spec is None or (spec.requires_flag is not None and not flags.get(spec.requires_flag, False)):
        return None
    return spec


def parse_args(spec: ToolSpec, raw: Mapping[str, Any]) -> BaseModel:
    return spec.args_model.model_validate(dict(raw))


__all__ = [
    "PageRef",
    "PageResolver",
    "PageResolverSpec",
    "Proposal",
    "ToolContext",
    "ToolRegistrationError",
    "ToolResult",
    "ToolSpec",
    "ValidationError",
    "WriteTool",
    "all_tools",
    "get_tool",
    "page_resolver",
    "parse_args",
    "register_page_resolver",
    "register_tool",
    "tools_for",
    "unregister_page_resolver",
    "unregister_tool",
    "web_url_key",
]
