"""Unit tests for tools/check_models.py rules (synthetic catalog; the live run is a CI step)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

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


def catalog(*models: Any) -> dict[str, Any]:
    return {m.id: m for m in models}


def cfg(roles: dict[str, Any], only_free: bool = True, **extra: Any) -> dict[str, Any]:
    return {"budget": {"only_free_ids_in_chains": only_free}, "roles": roles, **extra}


def test_free_id_rule() -> None:
    assert cm.is_free_id("vendor/a:free")
    assert cm.is_free_id("openrouter/free")
    assert not cm.is_free_id("vendor/a")
    assert not cm.is_free_id("openrouter/auto")


def test_all_good_passes_and_ignores_non_openrouter_blocks() -> None:
    c = cfg(
        {
            "tutor": {"model": "v/t:free", "fallbacks": ["v/missing-fallback:free", "openrouter/free"]},
            "embeddings": {"engine": "fastembed", "model": "BAAI/whatever"},
        },
        generation={"image": {"model": "not/openrouter"}},
    )
    cat = catalog(model("v/t:free", {"tools"}), model("openrouter/free"))
    assert cm.check(c, cat) == []


def test_paid_id_in_chain_fails() -> None:
    c = cfg({"router": {"model": "v/r:free", "fallbacks": ["v/paid"]}})
    failures = cm.check(c, catalog(model("v/r:free"), model("v/paid")))
    assert failures == ["router: v/paid is not a :free ID (only_free_ids_in_chains)"]


def test_paid_id_allowed_when_rule_off() -> None:
    c = cfg({"router": {"model": "v/paid"}}, only_free=False)
    assert cm.check(c, catalog(model("v/paid"))) == []


def test_missing_primary_fails() -> None:
    failures = cm.check(cfg({"author": {"model": "v/gone:free"}}), catalog())
    assert failures == ["author: primary v/gone:free is missing from the OpenRouter catalog"]


@pytest.mark.parametrize(
    ("role", "cap"), [("tutor", "tools"), ("structured", "structured_outputs"), ("vision", "image")]
)
def test_primary_without_required_capability_fails(role: str, cap: str) -> None:
    failures = cm.check(cfg({role: {"model": "v/m:free"}}), catalog(model("v/m:free", {"text"})))
    assert failures == [f"{role}: primary v/m:free lacks ['{cap}']"]


def test_free_suffix_that_started_charging_fails() -> None:
    failures = cm.check(cfg({"router": {"model": "v/r:free"}}), catalog(model("v/r:free", price=0.000001)))
    assert failures == ["router: v/r:free is no longer priced at $0"]


def test_repo_config_obeys_free_only_rule() -> None:
    config = yaml.safe_load((REPO_ROOT / "config" / "models.yaml").read_text())
    assert config["budget"]["only_free_ids_in_chains"] is True
    for role, spec in config["roles"].items():
        if role in cm.NOT_OPENROUTER_ROLES:
            continue
        for mid in [spec["model"], *(spec.get("fallbacks") or [])]:
            assert cm.is_free_id(mid), f"{role}: {mid}"


def test_main_with_missing_config_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        cm.main(["--config", str(tmp_path / "nope.yaml")])
