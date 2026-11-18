#!/usr/bin/env python3
"""Validate the content library: the legacy bundle, every pack in data/content/ and the USMLE-2026 taxonomy.

Usage (from apps/api, where the project's virtualenv has naadi_api):
    uv run python ../../tools/validate_content.py                    # everything
    uv run python ../../tools/validate_content.py --pack cardio-resp # report one pack's counts (checks run on all)
    uv run python ../../tools/validate_content.py --json             # machine-readable

Checks (docs/CONTENT_AUTHORING.md):
  * schema: every pack file parses against its model (naadi_api/seed/packs.py); ids carry the pack prefix;
  * references: areas are USMLE-2026 leaf ids; discipline / clinical-science / task slugs exist; sets, figures,
    chapters, sections, cards and sources resolve; further-reading and reference sources are in sources.yaml;
  * items: 5+ options labelled A.. in order, the key is an option, whyNot covers every wrong option and nothing
    else, sequential sets have 2-3 items and abstract sets 2+, numbered 1..n;
  * section bodies: data/content/<pack>/section_bodies.json is fresh (`pnpm --filter @naadi/shared seed:convert`).

Phase 2 of S23-0 adds item-writing lint, units and lab ranges, the licence gate and the originality check to
this command. Exit 1 when any problem is found. Prints ids and counts only; it never sends anything anywhere.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from naadi_api.content.validate import review_sample, validate
from naadi_api.seed.data import SeedDataError, load_bundle

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=ROOT / "data")
    p.add_argument("--config", type=Path, default=ROOT / "config")
    p.add_argument("--pack", help="only report this pack's counts")
    p.add_argument("--json", action="store_true")
    p.add_argument("--sample", action="store_true", help="print the stratified 15%% manual review sample (S23-R)")
    p.add_argument(
        "--areas",
        action="store_true",
        help="list the leaf areas of the pack's systems (or all) with Step targets and current item counts",
    )
    args = p.parse_args(argv)
    try:
        b = load_bundle(args.data, args.config)
    except SeedDataError as exc:
        if args.json:
            print(json.dumps({"ok": False, "problems": exc.problems}, indent=1))
        else:
            for line in exc.problems:
                print(f"ERROR {line}", file=sys.stderr)
            print(f"{len(exc.problems)} problem(s)", file=sys.stderr)
        return 1
    report = validate(b, args.config)
    if args.sample:
        for qid in review_sample(b):
            print(qid)
        return 0
    findings = [f for f in report.findings if args.pack in (None, f.pack)]
    packs = [pk for pk in b.packs if args.pack in (None, pk.meta.id)]
    if args.pack and not packs:
        print(f"no pack {args.pack!r} in {args.data / 'content'}", file=sys.stderr)
        return 1
    summary = []
    for pk in packs:
        steps = Counter(q.exam for q in pk.items)
        summary.append(
            {
                "pack": pk.meta.id,
                "chapters": len(pk.chapters),
                "sections": sum(len(c.sections) for c in pk.chapters),
                "items": len(pk.items),
                "step1": steps["step1"],
                "step2ck": steps["step2ck"],
                "sets": len(pk.sets),
                "cards": len(pk.cards),
                "figures": len(pk.figures),
            }
        )
    if args.areas and b.outline is not None:
        systems = {s for pk in packs for s in pk.meta.systems} if args.pack else None
        have: Counter[tuple[str, str]] = Counter()
        for q in b.questions:
            if q.status != "retired":
                for t in b.question_map.get(q.id, []):
                    have[(t, q.exam)] += 1
        target = {(r.node_id, r.exam): r.target_items for r in b.outline.blueprint}
        for n in b.outline.nodes:
            if not n.leaf or (systems is not None and str(n.code)[:2] not in systems):
                continue
            cells = [f"{st} {have[(n.id, st)]}/{target.get((n.id, st), 0)}" for st in ("step1", "step2ck")]
            print(f"{n.id:24} {'  '.join(cells):24} {n.label}")
    ok = report.ok
    if args.json:
        doc = {"ok": ok, "originality": report.originality_mode, "packs": summary}
        print(json.dumps(doc | {"findings": [f.as_dict() for f in findings]}, indent=1))
    else:
        for row in summary:
            print("  ".join(f"{k}={v}" for k, v in row.items()))
        for f in findings:
            print(f"{f.severity.upper():5} {f.check:11} {f.ref}: {f.detail}", file=sys.stderr)
        mode = "forbidden-corpus fingerprints" if report.originality_mode == "fingerprints" else "fallback"
        print(
            f"{'ok' if ok else 'FAILED'}: {len(b.questions)} items, {len(b.sections)} sections, {len(b.cards)} pack"
            f" cards; {len(report.errors)} error(s), {len(report.findings) - len(report.errors)} warning(s);"
            f" originality check: {mode}",
            file=sys.stderr,
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
