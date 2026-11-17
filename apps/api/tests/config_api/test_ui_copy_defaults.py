"""packages/shared/ui-copy.defaults.json + ui-copy.admin.defaults.json: the web's last-resort text for every
required ui_copy key.

    UPDATE_UI_COPY_DEFAULTS=1 uv run pytest tests/config_api/test_ui_copy_defaults.py   # regenerate

Built from config/app_config.yaml (the same values the seed writes), so the web never carries copy of its
own; CI fails when a committed file is stale.

The admin console's and the review queue's strings (ADMIN_PREFIXES) go to the second file, which only the admin
chunks load (segment 22 budget: initial JS <= 250 KB gzip); EAGER_ADMIN_KEYS are shown outside those chunks and
stay in the first. apps/web/src/copy/adminCopySplit.test.ts checks the web never renders an admin-only key
outside the admin chunks.
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
OUT_ADMIN = REPO / "packages" / "shared" / "ui-copy.admin.defaults.json"
GENERATOR = "UPDATE_UI_COPY_DEFAULTS=1 uv run pytest tests/config_api/test_ui_copy_defaults.py"

ADMIN_PREFIXES = ("admin.", "review.")
# Admin-prefixed keys the web renders outside the admin chunks (the Account page's admin link, the review route's
# not-found gate).
EAGER_ADMIN_KEYS = frozenset({"admin.account_link", "review.not_found_title", "review.not_found_body"})


def is_admin_only(key: str) -> bool:
    return key.startswith(ADMIN_PREFIXES) and key not in EAGER_ADMIN_KEYS


def build() -> dict[Path, str]:
    copy = file_values(REPO / "config")["ui_copy"]
    keys = sorted(REQUIRED_UI_COPY_KEYS)
    core = {
        "generator": GENERATOR,
        "required": keys,
        "values": {k: copy[k] for k in keys if not is_admin_only(k)},
    }
    admin = {"generator": GENERATOR, "values": {k: copy[k] for k in keys if is_admin_only(k)}}
    dump = lambda body: json.dumps(body, indent=2, ensure_ascii=False) + "\n"  # noqa: E731
    return {OUT: dump(core), OUT_ADMIN: dump(admin)}


def test_defaults_file_is_current() -> None:
    for out, fresh in build().items():
        if os.environ.get("UPDATE_UI_COPY_DEFAULTS"):
            out.write_text(fresh, encoding="utf-8")
        assert out.read_text(encoding="utf-8") == fresh, f"{out.name} is stale: regenerate (see the docstring)"


def test_split_covers_every_required_key_once() -> None:
    files = [json.loads(text)["values"] for text in build().values()]
    assert set(files[0]).isdisjoint(files[1])
    assert set(files[0]) | set(files[1]) == REQUIRED_UI_COPY_KEYS
    assert set(files[0]) >= EAGER_ADMIN_KEYS


def test_every_required_key_is_seeded_and_non_empty() -> None:
    copy = file_values(REPO / "config")["ui_copy"]
    assert set(copy) >= REQUIRED_UI_COPY_KEYS
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
