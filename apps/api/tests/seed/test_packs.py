"""Content-pack loading (naadi_api/seed/packs.py + data.merge_content): the sample pack loads, and each kind of
authoring mistake is reported with the pack, the id and what to fix."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from naadi_api.seed.data import SeedDataError, load_bundle
from naadi_api.settings import REPO_ROOT

from .content_fixture import FIXTURE_PACK, data_dir_with_sample_pack

CONFIG = REPO_ROOT / "config"


def _with_edit(tmp_path: Path, file: str, edit: Callable[[Any], None]) -> Path:
    data = data_dir_with_sample_pack(tmp_path)
    pack = data / "content" / FIXTURE_PACK.name
    pack.unlink()
    shutil.copytree(FIXTURE_PACK, pack)
    doc = json.loads((pack / file).read_text(encoding="utf-8"))
    edit(doc)
    (pack / file).write_text(json.dumps(doc), encoding="utf-8")
    return data


def _problems(data: Path) -> list[str]:
    with pytest.raises(SeedDataError) as exc:
        load_bundle(data, CONFIG)
    return exc.value.problems


def test_sample_pack_loads(tmp_path: Path) -> None:
    b = load_bundle(data_dir_with_sample_pack(tmp_path), CONFIG)
    q = next(q for q in b.questions if q.id == "smp-q04")
    assert (q.item_format, q.set_id, q.set_order, q.difficulty, q.pack_id) == (
        "sequential",
        "smp-set02",
        1,
        "easy",
        "sample-pack",
    )
    assert q.answer_html == "Answer: B. Primary PCI"
    assert [s.id for _p, s in b.sets] == ["smp-set01", "smp-set02"]
    assert "usmle2026.08.08.01" in b.question_map["ch05-q01"]
    fig = next(f for f in b.figures if f.id == "smp-fig01")
    assert fig.file == "content/sample-pack/figures/smp-fig01.svg"


@pytest.mark.parametrize(
    ("file", "edit", "expect"),
    [
        ("questions.json", lambda d: d["items"][0]["whyNot"].pop("A"), "whyNot needs exactly the wrong options"),
        ("questions.json", lambda d: d["items"][0].update(id="xyz-q01"), "must start with the pack prefix"),
        ("questions.json", lambda d: d["items"][0].update(areas=["usmle2026.08.05"]), "not USMLE-2026 leaf areas"),
        ("questions.json", lambda d: d["items"][0].update(disciplines=["astrology"]), "unknown discipline"),
        ("questions.json", lambda d: d["items"][0]["options"].pop(), "at least 5"),
        ("questions.json", lambda d: d["items"].pop(4), "set smp-set02 (sequential) needs 2-3 items"),
        ("questions.json", lambda d: d["items"][0].update(source="statpearls-typo"), "is not seedable"),
        ("questions.json", lambda d: d["sets"][0].update(introHtml=""), "an abstract set needs introHtml"),
        ("cards.json", lambda d: d[0].update(sectionId="smp-s99"), "unknown section smp-s99"),
        ("pack.json", lambda d: d.update(retag={"nope-q1": ["usmle2026.08.08.01"]}), "retag of unknown legacy"),
        ("chapters.json", lambda d: d[0]["sections"][0].update(html="<p>changed</p>"), "is stale"),
        (
            "chapters.json",
            lambda d: d[0]["sections"][0]["furtherReading"][0].update(sourceId="nowhere"),
            "further reading source nowhere",
        ),
    ],
)
def test_authoring_mistakes_are_reported(tmp_path: Path, file: str, edit: Callable[[Any], None], expect: str) -> None:
    problems = _problems(_with_edit(tmp_path, file, edit))
    assert any(expect in p for p in problems), problems
