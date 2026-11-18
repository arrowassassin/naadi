#!/usr/bin/env python3
"""USMLE-2026 blueprint coverage from the seed bundle (data/ + every content pack); no database.

Usage (from apps/api, where the project's virtualenv has naadi_api):
    uv run python ../../tools/coverage.py                 # both Steps: per-system summary + areas below minimum
    uv run python ../../tools/coverage.py --exam step1 --areas        # every area row
    uv run python ../../tools/coverage.py --json
    uv run python ../../tools/coverage.py --enforce       # exit 1 if any area is below its minimum

Without --enforce, the exit code follows config/content_coverage.yaml: `s23_complete: true` (set when segment 23
is marked complete) makes any area below its minimum a failure; until then the report is informational. The same
computation backs the admin page /admin/blueprint (naadi_api/content/coverage.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import yaml

from naadi_api.content.coverage import Leaf, Tagged, Target, blueprint_coverage
from naadi_api.seed.data import load_bundle

ROOT = Path(__file__).resolve().parents[1]
STEPS = ("step1", "step2ck")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=ROOT / "data")
    p.add_argument("--config", type=Path, default=ROOT / "config")
    p.add_argument("--exam", choices=STEPS)
    p.add_argument("--areas", action="store_true", help="print every area row")
    p.add_argument("--json", action="store_true")
    p.add_argument("--enforce", action="store_true", help="fail when any area is below its minimum")
    args = p.parse_args(argv)
    b = load_bundle(args.data, args.config)
    assert b.outline is not None
    systems = {n.code: n.label for n in b.outline.nodes if n.kind == "outline_system"}
    leaves = [
        Leaf(n.id, str(n.code), n.label, str(n.code)[:2], systems[str(n.code)[:2]]) for n in b.outline.nodes if n.leaf
    ]
    targets = {(r.node_id, r.exam): Target(r.min_items, r.target_items, r.tested) for r in b.outline.blueprint}
    items = [
        Tagged("items", q.exam, frozenset(b.question_map.get(q.id, []))) for q in b.questions if q.status == "published"
    ]
    items += [Tagged("sections", None, frozenset(s.taxonomy_ids or ())) for _c, _i, s in b.sections]
    items += [Tagged("cards", None, frozenset(b.outline.with_ancestors(c.areas))) for _p, c in b.cards]
    gate = yaml.safe_load((args.config / "content_coverage.yaml").read_text(encoding="utf-8")) or {}
    enforce = args.enforce or bool(gate.get("s23_complete"))
    reports = [blueprint_coverage(leaves, targets, items, st) for st in (args.exam,) if st] or [
        blueprint_coverage(leaves, targets, items, st) for st in STEPS
    ]
    failing = sum(len(r.below_min) for r in reports)
    if args.json:
        print(
            json.dumps(
                {
                    "enforced": enforce,
                    "ok": not (enforce and failing),
                    "reports": [
                        {"exam": r.exam, "items": r.items, "systems": [asdict(s) for s in r.systems]}
                        | {"areas": [asdict(a) for a in r.areas]}
                        for r in reports
                    ],
                },
                indent=1,
            )
        )
    else:
        for r in reports:
            target = sum(a.target_items for a in r.areas)
            below = f"{len(r.below_min)} of {len(r.areas)} areas below min"
            print(f"{r.exam}: {r.items} item-tags / target {target}; {below}")
            for s in r.systems:
                print(
                    f"  {s.code} {s.label[:44]:44} items {s.items:5}/{s.target_items:<5}"
                    f" at-min {s.areas_at_min:3}/{s.areas:<3} met {s.areas_met:3}"
                )
            for a in r.areas if args.areas else []:
                print(f"    {a.id:24} {a.status:12} {a.items:4}/{a.target_items:<4} min {a.min_items}  {a.label}")
        state = "enforced" if enforce else "report only (s23_complete: false)"
        print(f"coverage gate: {state}; {failing} area-step(s) below minimum", file=sys.stderr)
    return 1 if enforce and failing else 0


if __name__ == "__main__":
    raise SystemExit(main())
