"""packages/shared/ui-copy.defaults.json: the web's last-resort text for every required ui_copy key.

    UPDATE_UI_COPY_DEFAULTS=1 uv run pytest tests/config_api/test_ui_copy_defaults.py   # regenerate

Built from config/app_config.yaml (the same values the seed writes), so the web never carries copy of its
own; CI fails when the committed file is stale.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from naadi_api.config_api.defaults import file_values
from naadi_api.config_api.schemas import REQUIRED_UI_COPY_KEYS, validate_value

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "packages" / "shared" / "ui-copy.defaults.json"


def build() -> str:
    copy = file_values(REPO / "config")["ui_copy"]
    body = {
        "generator": "UPDATE_UI_COPY_DEFAULTS=1 uv run pytest tests/config_api/test_ui_copy_defaults.py",
        "required": sorted(REQUIRED_UI_COPY_KEYS),
        "values": {k: copy[k] for k in sorted(REQUIRED_UI_COPY_KEYS)},
    }
    return json.dumps(body, indent=2, ensure_ascii=False) + "\n"


def test_defaults_file_is_current() -> None:
    fresh = build()
    if os.environ.get("UPDATE_UI_COPY_DEFAULTS"):
        OUT.write_text(fresh, encoding="utf-8")
    assert OUT.read_text(encoding="utf-8") == fresh, "stale: regenerate (see this module's docstring)"


def test_every_required_key_is_seeded_and_non_empty() -> None:
    copy = file_values(REPO / "config")["ui_copy"]
    assert REQUIRED_UI_COPY_KEYS <= set(copy)
    assert all(copy[k].strip() for k in REQUIRED_UI_COPY_KEYS)


def test_dropping_a_required_key_is_refused() -> None:
    copy = dict(file_values(REPO / "config")["ui_copy"])
    del copy["signin.submit"]
    del copy["account.sign_out"]
    with pytest.raises(ValidationError, match=r"missing keys the app shows: account\.sign_out, signin\.submit"):
        validate_value("ui_copy", copy)


def test_extra_keys_are_allowed() -> None:
    copy = {**file_values(REPO / "config")["ui_copy"], "promo.banner": "New: notes export"}
    assert validate_value("ui_copy", copy)["promo.banner"] == "New: notes export"
