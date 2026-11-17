"""The eval's `LLMGateway`, built like the app's (`naadi_api.agent.runtime.init_runtime`): Settings + RuntimeConfig
over `config/models.yaml` + a usage ledger. The app's ledger is `llm_usage` in Postgres; the eval runs without a
database, so `MemoryUsageLedger` implements the same `UsageLedger` interface in memory. The gateway still applies
every guard itself: per-provider rpm buckets, the daily cap for purpose `job` (30 % of each provider's cap,
including OPENROUTER_DAILY_CAP), the per-turn call cap, PII scrubbing and the vault check.

The day's count survives between local runs through a small JSON file (`--usage-file`, default
`.tmp/model_eval_usage.json`, gitignored; counts only, per UTC day), so two runs on one day share one job budget.
It cannot see the app's own calls in `llm_usage`; a provider that is out of quota answers 429 and llm.py moves on.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from naadi_api.llm import LLMGateway
from naadi_api.llm_budget import UsageRow, provider_daily_cap, purpose_cap
from naadi_api.llm_config import AgentConfig, ProviderName, Purpose, RuntimeConfig
from naadi_api.settings import Settings


def utc_day() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


@dataclass
class _Row:
    row: UsageRow
    ok: bool | None = None
    status: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None


@dataclass
class MemoryUsageLedger:
    """`naadi_api.llm_budget.UsageLedger` in memory. `prior` holds today's earlier counts per (provider, purpose).
    Single process under asyncio: there is no await between the count and the insert, so two concurrent calls
    can never both take the last slot."""

    prior: dict[tuple[str, str], int] = field(default_factory=dict)
    day: str = field(default_factory=utc_day)
    rows: dict[int, _Row] = field(default_factory=dict)
    _ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    def _used(self, provider: str, purpose: str | None) -> int:
        earlier = sum(n for (p, u), n in self.prior.items() if p == provider and (purpose is None or u == purpose))
        now = sum(
            1
            for r in self.rows.values()
            if r.row.provider == provider and (purpose is None or r.row.purpose == purpose)
        )
        return earlier + now

    async def reserve(self, row: UsageRow, cap: int) -> int | None:
        if cap <= 0 or self._used(row.provider, row.purpose) >= cap:
            return None
        i = next(self._ids)
        self.rows[i] = _Row(row)
        return i

    async def finish(
        self,
        usage_id: int,
        *,
        ok: bool,
        status: int | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        latency_ms: int,
    ) -> None:
        r = self.rows[usage_id]
        r.ok, r.status, r.latency_ms = ok, status, latency_ms
        r.prompt_tokens, r.completion_tokens = prompt_tokens, completion_tokens

    async def count_today(self, provider: ProviderName, purpose: Purpose | None = None) -> int:
        return self._used(provider, purpose)

    def counts(self) -> dict[tuple[str, str], int]:
        out = dict(self.prior)
        for r in self.rows.values():
            k = (r.row.provider, r.row.purpose)
            out[k] = out.get(k, 0) + 1
        return out

    def summary(self) -> dict[str, Any]:
        """This run's attempts per role/provider (ok and failed), for the report. Never prompts or completions."""
        out: dict[str, dict[str, int]] = {}
        for r in self.rows.values():
            d = out.setdefault(f"{r.row.role}:{r.row.provider}", {"attempts": 0, "ok": 0, "failed": 0})
            d["attempts"] += 1
            d["ok" if r.ok else "failed"] += 1
        return out

    @classmethod
    def load(cls, path: Path | None) -> MemoryUsageLedger:
        day = utc_day()
        if path is None or not path.is_file():
            return cls(day=day)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls(day=day)
        if raw.get("day") != day:
            return cls(day=day)
        prior = {(str(p), str(u)): int(n) for p, by in (raw.get("counts") or {}).items() for u, n in dict(by).items()}
        return cls(prior=prior, day=day)

    def save(self, path: Path | None) -> None:
        if path is None:
            return
        counts: dict[str, dict[str, int]] = {}
        for (p, u), n in self.counts().items():
            counts.setdefault(p, {})[u] = n
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"day": self.day, "counts": counts}, indent=1) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class EvalGateway:
    gateway: LLMGateway
    ledger: MemoryUsageLedger
    runtime: RuntimeConfig
    settings: Settings


def build_gateway(
    settings: Settings,
    *,
    clients: Any = None,
    clock: Any = None,
    sleep: Any = None,
    ledger: MemoryUsageLedger | None = None,
) -> EvalGateway:
    """`clock`/`sleep` replace the monotonic clock only for offline fakes (a virtual clock, so the rpm buckets
    still gate every call but a dry run does not wait in real time)."""
    runtime = RuntimeConfig(None, settings.config_dir)  # no database: config/models.yaml is the source
    led = ledger or MemoryUsageLedger()
    kw: dict[str, Any] = {}
    if clock is not None:
        kw["clock"] = clock
    if sleep is not None:
        kw["sleep"] = sleep
    gw = LLMGateway(settings, runtime, led, clients=clients, **kw)
    return EvalGateway(gw, led, runtime, settings)


def job_caps(cfg: AgentConfig, settings: Settings) -> dict[str, int]:
    """Calls purpose `job` may make in a day, per provider (what the gateway enforces)."""
    out: dict[str, int] = {}
    for provider in cfg.models.providers:
        env = settings.openrouter_daily_cap if provider == "openrouter" else None
        out[provider] = purpose_cap(cfg, provider, "job", env)
    return out


def day_caps(cfg: AgentConfig, settings: Settings) -> dict[str, int]:
    out: dict[str, int] = {}
    for provider in cfg.models.providers:
        env = settings.openrouter_daily_cap if provider == "openrouter" else None
        out[provider] = provider_daily_cap(cfg, provider, env)
    return out


__all__ = [
    "EvalGateway",
    "MemoryUsageLedger",
    "build_gateway",
    "day_caps",
    "job_caps",
    "utc_day",
]
