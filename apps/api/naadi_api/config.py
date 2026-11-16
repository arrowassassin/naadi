"""Typed access to the repo's YAML configs (`config/models.yaml`). Model IDs live only there."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


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


class ModelsConfig(_Loose):
    budget: BudgetConfig
    search: SearchConfig


class ConfigError(RuntimeError):
    pass


def load_models_config(config_dir: Path) -> ModelsConfig:
    path = config_dir / "models.yaml"
    if not path.is_file():
        raise ConfigError(f"{path} not found (set NAADI_CONFIG_DIR to the directory holding models.yaml)")
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return ModelsConfig.model_validate(raw)
