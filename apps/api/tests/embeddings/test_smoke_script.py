"""scripts/smoke_search.py: exit codes and the p95 gate (the server is simulated; unit test of the script itself)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import respx

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke_search.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("smoke_search", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BASE = "https://space.example.test"


def test_needs_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMOKE_ACCESS_TOKEN", raising=False)
    assert load().main(["--base-url", BASE]) == 2


@respx.mock
def test_passes_under_budget_and_fails_on_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SMOKE_ACCESS_TOKEN", "t" * 20)
    respx.get(f"{BASE}/api/courses").mock(
        return_value=httpx.Response(200, json={"chapters": [{"title": "Step 1 · Renal"}, {"title": "Step 1 · Heart"}]})
    )
    search = respx.get(f"{BASE}/api/search").mock(return_value=httpx.Response(200, json={"hits": []}))
    mod = load()
    assert mod.main(["--base-url", BASE, "--runs", "6"]) == 0
    assert "p95" in capsys.readouterr().out and "t" * 20 not in capsys.readouterr().out
    assert search.calls.last.request.url.params["q"] in {"Renal", "Heart"}
    search.mock(return_value=httpx.Response(503, json={}))
    assert mod.main(["--base-url", BASE, "--runs", "4"]) == 1
    assert mod.main(["--base-url", BASE, "--runs", "4", "--budget-ms", "0"]) == 1


def test_percentile_helper() -> None:
    mod = load()
    assert mod.pct([10.0, 20.0, 30.0, 40.0, 1000.0], 95) == 1000.0
    assert mod.pct([5.0], 95) == 5.0
