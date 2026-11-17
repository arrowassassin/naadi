"""Settings, the feature flag without a token, and the health component when nothing is configured."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from naadi_api.config_api.service import AppConfigService
from naadi_api.db import Database
from naadi_api.generation.health import generation_check
from naadi_api.health.checks import NotConfigured
from naadi_api.settings import REPO_ROOT
from tests.conftest import make_settings


def test_not_configured_without_a_token_and_never_in_prod_with_the_stub() -> None:
    assert not make_settings().generation_configured
    assert make_settings(hf_gen_token=SecretStr("hf_x_value")).generation_configured
    assert make_settings(app_env="local", generation_fake=True).generation_configured
    assert not make_settings(app_env="prod", generation_fake=True).generation_configured


def test_the_token_is_a_secret_that_error_text_is_scrubbed_of() -> None:
    s = make_settings(hf_gen_token=SecretStr("hf_secret_value_123"))
    assert "hf_secret_value_123" in s.secret_values()
    assert "hf_secret_value_123" not in repr(s)


async def test_flag_stays_off_until_the_token_is_set() -> None:
    off = AppConfigService(Database(None), REPO_ROOT / "config", unavailable=lambda: ("generation",))
    on = AppConfigService(Database(None), REPO_ROOT / "config", unavailable=lambda: ())
    assert (await off.resolved_features(is_admin=True, app_env="local"))["generation"] is False
    assert await off.feature_enabled("generation", is_admin=True, app_env="local") is False
    assert (await on.resolved_features(is_admin=False, app_env="prod"))["generation"] is True
    # other flags are untouched
    assert (await off.resolved_features(is_admin=False, app_env="prod"))["today"] is True


async def test_health_reports_not_configured_without_calling_anything() -> None:
    check = generation_check(make_settings(), Database(None))
    with pytest.raises(NotConfigured) as e:
        await check()
    assert e.value.status == "ok"  # an optional feature that is not set up is not an outage


def test_factory_wires_the_stub_only_outside_production() -> None:
    from naadi_api.config_api.service import AppConfigService
    from naadi_api.generation.client import FakeGenerationClient
    from naadi_api.generation.factory import LazySpaceClient, build_generation_service
    from naadi_api.generation.store import MemoryMediaStore, SupabaseMediaStore

    def build(**kw: object) -> object:
        s = make_settings(**kw)
        return build_generation_service(s, Database(None), AppConfigService(Database(None), s.config_dir))

    local = build(app_env="local", generation_fake=True)
    assert local.configured and isinstance(local._client, FakeGenerationClient)  # type: ignore[attr-defined]
    assert isinstance(local._store, MemoryMediaStore)  # type: ignore[attr-defined]
    prod_fake = build(app_env="prod", generation_fake=True)
    assert not prod_fake.configured and isinstance(prod_fake._store, SupabaseMediaStore)  # type: ignore[attr-defined]
    real = build(hf_gen_token=SecretStr("hf_x_value"))
    assert real.configured and isinstance(real._client, LazySpaceClient)  # type: ignore[attr-defined]
    assert not build().configured  # type: ignore[attr-defined]
