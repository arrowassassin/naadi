"""The deterministic leak probe (tools/model_eval/leak_probe.py) passes, and it does catch a leak when the
reveal gate is broken (negative control), so a green probe means something."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[4]
CONFIG = REPO / "config"


def _probe() -> Any:
    spec = importlib.util.spec_from_file_location("leak_probe", REPO / "tools" / "model_eval" / "leak_probe.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["leak_probe"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> Any:
    from naadi_api.agent.graphs import tutor
    from naadi_api.agent.tools import knowledge
    from naadi_api.assistant import questions

    # The probe patches module globals; restore them after each test.
    for mod, names in (
        (tutor, ("_config", "_flags_for", "_exam_active", "verify_page", "agent_db")),
        (tutor.threads, ("record_turn",)),
        (questions, ("_conn_rows",)),
        (knowledge, ("search_local", "get_engine")),
    ):
        for n in names:
            monkeypatch.setattr(mod, n, getattr(mod, n))
    return _probe()


async def test_twenty_adversarial_prompts_leak_nothing(probe: Any) -> None:
    assert len(probe.PROMPTS) == 20
    assert await probe.probe(CONFIG) == []


async def test_the_probe_catches_a_broken_reveal_gate(probe: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from naadi_api.assistant import questions

    async def broken(db: Any, user_id: Any, ids: list[str]) -> set[str]:
        return set(ids)  # pretends every question was answered

    original = probe.install

    def install(config_dir: Any) -> Any:
        out = original(config_dir)
        monkeypatch.setattr(questions, "revealed_questions", broken)
        return out

    monkeypatch.setattr(probe, "install", install)
    failures = await probe.probe(CONFIG)
    assert len(failures) >= 15, failures
