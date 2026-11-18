#!/usr/bin/env python3
"""Lint question items against config/authoring_rules.md Part B (the same rules the author graph and the admin
review queue run: `naadi_api/authoring/lint.py`).

Usage (from apps/api, where the project's virtualenv has naadi_api):
    uv run python ../../tools/lint_items.py ../../data/questions.json --seed
    uv run python ../../tools/lint_items.py my_item.json            # one item or a list; seed or row shape
    uv run python ../../tools/lint_items.py items.json --json       # machine-readable findings

Items are read from a JSON file (an object, a list, or JSON lines). Taxonomy ids come from the item
(`taxonomy_ids`), else from the seed's `data/question_taxonomy_map.json` when `--seed` is given. `--seed` also
treats a missing educational objective as a warning (the shipped seed items have none).

Exit code 1 when any item has an ERROR finding (WARN and INFO never fail the run), 0 otherwise. Prints rule ids,
item ids and counts only: it never sends anything anywhere.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from naadi_api.authoring.lint import LintContext, SourceInfo, lab_refs, lint_question

ROOT = Path(__file__).resolve().parents[1]


def load_items(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(data, dict):
        data = data.get("items", [data]) if "items" in data else [data]
    if not isinstance(data, list):
        raise SystemExit(f"{path}: expected an object, a list or JSON lines")
    return [d for d in data if isinstance(d, dict)]


def build_context(config: Path, data: Path, *, seed: bool) -> LintContext:
    taxonomy = {n["id"]: n["kind"] for n in json.loads((data / "taxonomy.json").read_text(encoding="utf-8"))}
    sources_raw = yaml.safe_load((config / "sources.yaml").read_text(encoding="utf-8"))["sources"]
    sources = {s["id"]: SourceInfo(bool(s.get("allow_ai_ingest")), str(s.get("license") or "")) for s in sources_raw}
    app = yaml.safe_load((config / "app_config.yaml").read_text(encoding="utf-8"))
    return LintContext(
        taxonomy=taxonomy, sources=sources, labs=lab_refs(app.get("lab_values", {})), objective_optional=seed
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("items", type=Path, help="JSON file with the item(s)")
    p.add_argument("--config", type=Path, default=ROOT / "config")
    p.add_argument("--data", type=Path, default=ROOT / "data")
    p.add_argument("--seed", action="store_true", help="seed-bank items: map taxonomy ids; objective is optional")
    p.add_argument("--json", action="store_true", help="print findings as JSON lines")
    p.add_argument("--quiet", action="store_true", help="print only the summary")
    args = p.parse_args(argv)

    ctx = build_context(args.config, args.data, seed=args.seed)
    tax_map: dict[str, list[str]] = {}
    if args.seed and (args.data / "question_taxonomy_map.json").is_file():
        tax_map = json.loads((args.data / "question_taxonomy_map.json").read_text(encoding="utf-8"))
    items = load_items(args.items)
    by_rule: Counter[tuple[str, str]] = Counter()
    failed = 0
    for i, raw in enumerate(items):
        item = dict(raw)
        iid = str(item.get("id", f"#{i + 1}"))
        if args.seed and not item.get("taxonomy_ids") and not item.get("taxonomyIds"):
            item["taxonomy_ids"] = tax_map.get(iid, [])
        report = lint_question(item, ctx)
        failed += 0 if report.ok else 1
        for f in report.findings:
            by_rule[(f.rule, f.severity)] += 1
            if args.json:
                print(json.dumps({"item": iid, **f.as_dict()}, ensure_ascii=False))
            elif not args.quiet and f.severity == "error":
                print(f"ERROR {f.rule} {iid} {f.target} {json.dumps(f.params, ensure_ascii=False)}")
    print(f"{len(items)} items, {failed} with errors", file=sys.stderr)
    for (rule, sev), n in sorted(by_rule.items()):
        print(f"  {rule:4} {sev:5} {n}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
