"""Naadi-original fixtures for the authoring tests (never text from a commercial or official bank)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from naadi_api.authoring.lint import LabRef, LintContext, SourceInfo, lab_refs

ROOT = Path(__file__).resolve().parents[4]
TAXONOMY_IDS = [
    "step1.sys.respiratory_renal",
    "step1.disc.microbiology",
    "step1.comp.medical_knowledge",
    "topic.renal.stones_urinary_tract",
]

STEM = (
    "<p>A 34-year-old woman comes to the clinic because of burning with urination for 2 days. She has no fever or "
    "flank pain. Her temperature is 37.0°C (98.6°F), pulse is 72/min, and blood pressure is 118/74 mm Hg. "
    "Examination of the abdomen shows mild suprapubic tenderness. Urinalysis shows positive leukocyte esterase and "
    "nitrite. Urine culture grows a gram-negative rod that ferments lactose and produces indole.</p>"
    "<p>Which of the following is the most likely causal organism?</p>"
)


def good_item() -> dict[str, Any]:
    return {
        "exam": "step1",
        "stem_html": STEM,
        "options": [
            {"label": "A", "html": "Escherichia coli"},
            {"label": "B", "html": "Klebsiella pneumoniae"},
            {"label": "C", "html": "Proteus mirabilis"},
            {"label": "D", "html": "Pseudomonas aeruginosa"},
            {"label": "E", "html": "Staphylococcus saprophyticus"},
        ],
        "answer": "A",
        "explanation_html": "<p>Escherichia coli is the commonest cause of uncomplicated cystitis.</p>",
        "why_not_html": (
            "Why the others are wrong: B — Klebsiella is mucoid and lactose positive but indole negative. "
            "C — Proteus does not ferment lactose. D — Pseudomonas does not ferment lactose. "
            "E — Staphylococcus is a gram-positive coccus."
        ),
        "educational_objective": "Identify the usual cause of uncomplicated cystitis from culture findings.",
        "difficulty": "easy",
        "taxonomy_ids": list(TAXONOMY_IDS),
        "source_id": "naadi-original-v1",
    }


def variant(**changes: Any) -> dict[str, Any]:
    item = copy.deepcopy(good_item())
    item.update(changes)
    return item


def with_option(item: dict[str, Any], label: str, html: str) -> dict[str, Any]:
    out = copy.deepcopy(item)
    for o in out["options"]:
        if o["label"] == label:
            o["html"] = html
    return out


def taxonomy_kinds() -> dict[str, str]:
    nodes = json.loads((ROOT / "data" / "taxonomy.json").read_text())
    return {n["id"]: n["kind"] for n in nodes}


def labs() -> list[LabRef]:
    import yaml

    cfg = yaml.safe_load((ROOT / "config" / "app_config.yaml").read_text())
    return lab_refs(cfg["lab_values"])


def context(**kw: Any) -> LintContext:
    base: dict[str, Any] = {
        "taxonomy": taxonomy_kinds(),
        "sources": {
            "naadi-original-v1": SourceInfo(True, "proprietary-naadi"),
            "youtube": SourceInfo(False, "youtube-embed-only"),
            "statpearls": SourceInfo(False, "CC-BY-NC-ND-4.0"),
            "cdc": SourceInfo(True, "public-domain-us-gov"),
        },
        "labs": labs(),
    }
    base.update(kw)
    return LintContext(**base)
