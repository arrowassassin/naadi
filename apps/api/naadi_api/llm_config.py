"""Schemas and the runtime reader for the agent platform's `app_config` keys.

Four keys belong to the agent platform: `models` (providers + per-role chains), `budget`, `search` and
`generation`. They are seeded from `config/models.yaml` (segment 05) and edited by admins through the
`app_config` API, which validates every write with `validate_section`. Model IDs never appear in code:
everything that routes, limits or prices a call is read from here.

Runtime reads go through `RuntimeConfig`: the database row wins, cached for 60 s; a missing or invalid row
falls back to the YAML file (an invalid row is logged and reported by `/api/health` as degraded, never
silently used).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .db import Database
from .logs import get_logger

log = get_logger("naadi.llm_config")

ProviderName = Literal["nvidia", "openrouter"]
Purpose = Literal["chat", "job"]
AGENT_CONFIG_KEYS: tuple[str, ...] = ("models", "budget", "search", "generation")
CACHE_TTL_S = 60.0
FREE_ROUTER = "openrouter/free"
# Roles in models.yaml that are not chat chains; they move to top-level keys of the `models` section.
NON_CHAT_ROLES = ("embeddings", "speech_to_text")


def is_free_openrouter_id(model: str) -> bool:
    return model == FREE_ROUTER or (model.endswith(":free") and len(model) > len(":free"))


def model_family(model: str) -> str:
    """`nvidia/nemotron-…` → `nvidia`; the reviewer must come from a different family than the author."""
    return model.split("/", 1)[0].lower() if "/" in model else model.lower()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)


class ProviderSpec(_Strict):
    key_env: str = Field(min_length=1, max_length=64)
    rpm: int = Field(gt=0, le=10_000)
    daily_cap: int | None = Field(default=None, ge=0)
    timeout_s: float = Field(default=60.0, gt=0, le=600)
    sdk: str | None = None
    probe_before_use: bool = False
    only_free_ids: bool = False
    # /api/health one-token probe deadline (naadi_api/config.py reads the same key for the health check).
    probe_timeout_s: float | None = Field(default=None, gt=0, le=120)


class ChainEntry(_Strict):
    provider: ProviderName
    model: str = Field(min_length=3, max_length=200)


class RoleSpec(_Strict):
    chain: list[ChainEntry] = Field(min_length=1, max_length=12)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0, le=65_536)
    if_family_equals_author: Literal["mark_review_not_independent"] | None = None


class EmbeddingsSpec(_Open):
    engine: str
    model: str
    dimensions: int = Field(gt=0)
    cloud_fallback: str | None = None

    @field_validator("cloud_fallback")
    @classmethod
    def _free(cls, v: str | None) -> str | None:
        if v is not None and not is_free_openrouter_id(v):
            raise ValueError(f"embeddings.cloud_fallback {v!r} is not a free OpenRouter id")
        return v


class ModelsSection(_Strict):
    providers: dict[ProviderName, ProviderSpec]
    roles: dict[str, RoleSpec]
    embeddings: EmbeddingsSpec | None = None
    speech_to_text: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _lift_non_chat_roles(cls, data: Any) -> Any:
        """The stored value is models.yaml's `providers:` + `roles:` verbatim (seg05 contract), where
        `roles.embeddings` / `roles.speech_to_text` are not chat chains: lift them out for validation."""
        if isinstance(data, Mapping) and isinstance(data.get("roles"), Mapping):
            roles = dict(data["roles"])
            out = {k: v for k, v in data.items() if k != "roles"}
            for k in NON_CHAT_ROLES:
                if k in roles:
                    out.setdefault(k, roles.pop(k))
            out["roles"] = roles
            return out
        return data

    def stored(self) -> dict[str, Any]:
        """Back to the stored (YAML) shape: non-chat roles nested under `roles`."""
        d = self.model_dump(mode="json")
        roles = d["roles"]
        for k in NON_CHAT_ROLES:
            v = d.pop(k, None)
            if v is not None:
                roles[k] = v
        return d

    @model_validator(mode="after")
    def _routes(self) -> ModelsSection:
        if not self.roles:
            raise ValueError("models.roles is empty")
        for role, spec in self.roles.items():
            for i, entry in enumerate(spec.chain):
                if entry.provider not in self.providers:
                    raise ValueError(f"roles.{role}.chain[{i}]: provider {entry.provider!r} is not under providers")
                if entry.provider == "openrouter" and not is_free_openrouter_id(entry.model):
                    raise ValueError(
                        f"roles.{role}.chain[{i}]: {entry.model!r} is not free (must end with ':free' or be "
                        f"{FREE_ROUTER!r}); a paid id in a chain is billed at the paid price"
                    )
        return self

    def chain(self, role: str) -> list[ChainEntry]:
        spec = self.roles.get(role)
        if spec is None:
            raise KeyError(f"unknown LLM role {role!r}")
        return list(spec.chain)


class BudgetSection(_Strict):
    daily_request_cap: int = Field(ge=0)
    reserve_for_chat: float = Field(gt=0, lt=1)
    max_llm_calls_per_turn: int = Field(ge=1, le=10)
    rpm: int = Field(gt=0)
    on_cap_reached: Literal["degrade"] = "degrade"
    pdf_engine: str = Field(min_length=1)
    only_free_ids_in_chains: Literal[True] = True
    rate_wait_s: float = Field(default=3.0, ge=0, le=30)

    def share(self, purpose: Purpose) -> float:
        return self.reserve_for_chat if purpose == "chat" else 1.0 - self.reserve_for_chat


class SearchLayer(_Open):
    enabled: bool = True
    ttl_hours: float = Field(gt=0, le=24 * 365)
    rps: float = Field(gt=0, le=50)
    max_results: int = Field(ge=1, le=20)


SEARCH_LAYER_NAMES = (
    "pubmed",
    "europepmc",
    "medlineplus",
    "openfda",
    "clinicaltrials",
    "tavily",
    "searxng",
    "commons_images",
    "youtube_oembed",
)


class SearchSection(_Open):
    provider: Literal["tavily"] = "tavily"
    depth: Literal["basic", "advanced"] = "basic"
    max_results: int = Field(ge=1, le=20)
    include_answer: bool = False
    cache_ttl_days: float = Field(gt=0, le=365)
    per_user_daily_cap: int = Field(ge=0)
    monthly_credit_guard: int = Field(ge=0)
    never_call: list[str] = Field(default_factory=list)
    include_domains: list[str] = Field(default_factory=list)
    layers: dict[str, SearchLayer] = Field(default_factory=dict)

    @field_validator("layers")
    @classmethod
    def _known(cls, v: dict[str, SearchLayer]) -> dict[str, SearchLayer]:
        unknown = sorted(set(v) - set(SEARCH_LAYER_NAMES))
        if unknown:
            raise ValueError(f"unknown search layers: {unknown}")
        return v

    def layer(self, name: str) -> SearchLayer | None:
        spec = self.layers.get(name)
        return spec if spec is not None and spec.enabled else None


class GenerationMedia(_Open):
    model: str
    max_per_user_per_day: int = Field(ge=0)


class GenerationSection(_Open):
    provider: str
    space_id: str
    image: GenerationMedia
    video: GenerationMedia
    label: str = Field(min_length=1)
    publish_requires_admin_approval: bool = True


SECTION_MODELS: dict[str, type[BaseModel]] = {
    "models": ModelsSection,
    "budget": BudgetSection,
    "search": SearchSection,
    "generation": GenerationSection,
}


def validate_section(key: str, value: object) -> dict[str, Any]:
    """Validate one agent-platform `app_config` value; raises `KeyError` / `pydantic.ValidationError`."""
    parsed = SECTION_MODELS[key].model_validate(value)
    if isinstance(parsed, ModelsSection):
        return parsed.stored()
    return parsed.model_dump(mode="json", exclude_none=False)


def sections_from_models_yaml(raw: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Parsed `config/models.yaml` → the four `app_config` values (validated)."""
    models = {"providers": raw.get("providers") or {}, "roles": raw.get("roles") or {}}
    values = {
        "models": models,
        "budget": raw.get("budget"),
        "search": raw.get("search"),
        "generation": raw.get("generation"),
    }
    return {k: validate_section(k, v) for k, v in values.items()}


def load_yaml_sections(config_dir: Path) -> dict[str, dict[str, Any]]:
    path = config_dir / "models.yaml"
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, Mapping):
        raise ValueError(f"{path} is not a mapping")
    return sections_from_models_yaml(raw)


class AgentConfig(BaseModel):
    """One consistent snapshot of the four sections (what a call uses from start to finish)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    models: ModelsSection
    budget: BudgetSection
    search: SearchSection
    generation: GenerationSection
    source: dict[str, Literal["db", "yaml"]]
    invalid_keys: tuple[str, ...] = ()


def _snapshot(
    values: Mapping[str, Mapping[str, Any]], source: dict[str, Literal["db", "yaml"]], invalid: tuple[str, ...]
) -> AgentConfig:
    return AgentConfig(
        models=ModelsSection.model_validate(values["models"]),
        budget=BudgetSection.model_validate(values["budget"]),
        search=SearchSection.model_validate(values["search"]),
        generation=GenerationSection.model_validate(values["generation"]),
        source=source,
        invalid_keys=invalid,
    )


class RuntimeConfig:
    """60 s cached reader: `app_config` rows first, `config/models.yaml` for any key that is missing/invalid."""

    def __init__(
        self, db: Database | None, config_dir: Path, *, ttl_s: float = CACHE_TTL_S, clock: Any = time.monotonic
    ) -> None:
        self._db = db
        self._config_dir = config_dir
        self._ttl = ttl_s
        self._clock = clock
        self._cached: AgentConfig | None = None
        self._at: float | None = None
        self._lock = asyncio.Lock()
        self._yaml: dict[str, dict[str, Any]] | None = None

    def invalidate(self) -> None:
        self._at = None

    def yaml_sections(self) -> dict[str, dict[str, Any]]:
        if self._yaml is None:
            self._yaml = load_yaml_sections(self._config_dir)
        return self._yaml

    async def _db_rows(self) -> dict[str, Any]:
        if self._db is None or not self._db.configured:
            return {}
        if self._db.pool is None:
            await self._db.open()
        assert self._db.pool is not None
        async with self._db.pool.connection(timeout=5.0) as conn, asyncio.timeout(5.0):
            cur = await conn.execute(
                "select key, value from public.app_config where key = any(%s)", (list(AGENT_CONFIG_KEYS),)
            )
            rows = await cur.fetchall()
        return {str(k): v for k, v in rows}

    async def get(self) -> AgentConfig:
        now = self._clock()
        if self._cached is not None and self._at is not None and now - self._at < self._ttl:
            return self._cached
        async with self._lock:
            now = self._clock()
            if self._cached is not None and self._at is not None and now - self._at < self._ttl:
                return self._cached
            fallback = self.yaml_sections()
            try:
                rows = await self._db_rows()
            except Exception as exc:  # noqa: BLE001 - the DB being down must not stop routing; YAML is the seed
                log.warning("agent_config.db_unavailable", error=type(exc).__name__)
                if self._cached is not None:
                    self._at = now
                    return self._cached
                rows = {}
            values: dict[str, Mapping[str, Any]] = {}
            source: dict[str, Literal["db", "yaml"]] = {}
            invalid: list[str] = []
            for key in AGENT_CONFIG_KEYS:
                if key in rows:
                    try:
                        values[key] = validate_section(key, rows[key])
                        source[key] = "db"
                        continue
                    except ValidationError as exc:
                        invalid.append(key)
                        log.error("agent_config.invalid_row", key=key, errors=exc.error_count())
                values[key] = fallback[key]
                source[key] = "yaml"
            self._cached = _snapshot(values, source, tuple(invalid))
            self._at = now
            return self._cached


_RUNTIME: RuntimeConfig | None = None


def set_runtime_config(rc: RuntimeConfig | None) -> None:
    global _RUNTIME
    _RUNTIME = rc


def get_runtime_config() -> RuntimeConfig:
    if _RUNTIME is None:
        raise RuntimeError("the agent runtime config is not initialised (naadi_api.agent.runtime.init_runtime)")
    return _RUNTIME


def invalidate_runtime_config() -> None:
    """Called by the app_config API after an admin write so the next call reads the new value."""
    if _RUNTIME is not None:
        _RUNTIME.invalidate()


__all__ = [
    "AGENT_CONFIG_KEYS",
    "SEARCH_LAYER_NAMES",
    "SECTION_MODELS",
    "AgentConfig",
    "BudgetSection",
    "ChainEntry",
    "GenerationSection",
    "ModelsSection",
    "ProviderName",
    "ProviderSpec",
    "Purpose",
    "RoleSpec",
    "RuntimeConfig",
    "SearchLayer",
    "SearchSection",
    "get_runtime_config",
    "invalidate_runtime_config",
    "is_free_openrouter_id",
    "load_yaml_sections",
    "model_family",
    "sections_from_models_yaml",
    "set_runtime_config",
    "validate_section",
]
