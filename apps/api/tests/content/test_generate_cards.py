"""tools/generate_cards.py: key-fact cloze cards and objective cards from a pack's own text."""

from __future__ import annotations

import importlib.util
import json

from naadi_api.settings import REPO_ROOT

from ..seed.content_fixture import FIXTURE_PACK


def _tool():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("generate_cards", REPO_ROOT / "tools" / "generate_cards.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_committed_files_are_fresh() -> None:
    roots = ["--root", str(REPO_ROOT / "data" / "content"), "--root", str(FIXTURE_PACK.parent)]
    assert _tool().main(["--check", *roots]) == 0


def test_cloze_and_objective_cards() -> None:
    cards = json.loads(_tool().build(FIXTURE_PACK))
    cloze = [c for c in cards if "key-fact" in c["tags"]]
    assert len(cloze) == 2  # two <span class="hy"> key facts in smp-s01
    assert "[…]" in cloze[0]["frontHtml"] and "coronary blood flow" not in cloze[0]["frontHtml"]
    assert "<strong>coronary blood flow</strong>" in cloze[0]["backHtml"]
    assert all(c["sectionId"] == "smp-s01" and c["questionId"] is None for c in cloze)
    objective = {c["questionId"]: c for c in cards if "objective" in c["tags"]}
    assert set(objective) == {"smp-q01", "smp-q02", "smp-q03", "smp-q04", "smp-q05"}
    assert objective["smp-q01"]["backHtml"] == "<p>the heart raises supply by raising flow, not extraction.</p>"
    assert all(c["id"].startswith("smp-g-") for c in cards)
    assert len({c["id"] for c in cards}) == len(cards)
