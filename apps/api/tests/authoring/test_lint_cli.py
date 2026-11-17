"""tools/lint_items.py: the CLI over the same linter."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from .fixtures import ROOT, good_item, variant


def load_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("lint_items", ROOT / "tools" / "lint_items.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_clean_item_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "item.json"
    f.write_text(json.dumps(good_item()))
    assert load_cli().main([str(f)]) == 0
    assert "1 items, 0 with errors" in capsys.readouterr().err


def test_an_error_exits_one_and_names_the_rule_and_item(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = {**variant(answer="Z"), "id": "x-1"}
    f = tmp_path / "items.json"
    f.write_text(json.dumps([bad, good_item()]))
    assert load_cli().main([str(f)]) == 1
    out = capsys.readouterr()
    assert "ERROR S02 x-1 answer" in out.out and "2 items, 1 with errors" in out.err


def test_json_lines_and_machine_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "items.jsonl"
    f.write_text("\n".join(json.dumps({**variant(answer="Z"), "id": f"q{i}"}) for i in range(2)))
    assert load_cli().main([str(f), "--json"]) == 1
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {line["item"] for line in lines} == {"q0", "q1"} and all(
        "rule" in line and "severity" in line for line in lines
    )


def test_the_shipped_seed_bank_runs_through_the_cli(capsys: pytest.CaptureFixture[str]) -> None:
    load_cli().main([str(ROOT / "data" / "questions.json"), "--seed", "--quiet"])
    assert "685 items" in capsys.readouterr().err
