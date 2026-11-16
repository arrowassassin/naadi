#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = ["openrouter==1.3.179", "pyyaml==6.0.3"]
# ///
"""Verify every OpenRouter model in config/models.yaml against the live catalog (official `openrouter` SDK).

Usage:
    uv run tools/check_models.py [--config config/models.yaml]
    (from apps/api:) uv run python ../../tools/check_models.py

Checks, per role under `roles:` (other top-level blocks such as `generation:` are not OpenRouter models):
  - `only_free_ids_in_chains`: every primary and fallback ID ends with `:free`, except the explicit free
    router `openrouter/free`. A paid ID in a chain is billed at the paid price.
  - the primary exists in the catalog and has the capability its role needs (tools / structured outputs /
    image input). Missing fallbacks are reported but do not fail the run.
  - every listed ID is actually priced at $0 (catches a `:free` variant that started charging).
Roles served outside OpenRouter (local embeddings, speech-to-text chain) are listed and skipped.

Exit code 1 if any rule fails. The model catalog is public, so no API key is needed; OPENROUTER_API_KEY is
used when present (same catalog, higher rate limit).
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from openrouter import OpenRouter

FREE_ROUTER = "openrouter/free"
NOT_OPENROUTER_ROLES = {"embeddings", "speech_to_text"}
NEEDS: dict[str, set[str]] = {
    "tutor": {"tools"},
    "structured": {"structured_outputs"},
    "vision": {"image"},
}
ROOT = Path(__file__).resolve().parent.parent


@dataclass
class CatalogModel:
    id: str
    context_length: int | None
    capabilities: set[str]
    prompt_price: float
    completion_price: float


def is_free_id(model_id: str) -> bool:
    return model_id == FREE_ROUTER or model_id.endswith(":free")


def _price(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return float("inf")


def fetch_catalog(api_key: str | None) -> dict[str, CatalogModel]:
    out: dict[str, CatalogModel] = {}
    with OpenRouter(api_key=api_key, timeout_ms=60_000) as client:
        page = client.models.list(limit=500)
        while page is not None:
            for m in page.result.data:
                arch = m.architecture
                caps = set(m.supported_parameters or []) | set(getattr(arch, "input_modalities", None) or [])
                out[m.id] = CatalogModel(
                    id=m.id,
                    context_length=m.context_length,
                    capabilities={str(c) for c in caps},
                    prompt_price=_price(getattr(m.pricing, "prompt", 0)),
                    completion_price=_price(getattr(m.pricing, "completion", 0)),
                )
            page = page.next() if page.next is not None else None
    return out


def check(config: dict[str, Any], catalog: dict[str, CatalogModel]) -> list[str]:
    """Return the list of failures (empty = pass). Prints a line per model."""
    failures: list[str] = []
    only_free = bool((config.get("budget") or {}).get("only_free_ids_in_chains", False))
    roles = config.get("roles") or {}
    if not roles:
        return ["config has no `roles:` block"]
    for role, spec in roles.items():
        if role in NOT_OPENROUTER_ROLES:
            print(f"{role:14s} skipped (served outside OpenRouter)")
            continue
        primary = spec.get("model")
        if not primary:
            failures.append(f"{role}: no primary `model`")
            continue
        chain = [primary, *(spec.get("fallbacks") or [])]
        for i, mid in enumerate(chain):
            tag = "primary " if i == 0 else "fallback"
            if only_free and not is_free_id(mid):
                failures.append(f"{role}: {mid} is not a :free ID (only_free_ids_in_chains)")
                print(f"{role:14s} {tag} {mid:48s} NOT FREE")
                continue
            m = catalog.get(mid)
            if m is None:
                print(f"{role:14s} {tag} {mid:48s} MISSING from catalog")
                if i == 0:
                    failures.append(f"{role}: primary {mid} is missing from the OpenRouter catalog")
                continue
            if mid != FREE_ROUTER and (m.prompt_price > 0 or m.completion_price > 0):
                failures.append(f"{role}: {mid} is no longer priced at $0")
            missing = NEEDS.get(role, set()) - m.capabilities
            price = f"${m.prompt_price * 1e6:.2f}/${m.completion_price * 1e6:.2f} per 1M"
            status = f"MISSING CAP {sorted(missing)}" if missing else "ok"
            print(f"{role:14s} {tag} {mid:48s} ctx={m.context_length} {price} {status}")
            if missing and i == 0:
                failures.append(f"{role}: primary {mid} lacks {sorted(missing)}")
    return failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=ROOT / "config" / "models.yaml")
    args = ap.parse_args(argv)
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    catalog = fetch_catalog(os.environ.get("OPENROUTER_API_KEY") or None)
    print(f"OpenRouter catalog: {len(catalog)} models, {sum(is_free_id(i) for i in catalog)} free IDs\n")
    failures = check(config, catalog)
    if failures:
        print("\nFAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS: every chain is free and every primary exists with its required capability")
    return 0


if __name__ == "__main__":
    sys.exit(main())
