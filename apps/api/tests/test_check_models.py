"""Unit tests for tools/check_models.py rules (synthetic catalog; the live run is a CI step)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar

import pytest
import yaml

from .conftest import REPO_ROOT


def _load() -> ModuleType:
    path = REPO_ROOT / "tools" / "check_models.py"
    spec = importlib.util.spec_from_file_location("check_models", path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_models"] = mod
    spec.loader.exec_module(mod)
    return mod


cm = _load()


def model(mid: str, caps: set[str] | None = None, price: float = 0.0) -> Any:
    return cm.CatalogModel(
        id=mid, context_length=1000, capabilities=caps or set(), prompt_price=price, completion_price=price
    )


def nv(mid: str, caps: set[str] | None = None, probe: str | None = None) -> Any:
    return cm.NvidiaModel(id=mid, capabilities=caps or set(), probe=probe, latency_ms=10.0)


def catalog(*models: Any) -> dict[str, Any]:
    return {m.id: m for m in models}


def chain(*entries: tuple[str, str]) -> dict[str, Any]:
    return {"chain": [{"provider": p, "model": m} for p, m in entries]}


def cfg(roles: dict[str, Any], only_free: bool = True, **extra: Any) -> dict[str, Any]:
    providers = {"nvidia": {"key_env": "NVIDIA_API_KEY", "rpm": 35}, "openrouter": {"key_env": "K", "rpm": 18}}
    return {"budget": {"only_free_ids_in_chains": only_free}, "providers": providers, "roles": roles, **extra}


def run(c: dict[str, Any], orc: dict[str, Any] | None = None, nvd: dict[str, Any] | None = None) -> Any:
    lines: list[str] = []
    report = cm.check(c, orc or {}, nvd or {}, out=lines.append)
    report.lines = lines
    return report


def test_free_id_rule() -> None:
    assert cm.is_free_id("vendor/a:free")
    assert cm.is_free_id("openrouter/free")
    assert not cm.is_free_id("vendor/a")
    assert not cm.is_free_id("openrouter/auto")


def test_all_good_passes_and_ignores_non_chain_blocks() -> None:
    c = cfg(
        {
            "tutor": chain(("nvidia", "n/t"), ("openrouter", "v/missing:free"), ("openrouter", "openrouter/free")),
            "embeddings": {"engine": "fastembed", "model": "BAAI/whatever"},
            "speech_to_text": {"order": ["ipados_dictation"]},
        },
        generation={"image": {"model": "not/an-llm"}},
    )
    r = run(c, catalog(model("openrouter/free")), {"n/t": nv("n/t", {"tools"})})
    assert r.failures == []
    assert r.warnings == ["tutor: v/missing:free is missing from the OpenRouter catalog"]
    assert any("embeddings" in line and "skipped" in line for line in r.lines)


def test_paid_openrouter_id_fails_anywhere_in_chain() -> None:
    c = cfg({"router": chain(("openrouter", "v/r:free"), ("openrouter", "v/paid"))})
    r = run(c, catalog(model("v/r:free"), model("v/paid")))
    assert r.failures == ["router: v/paid is not a :free ID (only_free_ids_in_chains)"]


def test_provider_level_free_rule_applies_without_budget_flag() -> None:
    c = cfg({"router": chain(("openrouter", "v/paid"))}, only_free=False)
    c["providers"]["openrouter"]["only_free_ids"] = True
    assert run(c, catalog(model("v/paid"))).failures == ["router: v/paid is not a :free ID (only_free_ids_in_chains)"]


def test_paid_id_allowed_when_rules_off() -> None:
    c = cfg({"router": chain(("openrouter", "v/paid"))}, only_free=False)
    assert run(c, catalog(model("v/paid"))).failures == []


def test_missing_openrouter_primary_fails() -> None:
    r = run(cfg({"author": chain(("openrouter", "v/gone:free"))}))
    assert r.failures == ["author: v/gone:free is missing from the OpenRouter catalog"]


@pytest.mark.parametrize(
    ("role", "need", "orcap"),
    [
        ("tutor", "tools", "tools"),
        ("structured", "structured_output", "structured_outputs"),
        ("vision", "image", "image"),
    ],
)
def test_openrouter_primary_without_capability_fails(role: str, need: str, orcap: str) -> None:
    c = cfg({role: chain(("openrouter", "v/m:free"))})
    assert run(c, catalog(model("v/m:free", {"text"}))).failures == [f"{role}: v/m:free lacks {need}"]
    assert run(c, catalog(model("v/m:free", {orcap}))).failures == []


@pytest.mark.parametrize("role", ["tutor", "structured", "vision"])
def test_nvidia_primary_without_declared_capability_fails(role: str) -> None:
    need = cm.NEEDS[role]
    c = cfg({role: chain(("nvidia", "n/m"))})
    r = run(c, nvd={"n/m": nv("n/m", set())})
    assert r.failures == [f"{role}: n/m lacks {need} (not declared by the ChatNVIDIA model profile)"]
    assert run(c, nvd={"n/m": nv("n/m", {need})}).failures == []


def test_free_suffix_that_started_charging_fails() -> None:
    r = run(cfg({"router": chain(("openrouter", "v/r:free"))}), catalog(model("v/r:free", price=0.000001)))
    assert r.failures == ["router: v/r:free is no longer priced at $0"]


def test_nvidia_primary_that_does_not_answer_fails_fallback_warns() -> None:
    c = cfg({"author": chain(("nvidia", "n/a"), ("nvidia", "n/b"))})
    r = run(c, nvd={"n/a": nv("n/a", probe="HTTP 410"), "n/b": nv("n/b", probe="HTTP 404")})
    assert r.failures == ["author: n/a does not answer (HTTP 410)"]
    assert r.warnings == ["author: n/b does not answer (HTTP 404)"]


def test_nvidia_without_key_is_explicit_not_silent_and_not_a_failure() -> None:
    c = cfg({"author": chain(("nvidia", "n/a"))})
    r = run(c, nvd={"n/a": nv("n/a", probe=cm.NOT_PROBED_IN_CI)})
    assert r.failures == []
    assert any(cm.NOT_PROBED_IN_CI in line and "n/a" in line for line in r.lines)


def test_inspect_nvidia_without_key_makes_no_call(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeChat:
        profile: ClassVar[dict[str, bool]] = {"tool_calling": True, "image_inputs": False}

        def invoke(self, prompt: str) -> None:
            raise AssertionError("must not call the model without a key")

    monkeypatch.setattr(cm, "_nvidia_client", lambda mid, key: FakeChat())
    out = cm.inspect_nvidia(["n/a"], None)
    assert out["n/a"].capabilities == {"tools"}
    assert out["n/a"].probe == cm.NOT_PROBED_IN_CI


def test_inspect_nvidia_with_key_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeChat:
        profile: ClassVar[dict[str, bool]] = {"structured_output": True}

        def __init__(self, mid: str) -> None:
            self.mid = mid

        def invoke(self, prompt: str) -> None:
            if self.mid == "n/gone":
                raise Exception("[410] Gone")

    monkeypatch.setattr(cm, "_nvidia_client", lambda mid, key: FakeChat(mid))
    out = cm.inspect_nvidia(["n/ok", "n/gone"], "nvapi-unit-test")
    assert out["n/ok"].probe is None and out["n/ok"].capabilities == {"structured_output"}
    assert out["n/gone"].probe == "HTTP 410"


def test_shape_errors() -> None:
    c = cfg({"tutor": {"model": "v/old:free"}, "vision": {"chain": []}, "x": chain(("acme", "a/b"))})
    r = run(c)
    assert "tutor: has `model` but no `chain:` (old config shape)" in r.failures
    assert "vision: `chain` must be a non-empty list" in r.failures
    assert "x: chain[0] provider 'acme' is not under `providers:`" in r.failures


def test_repo_config_shape_and_free_rule() -> None:
    config = yaml.safe_load((REPO_ROOT / "config" / "models.yaml").read_text())
    assert config["budget"]["only_free_ids_in_chains"] is True
    role_chains, skipped, errors = cm.chains(config)
    assert errors == []
    assert {"embeddings", "speech_to_text"} <= set(skipped)
    assert {"tutor", "vision", "structured"} <= set(role_chains)
    for role, entries in role_chains.items():
        for e in entries:
            if e["provider"] == "openrouter":
                assert cm.is_free_id(e["model"]), f"{role}: {e['model']}"


def test_repo_nvidia_primaries_declare_their_capabilities() -> None:
    """Offline (SDK profile only, no key): the same check CI runs before the live probe."""
    config = yaml.safe_load((REPO_ROOT / "config" / "models.yaml").read_text())
    role_chains, _, _ = cm.chains(config)
    nvd = cm.inspect_nvidia(cm.models_for(role_chains, "nvidia"), None)
    r = cm.check(config, {}, nvd, out=lambda _line: None)
    nvidia_primary_failures = [
        f
        for f in r.failures
        if any(f.startswith(f"{role}: ") and ch[0]["provider"] == "nvidia" for role, ch in role_chains.items())
    ]
    assert nvidia_primary_failures == []


def test_main_with_missing_config_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        cm.main(["--config", str(tmp_path / "nope.yaml")])


def test_app_config_rows_map_to_the_checked_shape() -> None:
    models = {"providers": {"openrouter": {"key_env": "K", "rpm": 1}}, "roles": {"router": {"chain": []}}}
    cfg = cm._rows_to_config([("models", models), ("budget", {"only_free_ids_in_chains": True})])
    assert cfg == {
        "providers": models["providers"],
        "roles": models["roles"],
        "budget": {"only_free_ids_in_chains": True},
    }
    assert cm._rows_to_config([("budget", {})]) is None


def test_paid_id_in_live_app_config_fails_main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = yaml.safe_load((REPO_ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
    live = {
        "providers": repo["providers"],
        "roles": {"router": {"chain": [{"provider": "openrouter", "model": "vendor/paid-model"}]}},
        "budget": repo["budget"],
    }
    caps = {"tools", "image", "structured_output"}
    monkeypatch.setattr(cm, "load_app_config", lambda mode: (live, "read from DATABASE_URL"))
    monkeypatch.setattr(cm, "fetch_openrouter_catalog", lambda key: {})
    monkeypatch.setattr(
        cm, "inspect_nvidia", lambda models, key: {m: cm.NvidiaModel(m, caps, None, 1.0) for m in models}
    )
    assert cm.main(["--app-config", "db"]) == 1
    out = capsys.readouterr().out
    assert "app_config (live): router: vendor/paid-model is not a :free ID" in out


def test_app_config_off_and_auto_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_ACCESS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert cm.load_app_config("off") == (None, "skipped (--app-config off)")
    assert cm.load_app_config("auto")[0] is None
    with pytest.raises(SystemExit):
        cm.load_app_config("db")
