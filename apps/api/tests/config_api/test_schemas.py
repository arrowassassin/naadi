"""app_config key schemas, the config/ defaults and the cache/fallback behaviour of AppConfigService."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from naadi_api import llm_config
from naadi_api.config_api.defaults import ConfigFileError, file_values
from naadi_api.config_api.schemas import KEY_SPECS, PUBLIC_KEYS, FeatureFlag, UnknownConfigKey, validate_value
from naadi_api.config_api.service import AppConfigService
from naadi_api.db import Database

CONFIG = Path(__file__).resolve().parents[4] / "config"


@pytest.fixture(scope="module")
def values() -> dict[str, Any]:
    return file_values(CONFIG)


def test_config_files_define_every_key(values: dict[str, Any]) -> None:
    assert set(values) == set(KEY_SPECS)
    assert set(values) == {
        "models",
        "budget",
        "search",
        "generation",
        "sources_policy",
        "feature_flags",
        "ui.nav",
        "exam_rules",
        "lab_values",
        "storage",
        "ui_copy",
    }
    assert set(PUBLIC_KEYS) == {"feature_flags", "ui.nav", "exam_rules", "ui_copy"}


def test_agent_keys_are_llm_config_shapes(values: dict[str, Any]) -> None:
    import yaml

    raw = yaml.safe_load((CONFIG / "models.yaml").read_text())
    assert values["models"] == llm_config.validate_section(
        "models", {"providers": raw["providers"], "roles": raw["roles"]}
    )
    assert values["budget"]["max_llm_calls_per_turn"] == 4


def test_paid_model_id_is_rejected(values: dict[str, Any]) -> None:
    bad = copy.deepcopy(values["models"])
    bad["roles"]["tutor"]["chain"][1]["model"] = "anthropic/claude-sonnet-5.5"
    with pytest.raises(ValidationError):
        validate_value("models", bad)


def test_seeded_defaults_match_the_contract(values: dict[str, Any]) -> None:
    flags = values["feature_flags"]
    assert flags["assistant"]["enabled"] is False
    assert flags["qbank"]["enabled"] is True and flags["qbank_timed"]["enabled"] is False
    assert flags["ink_lab"] == {"enabled": True, "audience": "admins", "everyone_outside_prod": True}
    assert [i["path"] for i in values["ui.nav"]] == ["/", "/learn", "/practice"]
    assert values["exam_rules"]["modes"]["exam"]["assistant"] is False
    assert values["exam_rules"]["block"]["max_questions"] == 40
    assert values["storage"]["content_public"] == "content-public"
    for key in ("agent.quota_reset", "agent.rate_limited", "agent.offline", "agent.exam_mode", "agent.unavailable"):
        assert values["ui_copy"][key]


@pytest.mark.parametrize(
    ("flag", "admin", "env", "on"),
    [
        ({"enabled": True}, False, "prod", True),
        ({"enabled": False}, True, "local", False),
        ({"enabled": True, "audience": "admins"}, False, "staging", False),
        ({"enabled": True, "audience": "admins"}, True, "prod", True),
        ({"enabled": True, "audience": "admins", "everyone_outside_prod": True}, False, "staging", True),
        ({"enabled": True, "audience": "admins", "everyone_outside_prod": True}, False, "prod", False),
    ],
)
def test_feature_flag_resolution(flag: dict[str, Any], admin: bool, env: str, on: bool) -> None:
    assert FeatureFlag.model_validate(flag).on_for(is_admin=admin, app_env=env) is on


def _invalid(key: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        validate_value(key, value)


def test_exam_mode_can_never_reveal_answers_or_enable_the_assistant(values: dict[str, Any]) -> None:
    rules = copy.deepcopy(values["exam_rules"])
    rules["modes"]["exam"]["assistant"] = True
    _invalid("exam_rules", rules)
    rules = copy.deepcopy(values["exam_rules"])
    rules["modes"]["exam"]["explanations"] = "after_each_question"
    _invalid("exam_rules", rules)
    rules = copy.deepcopy(values["exam_rules"])
    rules["modes"]["tutor"]["seconds_per_question"] = 90  # untimed with a timer
    _invalid("exam_rules", rules)
    rules = copy.deepcopy(values["exam_rules"])
    rules["block"]["default_questions"] = 99
    _invalid("exam_rules", rules)


def test_other_validation_rules(values: dict[str, Any]) -> None:
    _invalid("ui_copy", {"Not Dotted": "x"})
    _invalid("ui_copy", {"a.b": ""})
    _invalid("ui_copy", {"a.b": "x" * 501})
    _invalid("ui.nav", [*values["ui.nav"], values["ui.nav"][0]])  # duplicate
    _invalid("ui.nav", [{"id": "x", "label": "X", "path": "https://evil.example", "icon": "sun", "feature": "today"}])
    _invalid("feature_flags", {"Bad-Name": {"enabled": True}})
    _invalid("feature_flags", {"x": {"enabled": True, "extra": 1}})
    _invalid("storage", {**values["storage"], "vault": "Not A Bucket"})
    _invalid("sources_policy", {**values["sources_policy"], "block_without_license": False})
    with pytest.raises(UnknownConfigKey):
        validate_value("nope", {})


def test_nav_must_use_known_flags(tmp_path: Path) -> None:
    import shutil

    shutil.copytree(CONFIG, tmp_path / "config")
    p = tmp_path / "config" / "app_config.yaml"
    p.write_text(p.read_text().replace("feature: courses}", "feature: ghost}"))
    with pytest.raises(ConfigFileError, match="unknown feature flag 'ghost'"):
        file_values(tmp_path / "config")


def test_unknown_key_in_app_config_yaml_is_refused(tmp_path: Path) -> None:
    import shutil

    shutil.copytree(CONFIG, tmp_path / "config")
    p = tmp_path / "config" / "app_config.yaml"
    p.write_text(p.read_text() + "\nsurprise: 1\n")
    with pytest.raises(ConfigFileError, match="surprise"):
        file_values(tmp_path / "config")


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


async def test_service_falls_back_to_files_without_a_database_and_caches(values: dict[str, Any]) -> None:
    clock = FakeClock()
    svc = AppConfigService(Database(None), CONFIG, clock=clock)
    row = await svc.row("feature_flags")
    assert row.source == "file"
    assert row.value == values["feature_flags"]
    assert await svc.feature_enabled("today", is_admin=False, app_env="prod") is True
    assert await svc.feature_enabled("ink_lab", is_admin=False, app_env="prod") is False
    assert await svc.feature_enabled("ink_lab", is_admin=True, app_env="prod") is True
    assert await svc.feature_enabled("ghost", is_admin=True, app_env="local") is False
    assert [i["id"] for i in await svc.nav_for(is_admin=False, app_env="prod")] == ["today", "learn", "practice"]
    assert (await svc.row("feature_flags")) is row  # cached
    clock.t += 61
    assert (await svc.row("feature_flags")) is not row  # expired
    with pytest.raises(UnknownConfigKey):
        await svc.get("nope")
    await svc.start()  # no database: nothing to listen on, no task
    assert svc.listening is False
    await svc.stop()


async def test_service_falls_back_when_the_database_is_unreachable(values: dict[str, Any]) -> None:
    db = Database(SecretStr("postgresql://naadi:x@127.0.0.1:9/none?connect_timeout=1"))
    svc = AppConfigService(db, CONFIG)
    try:
        row = await svc.row("ui_copy")
        assert row.source == "file"
        assert row.value == values["ui_copy"]
    finally:
        await db.close()
