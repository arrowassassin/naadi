"""NVIDIA provider: config parsing, the /api/health probe check and the probe helper (no network)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import SecretStr

from naadi_api import llm
from naadi_api.config import ConfigError, ModelsConfig, load_models_config
from naadi_api.health.checks import NotConfigured, nvidia_check
from naadi_api.health.service import COMPONENTS, Check, HealthService
from naadi_api.llm import ProbeResult, describe_failure, http_status_of

from .conftest import REPO_ROOT, fake_check, make_settings


def _cfg(roles: dict[str, Any]) -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load((REPO_ROOT / "config" / "models.yaml").read_text())
    raw["roles"] = roles
    return raw


def _probe(answers: dict[str, str | None]) -> tuple[Any, list[str]]:
    """Fake probe: `None` = answers, a string = failure detail."""
    seen: list[str] = []

    async def probe(model: str, key: SecretStr) -> ProbeResult:
        assert key.get_secret_value() == "nvapi-unit-test-key"
        seen.append(model)
        detail = answers[model]
        return ProbeResult("nvidia", model, detail is None, 12.5, detail or "answers")

    return probe, seen


# ───────────── config ─────────────
def test_repo_config_parses_and_lists_nvidia_models_in_order() -> None:
    cfg = load_models_config(REPO_ROOT / "config")
    raw = yaml.safe_load((REPO_ROOT / "config" / "models.yaml").read_text())
    expected: list[str] = []
    for spec in raw["roles"].values():
        for entry in spec.get("chain") or []:
            if entry["provider"] == "nvidia" and entry["model"] not in expected:
                expected.append(entry["model"])
    assert cfg.models_for("nvidia") == expected
    assert expected, "the repo config routes at least one role to NVIDIA"
    assert set(cfg.providers) == {"nvidia", "openrouter"}
    assert cfg.providers["nvidia"].key_env == "NVIDIA_API_KEY"


def test_chain_with_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="provider"):
        ModelsConfig.model_validate(_cfg({"tutor": {"chain": [{"provider": "acme", "model": "a/b"}]}}))


def test_provider_missing_from_providers_block_is_rejected() -> None:
    raw = _cfg({"tutor": {"chain": [{"provider": "nvidia", "model": "a/b"}]}})
    del raw["providers"]["nvidia"]
    with pytest.raises(ValueError, match="not under `providers:`"):
        ModelsConfig.model_validate(raw)


def test_roles_without_chain_are_allowed() -> None:
    cfg = ModelsConfig.model_validate(_cfg({"embeddings": {"engine": "fastembed", "model": "x/y"}}))
    assert cfg.models_for("nvidia") == []


def test_missing_config_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_models_config(tmp_path)


# ───────────── health check ─────────────
async def test_all_models_answer_is_ok() -> None:
    probe, seen = _probe({"a/one": None, "b/two": None})
    s = make_settings(nvidia_api_key="nvapi-unit-test-key")
    out = await nvidia_check(s, ["a/one", "b/two"], probe=probe)()
    assert out.status == "ok"
    assert out.detail == "All 2 configured models answer"
    assert out.models is not None and [m.model for m in out.models] == ["a/one", "b/two"]
    assert sorted(seen) == ["a/one", "b/two"]


async def test_some_models_failing_is_degraded_with_names() -> None:
    probe, _ = _probe({"a/one": None, "b/two": "HTTP 410 (retired)"})
    out = await nvidia_check(make_settings(nvidia_api_key="nvapi-unit-test-key"), ["a/one", "b/two"], probe=probe)()
    assert out.status == "degraded"
    assert out.detail == "1 of 2 configured models answer. Failing: b/two HTTP 410 (retired)"
    assert out.models is not None and [m.ok for m in out.models] == [True, False]


async def test_all_models_failing_is_down() -> None:
    probe, _ = _probe({"a/one": "timed out"})
    out = await nvidia_check(make_settings(nvidia_api_key="nvapi-unit-test-key"), ["a/one"], probe=probe)()
    assert out.status == "down"


async def test_missing_key_is_degraded_not_down() -> None:
    probe, seen = _probe({})
    with pytest.raises(NotConfigured) as ei:
        await nvidia_check(make_settings(), ["a/one"], probe=probe)()
    assert ei.value.status == "degraded"
    assert "NVIDIA_API_KEY" in str(ei.value)
    assert seen == []


async def test_no_nvidia_models_is_ok_without_calls() -> None:
    probe, seen = _probe({})
    out = await nvidia_check(make_settings(), [], probe=probe)()
    assert out.status == "ok" and seen == []


async def test_not_configured_component_is_degraded_and_flagged() -> None:
    probe, _ = _probe({})
    checks = {n: Check(n, fake_check(), ttl_s=60) for n in COMPONENTS}
    checks["nvidia"] = Check("nvidia", nvidia_check(make_settings(), ["a/one"], probe=probe), ttl_s=3600)
    report = await HealthService(checks=checks, version="t", environment="prod").health()
    comp = report.components.nvidia
    assert (comp.status, comp.configured, comp.latency_ms) == ("degraded", False, None)
    assert comp.detail == "Not configured: NVIDIA_API_KEY is not set on this deployment"
    assert report.status == "degraded"


async def test_probe_results_are_cached_for_an_hour_even_when_down() -> None:
    now = [0.0]
    calls: list[str] = []

    async def probe(model: str, key: SecretStr) -> ProbeResult:
        calls.append(model)
        return ProbeResult("nvidia", model, False, 1.0, "HTTP 503 (server error)")

    s = make_settings(nvidia_api_key="nvapi-unit-test-key")
    checks = {n: Check(n, fake_check(), ttl_s=60) for n in COMPONENTS}
    checks["nvidia"] = Check("nvidia", nvidia_check(s, ["a/one"], probe=probe), ttl_s=3600, failure_ttl_s=3600)
    svc = HealthService(checks=checks, version="t", environment="prod", clock=lambda: now[0])
    await asyncio.gather(*(svc.component("nvidia") for _ in range(10)))
    now[0] = 3599.0
    assert (await svc.component("nvidia")).cached is True
    assert calls == ["a/one"]
    now[0] = 3601.0
    await svc.component("nvidia")
    assert calls == ["a/one", "a/one"]


def test_app_wires_nvidia_with_hour_long_cache() -> None:
    from naadi_api.db import Database
    from naadi_api.main import NVIDIA_PROBE_TTL_S, default_health_service

    svc = default_health_service(make_settings(), Database(None))
    check = svc.checks["nvidia"]
    assert NVIDIA_PROBE_TTL_S >= 3600
    assert check.ttl_s == NVIDIA_PROBE_TTL_S and check.down_ttl_s == NVIDIA_PROBE_TTL_S


# ───────────── probe helper ─────────────
@pytest.mark.parametrize(
    ("message", "status", "detail"),
    [
        ("[410] Gone\nThe model has been retired", 410, "HTTP 410 (retired)"),
        ("[404] Not Found\nnope", 404, "HTTP 404 (not found)"),
        ("[429] Too Many Requests", 429, "HTTP 429 (rate-limited)"),
        ("[502] Bad Gateway", 502, "HTTP 502 (server error)"),
        ("[418] I'm a teapot", 418, "HTTP 418 (request failed)"),
    ],
)
def test_failure_descriptions(message: str, status: int, detail: str) -> None:
    exc = Exception(message)
    assert http_status_of(exc) == status
    assert describe_failure(exc) == detail


def test_failure_without_status_names_the_type_only() -> None:
    assert describe_failure(ConnectionError("dns failure for nvapi-secret-value")) == "ConnectionError"
    assert describe_failure(TimeoutError()) == "timed out"


async def test_probe_never_raises_and_sends_one_token_request(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []

    def fake_call(model: str, api_key: str, timeout_s: float) -> None:
        sent.append({"model": model, "key": api_key, "timeout": timeout_s})
        if model == "bad/model":
            raise Exception("[410] Gone")

    monkeypatch.setattr(llm, "_nvidia_call", fake_call)
    good = await llm.probe_nvidia("good/model", SecretStr("nvapi-unit-test-key"), timeout_s=3)
    bad = await llm.probe_nvidia("bad/model", SecretStr("nvapi-unit-test-key"), timeout_s=3)
    assert (good.ok, good.detail) == (True, "answers")
    assert (bad.ok, bad.detail) == (False, "HTTP 410 (retired)")
    assert sent[0] == {"model": "good/model", "key": "nvapi-unit-test-key", "timeout": 3}


def test_probe_client_disables_telemetry_and_limits_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    built: dict[str, Any] = {}

    class FakeChat:
        def __init__(self, **kw: Any) -> None:
            built.update(kw)

        def invoke(self, prompt: str) -> None:
            built["prompt"] = prompt

    monkeypatch.setattr(llm, "ChatNVIDIA", FakeChat)
    llm._nvidia_call("m/x", "nvapi-unit-test-key", 5.0)
    assert built["usage_telemetry_enabled"] is False
    assert built["max_completion_tokens"] == 1
    assert built["prompt"] == llm.PROBE_PROMPT
