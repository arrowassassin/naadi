#!/usr/bin/env python3
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "openrouter==1.3.179", "pyyaml==6.0.3", "langchain-nvidia-ai-endpoints==1.5.0",
#   "httpx==0.28.1", "psycopg[binary]==3.3.9",
# ]
# ///
"""Verify every LLM role chain in config/models.yaml against its provider.

Usage:
    uv run tools/check_models.py [--config config/models.yaml] [--app-config auto|db|management|off]
    (from apps/api:) uv run python ../../tools/check_models.py

Two sources are checked with the same rules: the YAML (the seed) and the LIVE routing in the
`app_config` table (keys `models` + `budget`, what llm.py actually uses; an admin can edit them).
`--app-config` picks how the table is read: `db` = DATABASE_URL (psycopg), `management` = the Supabase
Management API SQL endpoint, read-only (SUPABASE_URL + SUPABASE_ACCESS_TOKEN; the build sandbox has no raw
Postgres egress), `auto` (default) = the first of those that is configured, `off` = YAML only. A missing
row is reported (not seeded yet) and is not a failure; a paid ID in the table is.

Each role under `roles:` with a `chain:` is an ordered list of `{provider, model}` (providers under
`providers:`). Roles without a chain (local embeddings, speech-to-text) and the `generation:` block are
not LLM chains and are listed as skipped.

OpenRouter entries (public catalog via the official `openrouter` SDK, `models.list()`):
  - `only_free_ids_in_chains` / `providers.openrouter.only_free_ids`: every ID ends with `:free`, except the
    explicit free router `openrouter/free`. A paid ID in a chain is billed at the paid price.
  - every listed ID is priced at $0 (catches a `:free` variant that started charging).
  - the chain's first entry (the primary) exists and has the capability its role needs.
NVIDIA entries (`langchain-nvidia-ai-endpoints` `ChatNVIDIA`):
  - the catalog lists retired models, so each model is PROBED with a one-token call when NVIDIA_API_KEY is
    set. Without the key (CI) the script says so explicitly: the deployed Space's /api/health probes them.
  - the primary's capability comes from the SDK's model profile (`tool_calling`, `image_inputs`,
    `structured_output`); a capability the profile does not declare counts as missing.
Required capabilities: tutor -> tools, vision -> image input, structured -> structured output.
Missing or incapable fallbacks are reported as warnings; llm.py skips them at run time.

Exit code 1 if any rule fails. Prints model IDs and statuses only, never key material.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from openrouter import OpenRouter

FREE_ROUTER = "openrouter/free"
PROVIDERS = ("nvidia", "openrouter")
NEEDS: dict[str, str] = {"tutor": "tools", "vision": "image", "structured": "structured_output"}
# How each provider spells a capability: OpenRouter supported_parameters / input_modalities,
# ChatNVIDIA model profile keys.
OPENROUTER_CAP = {"tools": "tools", "image": "image", "structured_output": "structured_outputs"}
NVIDIA_PROFILE_CAP = {"tools": "tool_calling", "image": "image_inputs", "structured_output": "structured_output"}
NOT_PROBED_IN_CI = "not probed in CI; probed by the Space health check"
ROOT = Path(__file__).resolve().parent.parent
_HTTP_STATUS = re.compile(r"\[(\d{3})\]")


@dataclass
class CatalogModel:
    id: str
    context_length: int | None
    capabilities: set[str]
    prompt_price: float
    completion_price: float


@dataclass
class NvidiaModel:
    id: str
    capabilities: set[str]  # normalized names from NEEDS values
    probe: str | None  # None = answers; else why it failed; NOT_PROBED_IN_CI when there is no key
    latency_ms: float | None = None


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def is_free_id(model_id: str) -> bool:
    return model_id == FREE_ROUTER or model_id.endswith(":free")


def _price(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return float("inf")


def fetch_openrouter_catalog(api_key: str | None) -> dict[str, CatalogModel]:
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


def _nvidia_client(model: str, api_key: str | None) -> Any:
    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    with warnings.catch_warnings():
        # Without a key the SDK warns that hosted NIMs need one; we only read the profile then.
        warnings.simplefilter("ignore", UserWarning)
        return ChatNVIDIA(
            model=model,
            api_key=api_key,
            max_completion_tokens=1,
            temperature=0,
            timeout=20,
            usage_telemetry_enabled=False,
        )


def _describe(exc: BaseException) -> str:
    m = _HTTP_STATUS.search(str(exc))
    return f"HTTP {m.group(1)}" if m else type(exc).__name__


def inspect_nvidia(models: list[str], api_key: str | None) -> dict[str, NvidiaModel]:
    """Read each model's SDK profile; probe it with a one-token call when a key is available."""
    out: dict[str, NvidiaModel] = {}
    for mid in models:
        client = _nvidia_client(mid, api_key)
        profile = dict(client.profile or {})
        caps = {need for need, key in NVIDIA_PROFILE_CAP.items() if profile.get(key) is True}
        if not api_key:
            out[mid] = NvidiaModel(mid, caps, NOT_PROBED_IN_CI)
            continue
        started = time.perf_counter()
        try:
            client.invoke("Reply with OK.")
            probe: str | None = None
        except Exception as exc:  # noqa: BLE001 - any failure means the model cannot be used
            probe = _describe(exc)
        out[mid] = NvidiaModel(mid, caps, probe, round((time.perf_counter() - started) * 1000, 1))
    return out


def chains(config: dict[str, Any]) -> tuple[dict[str, list[dict[str, str]]], list[str], list[str]]:
    """Return (role -> chain, roles without a chain, shape errors)."""
    out: dict[str, list[dict[str, str]]] = {}
    skipped: list[str] = []
    errors: list[str] = []
    providers = config.get("providers") or {}
    for p in providers:
        if p not in PROVIDERS:
            errors.append(f"providers.{p}: unknown provider (known: {', '.join(PROVIDERS)})")
    for role, spec in (config.get("roles") or {}).items():
        chain = (spec or {}).get("chain")
        if chain is None:
            if "model" in (spec or {}) and "engine" not in (spec or {}):
                errors.append(f"{role}: has `model` but no `chain:` (old config shape)")
            else:
                skipped.append(role)
            continue
        if not isinstance(chain, list) or not chain:
            errors.append(f"{role}: `chain` must be a non-empty list")
            continue
        entries: list[dict[str, str]] = []
        for i, e in enumerate(chain):
            if not isinstance(e, dict) or not e.get("provider") or not e.get("model"):
                errors.append(f"{role}: chain[{i}] needs `provider` and `model`")
                continue
            if e["provider"] not in providers:
                errors.append(f"{role}: chain[{i}] provider {e['provider']!r} is not under `providers:`")
                continue
            entries.append({"provider": str(e["provider"]), "model": str(e["model"])})
        out[role] = entries
    if not out and not errors:
        errors.append("config has no role with a `chain:`")
    return out, skipped, errors


def models_for(role_chains: dict[str, list[dict[str, str]]], provider: str) -> list[str]:
    seen: dict[str, None] = {}
    for entries in role_chains.values():
        for e in entries:
            if e["provider"] == provider:
                seen.setdefault(e["model"], None)
    return list(seen)


def check(
    config: dict[str, Any],
    openrouter_catalog: dict[str, CatalogModel],
    nvidia: dict[str, NvidiaModel],
    out: Callable[[str], None] = print,
) -> Report:
    report = Report()
    role_chains, skipped, errors = chains(config)
    report.failures.extend(errors)
    budget = config.get("budget") or {}
    or_cfg = (config.get("providers") or {}).get("openrouter") or {}
    only_free = bool(budget.get("only_free_ids_in_chains")) or bool(or_cfg.get("only_free_ids"))

    for role in skipped:
        out(f"{role:13s} skipped (not an LLM chain)")
    for role, entries in role_chains.items():
        need = NEEDS.get(role)
        for i, e in enumerate(entries):
            primary = i == 0
            tag = "primary " if primary else "fallback"
            mid, provider = e["model"], e["provider"]
            label = f"{role:13s} {tag} {provider:10s} {mid:46s}"

            def problem(msg: str, label: str = label, primary: bool = primary, role: str = role) -> None:
                out(f"{label} {msg}")
                (report.failures if primary else report.warnings).append(f"{role}: {msg}")

            if provider == "openrouter":
                if only_free and not is_free_id(mid):
                    out(f"{label} NOT FREE")
                    report.failures.append(f"{role}: {mid} is not a :free ID (only_free_ids_in_chains)")
                    continue
                m = openrouter_catalog.get(mid)
                if m is None:
                    problem(f"{mid} is missing from the OpenRouter catalog")
                    continue
                if mid != FREE_ROUTER and (m.prompt_price > 0 or m.completion_price > 0):
                    out(f"{label} PRICED")
                    report.failures.append(f"{role}: {mid} is no longer priced at $0")
                    continue
                if need and OPENROUTER_CAP[need] not in m.capabilities and mid != FREE_ROUTER:
                    problem(f"{mid} lacks {need}")
                    continue
                out(f"{label} ok (ctx {m.context_length}, $0)")
            else:
                n = nvidia.get(mid)
                if n is None:
                    problem(f"{mid} was not inspected")
                    continue
                if need and need not in n.capabilities:
                    problem(f"{mid} lacks {need} (not declared by the ChatNVIDIA model profile)")
                if n.probe == NOT_PROBED_IN_CI:
                    out(f"{label} {NOT_PROBED_IN_CI}")
                elif n.probe is not None:
                    problem(f"{mid} does not answer ({n.probe})")
                else:
                    out(f"{label} answers ({n.latency_ms:.0f} ms)")
    return report


APP_CONFIG_SQL = "select key, value from public.app_config where key in ('models', 'budget')"


def _rows_to_config(rows: list[tuple[str, Any]]) -> dict[str, Any] | None:
    values = {k: v for k, v in rows}
    models = values.get("models")
    if not isinstance(models, dict):
        return None
    return {
        "providers": models.get("providers") or {},
        "roles": models.get("roles") or {},
        "budget": values.get("budget") or {},
    }


def app_config_from_db(database_url: str) -> dict[str, Any] | None:
    import psycopg

    with psycopg.connect(database_url, connect_timeout=10) as conn:
        present = conn.execute("select to_regclass('public.app_config') is not null").fetchone()
        if not present or not present[0]:
            return None
        rows = conn.execute(APP_CONFIG_SQL).fetchall()
    return _rows_to_config([(str(r[0]), r[1]) for r in rows])


def app_config_from_management(supabase_url: str, token: str) -> dict[str, Any] | None:
    from urllib.parse import urlparse

    import httpx

    ref = (urlparse(supabase_url).hostname or "").split(".", 1)[0]

    def sql(query: str) -> list[dict[str, Any]]:
        # No SDK covers the Management API SQL endpoint (supabase-py wraps the project APIs only).
        r = httpx.post(
            f"https://api.supabase.com/v1/projects/{ref}/database/query",
            json={"query": query, "read_only": True},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if r.status_code >= 400:
            raise SystemExit(f"app_config: Management API answered HTTP {r.status_code}")
        rows: list[dict[str, Any]] = r.json()
        return rows

    present = sql("select to_regclass('public.app_config') is not null as present")
    if not present or not present[0].get("present"):
        return None
    return _rows_to_config([(str(row["key"]), row["value"]) for row in sql(APP_CONFIG_SQL)])


def load_app_config(mode: str) -> tuple[dict[str, Any] | None, str]:
    """(config or None, how it was read / why it was skipped)."""
    db = os.environ.get("DATABASE_URL") or ""
    sb, tok = os.environ.get("SUPABASE_URL") or "", os.environ.get("SUPABASE_ACCESS_TOKEN") or ""
    if mode == "off":
        return None, "skipped (--app-config off)"
    if mode in ("db", "auto") and db:
        return app_config_from_db(db), "read from DATABASE_URL"
    if mode in ("management", "auto") and sb and tok:
        return app_config_from_management(sb, tok), "read through the Supabase Management API (read-only)"
    if mode == "auto":
        return None, "skipped (no DATABASE_URL, and no SUPABASE_URL + SUPABASE_ACCESS_TOKEN)"
    raise SystemExit(f"--app-config {mode}: the required environment variables are not set")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=ROOT / "config" / "models.yaml")
    ap.add_argument("--app-config", choices=("auto", "db", "management", "off"), default="auto")
    args = ap.parse_args(argv)
    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    live, how = load_app_config(args.app_config)
    sources: list[tuple[str, dict[str, Any]]] = [(str(args.config), config)]
    missing = "" if live is not None or "skipped" in how else "; no `models` row yet (not migrated or not seeded)"
    print(f"app_config: {how}{missing}")
    if live is not None:
        sources.append(("app_config (live)", live))
    # Every model either source routes to, so each is fetched / probed once.
    role_chains: dict[str, list[dict[str, str]]] = {}
    for _, cfg in sources:
        for role, entries in chains(cfg)[0].items():
            role_chains[role] = [*role_chains.get(role, []), *entries]

    catalog: dict[str, CatalogModel] = {}
    if models_for(role_chains, "openrouter"):
        catalog = fetch_openrouter_catalog(os.environ.get("OPENROUTER_API_KEY") or None)
        print(f"OpenRouter catalog: {len(catalog)} models, {sum(is_free_id(i) for i in catalog)} free IDs")
    nv_key_env = ((config.get("providers") or {}).get("nvidia") or {}).get("key_env") or "NVIDIA_API_KEY"
    nv_key = os.environ.get(nv_key_env) or None
    nv_models = models_for(role_chains, "nvidia")
    nvidia = inspect_nvidia(nv_models, nv_key)
    if nv_models:
        how = "probed with a one-token call" if nv_key else f"{nv_key_env} not set: {NOT_PROBED_IN_CI}"
        print(f"NVIDIA: {len(nv_models)} models, {how}")
    print()

    failures: list[str] = []
    for name, cfg in sources:
        print(f"── {name}")
        report = check(cfg, catalog, nvidia)
        for w in report.warnings:
            print(f"WARN  {w}")
        failures.extend(f"{name}: {f}" for f in report.failures)
        print()
    if failures:
        print("FAIL")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS: every chain is free, and every primary is available with its required capability")
    return 0


if __name__ == "__main__":
    sys.exit(main())
