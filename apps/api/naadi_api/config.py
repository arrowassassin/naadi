"""Typed access to the repo's YAML configs (`config/models.yaml`). Model IDs live only there."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

ProviderName = Literal["nvidia", "openrouter"]


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


class BudgetConfig(_Loose):
    daily_request_cap: int
    reserve_for_chat: float
    max_llm_calls_per_turn: int
    rpm: int
    only_free_ids_in_chains: bool


class SearchConfig(_Loose):
    provider: str
    monthly_credit_guard: int
    per_user_daily_cap: int
    cache_ttl_days: int


class ProviderConfig(_Loose):
    key_env: str
    rpm: int = Field(gt=0)
    probe_timeout_s: float = Field(default=30.0, gt=0, le=120)


class ChainEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: ProviderName
    model: str = Field(min_length=3)
    context_tokens: int | None = Field(default=None, ge=4_096, le=4_194_304)


class RoleConfig(_Loose):
    """An LLM role has an ordered `chain`; non-LLM roles (embeddings, speech_to_text) have none."""

    chain: list[ChainEntry] | None = None


class ModelsConfig(_Loose):
    budget: BudgetConfig
    search: SearchConfig
    providers: dict[ProviderName, ProviderConfig]
    roles: dict[str, RoleConfig]

    @model_validator(mode="after")
    def _chains_use_known_providers(self) -> ModelsConfig:
        for role, spec in self.roles.items():
            for entry in spec.chain or []:
                if entry.provider not in self.providers:
                    raise ValueError(f"roles.{role}: provider {entry.provider!r} is not under `providers:`")
        return self

    def models_for(self, provider: ProviderName) -> list[str]:
        """Distinct model IDs that any role chain routes to `provider`, in first-use order."""
        seen: dict[str, None] = {}
        for spec in self.roles.values():
            for entry in spec.chain or []:
                if entry.provider == provider:
                    seen.setdefault(entry.model, None)
        return list(seen)


class ConfigError(RuntimeError):
    pass


def load_models_config(config_dir: Path) -> ModelsConfig:
    path = config_dir / "models.yaml"
    if not path.is_file():
        raise ConfigError(f"{path} not found (set NAADI_CONFIG_DIR to the directory holding models.yaml)")
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return ModelsConfig.model_validate(raw)
