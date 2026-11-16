"""`app_config` values as shipped in `config/` (the seed source, and the API's fallback when a row is missing)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from .. import llm_config
from .schemas import KEY_SPECS, cross_key_problems, validate_value

APP_CONFIG_FILE = "app_config.yaml"


class ConfigFileError(ValueError):
    pass


def _load_yaml(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise ConfigFileError(f"{path.name} not found in {path.parent} (set NAADI_CONFIG_DIR)")
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, Mapping):
        raise ConfigFileError(f"{path.name} is not a mapping")
    return raw


def file_values(config_dir: Path) -> dict[str, Any]:
    """Every `app_config` key's validated value from config/*.yaml. Raises on any invalid or missing key."""
    values: dict[str, Any] = dict(llm_config.load_yaml_sections(config_dir))
    app = _load_yaml(config_dir / APP_CONFIG_FILE)
    unknown = sorted(set(app) - set(KEY_SPECS))
    if unknown:
        raise ConfigFileError(f"{APP_CONFIG_FILE}: unknown keys {unknown}")
    for key, raw in app.items():
        values[key] = validate_value(key, raw)
    missing = sorted(set(KEY_SPECS) - set(values))
    if missing:
        raise ConfigFileError(f"config/ does not define app_config keys {missing}")
    problems = cross_key_problems(values)
    if problems:
        raise ConfigFileError("; ".join(problems))
    return values
