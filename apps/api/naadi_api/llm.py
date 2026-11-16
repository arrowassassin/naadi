"""The only module in the API that calls a language model (CLAUDE.md).

Model IDs come from `config/models.yaml` (never from code). Segment 01 needs just one call: a liveness
probe of each NVIDIA model the role chains route to, used by `/api/health`. The NVIDIA API catalog keeps
listing retired models (docs/MODEL_TESTS.md), so the only reliable check is to call the model. The probe
prompt is a fixed constant with no user data, asks for one token, and telemetry is disabled. Chat, tools,
fallbacks and budget guards arrive with the agent platform (segment 04).
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

from langchain_nvidia_ai_endpoints import ChatNVIDIA
from pydantic import SecretStr

from .config import ProviderName

PROBE_PROMPT = "Reply with OK."
_STATUS = re.compile(r"\[(\d{3})\]")
_STATUS_WORD = {
    401: "key rejected",
    403: "not available to this key",
    404: "not found",
    410: "retired",
    429: "rate-limited",
}


@dataclass(frozen=True)
class ProbeResult:
    provider: ProviderName
    model: str
    ok: bool
    latency_ms: float | None
    detail: str


def http_status_of(exc: BaseException) -> int | None:
    """ChatNVIDIA raises `Exception("[410] Gone ...")`; pull the HTTP status out of the message."""
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


async def probe_nvidia(model: str, api_key: SecretStr, timeout_s: float = 30.0) -> ProbeResult:
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
