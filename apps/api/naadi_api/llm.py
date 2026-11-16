"""The only module in the API that calls a language model (CLAUDE.md).

Every call goes through `LLMGateway`, which, in this order:

1. rejects vault material (`llm_privacy.assert_no_vault`) and replaces PII with placeholders;
2. takes one call from the turn's budget (`max_llm_calls_per_turn`, a hard stop);
3. walks the role's chain from `app_config.models` (provider × model, in order), skipping a provider whose
   rpm bucket or daily cap (chat 70 % / jobs 30 %, counted in `llm_usage`) is exhausted and a model that
   just answered 404/410; it moves on after 429, 5xx, timeouts and other provider errors;
4. records one `llm_usage` row per attempt (reserved before the request, completed after it) and logs
   role, provider, model, status, tokens, latency and run id: never a prompt or a completion.

When nothing can be called because of the budget, it raises `BudgetExhausted` with the reset time and graphs
degrade to course retrieval. Model IDs come only from configuration; nothing here names a model.

Graphs use `gateway.chat_model(role, ctx)`, a LangChain chat model, so LangGraph streams its tokens
(`stream_mode="messages"`) and `bind_tools` / `with_structured_output` work as with any provider model.

Providers: NVIDIA API catalog through `langchain-nvidia-ai-endpoints` (`ChatNVIDIA`), OpenRouter `:free`
through `langchain-openrouter` (`ChatOpenRouter`). `ChatNVIDIA`'s async client is aiohttp, which ignores
HTTPS_PROXY; its sync client (requests) does not, so NVIDIA calls run the sync client in a worker thread.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_nvidia_ai_endpoints import ChatNVIDIA
from pydantic import BaseModel, PrivateAttr, SecretStr

from .config import ProviderName
from .llm_budget import (
    BudgetExhausted,
    RateBuckets,
    TurnCounter,
    TurnRegistry,
    UsageLedger,
    UsageRow,
    next_utc_midnight,
    purpose_cap,
)
from .llm_config import AgentConfig, ChainEntry, ProviderSpec, Purpose, RuntimeConfig, model_family
from .llm_privacy import PrivacyContext, VaultContentRejected, assert_no_vault, scrub_messages
from .logs import get_logger
from .settings import Settings

log = get_logger("naadi.llm")

PROBE_PROMPT = "Reply with OK."
_STATUS = re.compile(r"\[(\d{3})\]")
_STATUS_WORD = {
    401: "key rejected",
    403: "not available to this key",
    404: "not found",
    410: "retired",
    429: "rate-limited",
}
# A model that answered 404/410 is skipped for this long (retired models stay listed in the catalogs).
DEAD_MODEL_TTL_S = 600.0
# A provider that rejected the key (401) is skipped for this long.
DEAD_PROVIDER_TTL_S = 300.0


class LLMUnavailable(RuntimeError):
    """Every candidate in the chain was tried and failed (not a budget problem)."""

    def __init__(self, role: str, attempts: list[dict[str, Any]]) -> None:
        super().__init__(f"no model in the {role!r} chain answered")
        self.role = role
        self.attempts = attempts


@dataclass
class CallContext:
    """Who is calling and for what. `privacy` is built from the student's profile; never logged."""

    purpose: Purpose
    user_id: uuid.UUID | None = None
    run_id: str | None = None
    privacy: PrivacyContext = field(default_factory=PrivacyContext)
    turn: TurnCounter = field(default_factory=TurnCounter)
    # The reviewer role records `review_not_independent` when it is served by the author's model family.
    author_model: str | None = None


@dataclass(frozen=True)
class Served:
    provider: ProviderName
    model: str
    role: str
    attempts: int
    review_not_independent: bool

    def metadata(self) -> dict[str, Any]:
        return {
            "naadi_provider": self.provider,
            "naadi_model": self.model,
            "naadi_role": self.role,
            "naadi_attempts": self.attempts,
            "review_not_independent": self.review_not_independent,
        }


def http_status_of(exc: BaseException) -> int | None:
    """OpenRouter SDK errors carry `status_code`; ChatNVIDIA raises `Exception("[410] Gone ...")`."""
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    if isinstance(code, int):
        return code
    m = _STATUS.search(str(exc))
    return int(m.group(1)) if m else None


def describe_failure(exc: BaseException) -> str:
    status = http_status_of(exc)
    if status is not None:
        word = _STATUS_WORD.get(status, "server error" if status >= 500 else "request failed")
        return f"HTTP {status} ({word})"
    if isinstance(exc, TimeoutError):
        return "timed out"
    return type(exc).__name__


def _has_pdf(messages: Sequence[BaseMessage]) -> bool:
    for m in messages:
        if isinstance(m.content, list):
            for part in m.content:
                if isinstance(part, dict) and part.get("type") == "file":
                    return True
    return False


def _usage_tokens(msg: BaseMessage | None) -> tuple[int | None, int | None]:
    usage = getattr(msg, "usage_metadata", None) if msg is not None else None
    if not usage:
        return None, None
    return usage.get("input_tokens"), usage.get("output_tokens")


class _Clients:
    """Provider chat-model instances, built once per (provider, model, temperature, max_tokens)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._cache: dict[tuple[str, str, float | None, int | None, float], BaseChatModel] = {}
        self._lock = threading.Lock()

    def key_for(self, provider: ProviderName) -> SecretStr | None:
        return self._settings.nvidia_api_key if provider == "nvidia" else self._settings.openrouter_api_key

    def _build(
        self, provider: ProviderName, model: str, temperature: float | None, max_tokens: int | None, timeout_s: float
    ) -> BaseChatModel:
        key = self.key_for(provider)
        if key is None:
            raise RuntimeError(f"{provider} is not configured")
        if provider == "nvidia":
            return ChatNVIDIA(
                model=model,
                api_key=key.get_secret_value(),
                temperature=temperature,
                max_completion_tokens=max_tokens,
                timeout=timeout_s,
                usage_telemetry_enabled=False,
            )
        from langchain_openrouter import ChatOpenRouter

        return ChatOpenRouter(  # type: ignore[call-arg]  # `model` is the documented alias of `model_name`
            model=model,
            api_key=key,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=int(timeout_s * 1000),
            max_retries=0,  # llm.py owns retries: the next chain entry, not the same endpoint again
            app_url=self._settings.openrouter_app_url,
            app_title=self._settings.openrouter_app_title,
            stream_usage=True,
        )

    async def get(
        self, provider: ProviderName, model: str, temperature: float | None, max_tokens: int | None, timeout_s: float
    ) -> BaseChatModel:
        k = (provider, model, temperature, max_tokens, timeout_s)
        hit = self._cache.get(k)
        if hit is not None:
            return hit
        # ChatNVIDIA may read the catalog over the network while validating an unknown model id: off-loop.
        built = await asyncio.to_thread(self._build, provider, model, temperature, max_tokens, timeout_s)
        with self._lock:
            return self._cache.setdefault(k, built)


async def _thread_stream(
    fn: Callable[[], Iterator[ChatGenerationChunk]], timeout_s: float
) -> AsyncIterator[ChatGenerationChunk]:
    """Run a sync chunk iterator in a worker thread and yield its chunks on the event loop."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    stop = threading.Event()

    def worker() -> None:
        try:
            for chunk in fn():
                if stop.is_set():
                    return
                loop.call_soon_threadsafe(queue.put_nowait, ("chunk", chunk))
            loop.call_soon_threadsafe(queue.put_nowait, ("end", None))
        except BaseException as exc:  # noqa: BLE001 - handed to the consumer, which re-raises it
            loop.call_soon_threadsafe(queue.put_nowait, ("error", exc))

    task = loop.run_in_executor(None, worker)
    try:
        while True:
            async with asyncio.timeout(timeout_s):
                kind, payload = await queue.get()
            if kind == "chunk":
                yield payload
            elif kind == "error":
                raise payload
            else:
                return
    finally:
        stop.set()
        task.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)


class LLMGateway:
    def __init__(
        self,
        settings: Settings,
        runtime: RuntimeConfig,
        ledger: UsageLedger,
        *,
        clients: Any = None,
        clock: Any = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._runtime = runtime
        self._ledger = ledger
        self._clients = clients or _Clients(settings)
        self._buckets = RateBuckets(clock=clock)
        self._clock = clock
        self._sleep = sleep
        self._dead_models: dict[tuple[str, str], float] = {}
        self._dead_providers: dict[str, float] = {}
        self.turns = TurnRegistry()

    # ── public API ──
    def chat_model(self, role: str, ctx: CallContext) -> NaadiChatModel:
        m = NaadiChatModel(role=role)
        m._gateway = self
        m._ctx = ctx
        return m

    def context_for_run(self, purpose: Purpose, run_id: str | None, **kw: Any) -> CallContext:
        turn = self.turns.for_run(run_id) if run_id else TurnCounter()
        return CallContext(purpose=purpose, run_id=run_id, turn=turn, **kw)

    async def config(self) -> AgentConfig:
        return await self._runtime.get()

    async def ainvoke(self, role: str, messages: Sequence[BaseMessage], ctx: CallContext, **kwargs: Any) -> AIMessage:
        result = await self.generate(role, messages, ctx, **kwargs)
        msg = result.generations[0].message
        assert isinstance(msg, AIMessage)
        return msg

    async def generate(
        self, role: str, messages: Sequence[BaseMessage], ctx: CallContext, stop: list[str] | None = None, **kwargs: Any
    ) -> ChatResult:
        gen: ChatGeneration | None = None
        served: Served | None = None
        async for item in self._run(role, messages, ctx, stop=stop, stream=False, kwargs=kwargs):
            if isinstance(item, Served):
                served = item
            elif gen is None:
                gen = item
        assert served is not None
        assert gen is not None
        message = gen.message
        message.response_metadata = {**message.response_metadata, **served.metadata()}
        return ChatResult(generations=[ChatGeneration(message=message, generation_info=gen.generation_info)])

    async def astream(
        self, role: str, messages: Sequence[BaseMessage], ctx: CallContext, stop: list[str] | None = None, **kwargs: Any
    ) -> AsyncIterator[ChatGenerationChunk]:
        served_meta: dict[str, Any] | None = None
        async for item in self._run(role, messages, ctx, stop=stop, stream=True, kwargs=kwargs):
            if isinstance(item, Served):
                served_meta = item.metadata()
            elif isinstance(item, ChatGenerationChunk):
                yield item
        if served_meta is not None:
            yield ChatGenerationChunk(message=AIMessageChunk(content="", response_metadata=served_meta))

    # ── internals ──
    def _openrouter_env_cap(self) -> int | None:
        return self._settings.openrouter_daily_cap

    def _is_dead(self, provider: str, model: str) -> bool:
        now = self._clock()
        until = self._dead_providers.get(provider)
        if until is not None and until > now:
            return True
        until = self._dead_models.get((provider, model))
        return until is not None and until > now

    async def _candidate(
        self, cfg: AgentConfig, role: str, temperature: float | None, max_tokens: int | None, entry: ChainEntry
    ) -> BaseChatModel:
        spec: ProviderSpec = cfg.models.providers[entry.provider]
        return await self._clients.get(entry.provider, entry.model, temperature, max_tokens, spec.timeout_s)

    async def _call(
        self,
        model: BaseChatModel,
        provider: ProviderName,
        messages: list[BaseMessage],
        stop: list[str] | None,
        stream: bool,
        kwargs: dict[str, Any],
        timeout_s: float,
    ) -> AsyncIterator[ChatGeneration]:
        """Stream: `ChatGenerationChunk`s. Otherwise: exactly one `ChatGeneration` (the provider's message)."""
        if provider == "nvidia":
            if stream:
                async for c in _thread_stream(lambda: model._stream(messages, stop=stop, **kwargs), timeout_s):
                    yield c
                return
            async with asyncio.timeout(timeout_s + 5):
                res = await asyncio.to_thread(model._generate, messages, stop, None, **kwargs)
            yield res.generations[0]
            return
        if stream:
            it = model._astream(messages, stop=stop, **kwargs).__aiter__()
            while True:
                try:
                    async with asyncio.timeout(timeout_s):
                        c = await it.__anext__()
                except StopAsyncIteration:
                    return
                yield c
        async with asyncio.timeout(timeout_s):
            res = await model._agenerate(messages, stop=stop, **kwargs)
        yield res.generations[0]

    async def _run(
        self,
        role: str,
        messages: Sequence[BaseMessage],
        ctx: CallContext,
        *,
        stop: list[str] | None,
        stream: bool,
        kwargs: dict[str, Any],
    ) -> AsyncIterator[ChatGeneration | Served]:
        cfg = await self._runtime.get()
        assert_no_vault(messages)
        clean = scrub_messages(messages, ctx.privacy)
        ctx.turn.take(cfg.budget.max_llm_calls_per_turn)
        spec = cfg.models.roles.get(role)
        if spec is None:
            raise KeyError(f"unknown LLM role {role!r}")
        pdf = _has_pdf(clean)
        extra = dict(kwargs)
        attempts: list[dict[str, Any]] = []
        deadline = self._clock() + cfg.budget.rate_wait_s
        while True:
            cap_hit = False
            rate_waits: list[float] = []
            for entry in spec.chain:
                provider = entry.provider
                pspec = cfg.models.providers[provider]
                if self._clients.key_for(provider) is None or self._is_dead(provider, entry.model):
                    continue
                if pdf and provider != "openrouter":
                    continue  # only OpenRouter parses PDFs (file-parser plugin); NVIDIA would reject the part
                bucket = self._buckets.get(provider, pspec.rpm)
                if not await bucket.try_take():
                    rate_waits.append(bucket.wait_s())
                    continue
                row = UsageRow(provider, ctx.purpose, role, entry.model, ctx.user_id, ctx.run_id)
                cap = purpose_cap(
                    cfg, provider, ctx.purpose, self._openrouter_env_cap() if provider == "openrouter" else None
                )
                usage_id = await self._ledger.reserve(row, cap)
                if usage_id is None:
                    cap_hit = True
                    await bucket.refund()
                    continue
                call_kwargs = dict(extra)
                if provider == "openrouter" and pdf:
                    call_kwargs["plugins"] = [{"id": "file-parser", "pdf": {"engine": cfg.budget.pdf_engine}}]
                started = self._clock()
                status: int | None = None
                yielded = False
                last: ChatGeneration | None = None
                try:
                    model = await self._candidate(cfg, role, spec.temperature, spec.max_tokens, entry)
                    served = Served(
                        provider,
                        entry.model,
                        role,
                        len(attempts) + 1,
                        bool(
                            spec.if_family_equals_author
                            and ctx.author_model
                            and model_family(ctx.author_model) == model_family(entry.model)
                        ),
                    )
                    async for chunk in self._call(model, provider, clean, stop, stream, call_kwargs, pspec.timeout_s):
                        if not yielded:
                            yield served
                        yielded = True
                        last = (
                            last + chunk
                            if isinstance(last, ChatGenerationChunk) and isinstance(chunk, ChatGenerationChunk)
                            else chunk
                        )
                        yield chunk
                    if not yielded:
                        raise RuntimeError("empty response")
                    status = 200
                except (VaultContentRejected, BudgetExhausted, asyncio.CancelledError):
                    await self._finish(usage_id, ok=False, status=None, last=None, started=started)
                    raise
                except Exception as exc:  # classified below; the chain moves on
                    status = http_status_of(exc)
                    latency = await self._finish(usage_id, ok=False, status=status, last=last, started=started)
                    attempts.append(
                        {"provider": provider, "model": entry.model, "status": status, "error": describe_failure(exc)}
                    )
                    log.warning(
                        "llm.call",
                        role=role,
                        provider=provider,
                        model=entry.model,
                        ok=False,
                        status=status,
                        error=describe_failure(exc),
                        latency_ms=latency,
                        run_id=ctx.run_id,
                        purpose=ctx.purpose,
                        attempt=len(attempts),
                    )
                    if status in (404, 410):
                        self._dead_models[(provider, entry.model)] = self._clock() + DEAD_MODEL_TTL_S
                    elif status == 401:
                        self._dead_providers[provider] = self._clock() + DEAD_PROVIDER_TTL_S
                    if yielded:
                        # Tokens already reached the client: a second model cannot continue the same text.
                        raise LLMUnavailable(role, attempts) from exc
                    continue
                latency = await self._finish(usage_id, ok=True, status=status, last=last, started=started)
                pt, ct = _usage_tokens(last.message if last else None)
                log.info(
                    "llm.call",
                    role=role,
                    provider=provider,
                    model=entry.model,
                    ok=True,
                    status=status,
                    latency_ms=latency,
                    prompt_tokens=pt,
                    completion_tokens=ct,
                    run_id=ctx.run_id,
                    purpose=ctx.purpose,
                    attempt=len(attempts) + 1,
                )
                return
            if attempts:
                if rate_waits and self._clock() < deadline:
                    await self._sleep(min(min(rate_waits), max(0.0, deadline - self._clock())) + 0.01)
                    continue
                raise LLMUnavailable(role, attempts)
            if rate_waits:
                wait = min(rate_waits)
                if self._clock() + wait <= deadline:
                    await self._sleep(wait + 0.01)
                    continue
                raise BudgetExhausted("rate_limited", datetime.now(UTC) + timedelta(seconds=wait), purpose=ctx.purpose)
            if cap_hit:
                raise BudgetExhausted("daily_cap", next_utc_midnight(), purpose=ctx.purpose)
            raise LLMUnavailable(role, [{"error": "no provider in the chain is configured"}])

    async def _finish(
        self, usage_id: int, *, ok: bool, status: int | None, last: ChatGeneration | None, started: float
    ) -> int:
        latency = int((self._clock() - started) * 1000)
        pt, ct = _usage_tokens(last.message if last else None)
        try:
            await self._ledger.finish(
                usage_id, ok=ok, status=status, prompt_tokens=pt, completion_tokens=ct, latency_ms=latency
            )
        except Exception as exc:  # noqa: BLE001 - the row exists (it counted); a failed update only loses detail
            log.warning("llm.usage_update_failed", error=type(exc).__name__)
        return latency


class NaadiChatModel(BaseChatModel):
    """A LangChain chat model whose every call goes through `LLMGateway` for one role and one context."""

    role: str
    _gateway: LLMGateway | None = PrivateAttr(default=None)
    _ctx: CallContext | None = PrivateAttr(default=None)

    @property
    def _llm_type(self) -> str:
        return "naadi-gateway"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"role": self.role}

    def _parts(self) -> tuple[LLMGateway, CallContext]:
        if self._gateway is None or self._ctx is None:
            raise RuntimeError("NaadiChatModel must be created with LLMGateway.chat_model(role, ctx)")
        return self._gateway, self._ctx

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type[BaseModel] | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        formatted = [convert_to_openai_tool(t) for t in tools]
        if tool_choice:
            if tool_choice == "any":
                tool_choice = "required"
            if tool_choice not in ("auto", "none", "required"):
                kwargs["tool_choice"] = {"type": "function", "function": {"name": tool_choice}}
            else:
                kwargs["tool_choice"] = tool_choice
        return self.bind(tools=formatted, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise TypeError("NaadiChatModel is async-only: use ainvoke/astream (budget guards are async)")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        gateway, ctx = self._parts()
        return await gateway.generate(self.role, messages, ctx, stop=stop, **kwargs)

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        gateway, ctx = self._parts()
        async for chunk in gateway.astream(self.role, messages, ctx, stop=stop, **kwargs):
            yield chunk


# ── Liveness probes (health + check_models): fixed prompt, no user data, one token ──


@dataclass(frozen=True)
class ProbeResult:
    provider: ProviderName
    model: str
    ok: bool
    latency_ms: float | None
    detail: str


def _nvidia_call(model: str, api_key: str, timeout_s: float) -> None:
    llm = ChatNVIDIA(
        model=model,
        api_key=api_key,
        max_completion_tokens=1,
        temperature=0,
        timeout=timeout_s,
        usage_telemetry_enabled=False,
    )
    llm.invoke(PROBE_PROMPT)


async def probe_nvidia(model: str, api_key: SecretStr, timeout_s: float = 15.0) -> ProbeResult:
    """One-token call to `model` on the NVIDIA API catalog. Never raises; failures come back as `ok=False`.

    The sync client runs in a worker thread: its `requests` session honours HTTPS_PROXY (the build
    sandbox), which the SDK's aiohttp client does not. `timeout_s` bounds the HTTP request itself.
    """
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_s + 2.0):
            await asyncio.to_thread(_nvidia_call, model, api_key.get_secret_value(), timeout_s)
    except Exception as exc:  # noqa: BLE001 - every failure is a probe result, never a crash
        latency = round((time.perf_counter() - started) * 1000, 1)
        return ProbeResult("nvidia", model, False, latency, describe_failure(exc))
    latency = round((time.perf_counter() - started) * 1000, 1)
    return ProbeResult("nvidia", model, True, latency, "answers")


_GATEWAY: LLMGateway | None = None


def set_gateway(gw: LLMGateway | None) -> None:
    global _GATEWAY
    _GATEWAY = gw


def get_gateway() -> LLMGateway:
    if _GATEWAY is None:
        raise RuntimeError("the LLM gateway is not initialised (naadi_api.agent.runtime.init_runtime)")
    return _GATEWAY


__all__ = [
    "PROBE_PROMPT",
    "BudgetExhausted",
    "CallContext",
    "LLMGateway",
    "LLMUnavailable",
    "NaadiChatModel",
    "PrivacyContext",
    "ProbeResult",
    "Served",
    "VaultContentRejected",
    "describe_failure",
    "get_gateway",
    "http_status_of",
    "probe_nvidia",
    "set_gateway",
]
