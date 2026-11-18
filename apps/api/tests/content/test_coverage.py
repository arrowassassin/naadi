"""Blueprint coverage (naadi_api/content/coverage.py and tools/coverage.py)."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from naadi_api.content.coverage import Leaf, Tagged, Target, blueprint_coverage, status_of
from naadi_api.settings import REPO_ROOT

LEAVES = [Leaf("a.1", "08.01.01", "One", "08", "Cardio"), Leaf("a.2", "08.02.01", "Two", "08", "Cardio")]
TARGETS = {("a.1", "step1"): Target(6, 10), ("a.2", "step1"): Target(6, 8), ("a.1", "step2ck"): Target(6, 6)}


def test_status_words() -> None:
    t = Target(6, 10)
    assert [status_of(n, t) for n in (0, 5, 6, 9, 10, 12)] == [
        "below_min",
        "below_min",
        "below_target",
        "below_target",
        "met",
        "met",
    ]


def test_items_count_on_their_step_and_sections_on_every_step() -> None:
    items = [Tagged("items", "step1", frozenset({"a.1", "x"}))] * 10
    items += [Tagged("items", "step2ck", frozenset({"a.2"}))] * 3  # a.2 is not tested on step2ck here
    items += [Tagged("sections", None, frozenset({"a.2"})), Tagged("cards", None, frozenset({"a.1"}))]
    s1 = blueprint_coverage(LEAVES, TARGETS, items, "step1")
    assert [(a.id, a.items, a.sections, a.cards, a.status) for a in s1.areas] == [
        ("a.1", 10, 0, 1, "met"),
        ("a.2", 0, 1, 0, "below_min"),
    ]
    assert (s1.systems[0].areas, s1.systems[0].areas_at_min, s1.systems[0].areas_met) == (2, 1, 1)
    s2 = blueprint_coverage(LEAVES, TARGETS, items, "step2ck")
    assert [a.id for a in s2.areas] == ["a.1"]
    assert s2.below_min[0].items == 0


def _tool():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("coverage_tool", REPO_ROOT / "tools" / "coverage.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tool_reports_until_s23_is_complete_then_enforces(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tool = _tool()
    assert tool.main(["--exam", "step1"]) == 0  # s23_complete: false ships
    assert "report only" in capsys.readouterr().err
    assert tool.main(["--exam", "step1", "--enforce"]) == 1
    config = tmp_path / "config"
    shutil.copytree(REPO_ROOT / "config", config)
    (config / "content_coverage.yaml").write_text("s23_complete: true\n", encoding="utf-8")
    assert tool.main(["--exam", "step2ck", "--config", str(config)]) == 1
    assert "enforced" in capsys.readouterr().err
