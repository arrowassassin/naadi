"""Row totals of the S23 content packs, counted straight from data/content/<pack>/*.json.

The seed tests pin the legacy bundle (data/chapters.json, questions.json, figures.json) to fixed numbers and add
these computed pack totals, so authors adding content never edit a test, while a loader or importer that silently
drops pack rows still fails (expected = legacy + sum over packs). Read independently of naadi_api.seed on purpose.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
CONTENT = REPO / "data" / "content"

# The legacy bundle (segment 05): fixed, never edited by S23 authors.
LEGACY_CHAPTERS = 14
LEGACY_SECTIONS = 259
LEGACY_QUESTIONS = 685
LEGACY_FIGURES = 95


@dataclass(frozen=True)
class PackTotals:
    packs: int
    chapters: int
    sections: int
    section_figures: int  # [[figure:id]] placements in pack sections (one section_media row each)
    items: int
    sets: int
    cards: int  # authored cards.json plus tools/generate_cards.py output (cards.generated.json)
    figures: int


def _read(path: Path, default: object) -> object:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def pack_totals(content: Path = CONTENT) -> PackTotals:
    packs = chapters = sections = section_figures = items = sets = cards = figures = 0
    for d in sorted(p for p in content.iterdir() if p.is_dir() and (p / "pack.json").is_file()):
        packs += 1
        chs = _read(d / "chapters.json", [])
        assert isinstance(chs, list)
        chapters += len(chs)
        for c in chs:
            sections += len(c["sections"])
            section_figures += sum(len(s.get("figureIds", [])) for s in c["sections"])
        qs = _read(d / "questions.json", {})
        assert isinstance(qs, dict)
        items += len(qs.get("items", []))
        sets += len(qs.get("sets", []))
        cs = _read(d / "cards.json", []) + _read(d / "cards.generated.json", [])  # type: ignore[operator]
        fs = _read(d / "figures.json", [])
        assert isinstance(cs, list) and isinstance(fs, list)
        cards += len(cs)
        figures += len(fs)
    return PackTotals(packs, chapters, sections, section_figures, items, sets, cards, figures)
