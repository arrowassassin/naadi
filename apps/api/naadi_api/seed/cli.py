"""Command line for the seed import (apps/api/scripts/seed.py and `python -m naadi_api.seed`).

    uv run python scripts/seed.py --data ../../data --config ../../config [--dry-run]
        [--only sources,taxonomy,figures,chapters,questions,pathway,plan,app_config]
        [--force] [--report out.json] [--storage-url http://localhost:54335]

Database: DATABASE_URL. Storage: SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY (a Supabase project) or
`--storage-url` / NAADI_STORAGE_URL (a bare storage-api, local stack and CI) with the service key in
SUPABASE_SERVICE_ROLE_KEY. Secrets are read through naadi_api.settings and never printed.
Exit codes: 0 ok, 1 verification failed, 2 bad input or configuration, 3 another import is running.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ..settings import Settings, get_settings
from .data import SeedDataError, default_data_dir
from .importer import GROUPS, SeedFailed, SeedLocked, SeedOptions, run_seed
from .report import SeedReport
from .storage import StorageTarget


def storage_target(settings: Settings, storage_url: str | None) -> StorageTarget | None:
    key = settings.supabase_service_role_key
    if key is None:
        return None
    if storage_url:
        return StorageTarget(key=key.get_secret_value(), storage_url=storage_url)
    if settings.supabase_url:
        return StorageTarget(key=key.get_secret_value(), supabase_url=settings.supabase_url)
    return None


def _write_report(path: str | None, report: SeedReport) -> None:
    if path:
        Path(path).write_text(json.dumps(report.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    p = argparse.ArgumentParser(prog="seed.py", description="Import Naadi's seed content (idempotent).")
    p.add_argument("--data", type=Path, default=default_data_dir(), help="data/ directory")
    p.add_argument("--config", type=Path, default=settings.config_dir, help="config/ directory")
    p.add_argument("--dry-run", action="store_true", help="write nothing; print what a run would change")
    p.add_argument("--only", default=",".join(GROUPS), help=f"comma list of groups ({', '.join(GROUPS)})")
    p.add_argument("--force", action="store_true", help="also overwrite admin-edited sections and app_config keys")
    p.add_argument("--report", help="write the JSON report to this file")
    p.add_argument(
        "--storage-url",
        default=os.environ.get("NAADI_STORAGE_URL") or None,
        help="a bare storage-api URL (local stack / CI) instead of SUPABASE_URL",
    )
    args = p.parse_args(argv)
    only = tuple(g.strip() for g in args.only.split(",") if g.strip())
    if settings.database_url is None:
        print("seed: DATABASE_URL is not set", file=sys.stderr)
        return 2
    storage = storage_target(settings, args.storage_url)
    if "figures" in only and storage is None:
        print("seed: figures need SUPABASE_SERVICE_ROLE_KEY and SUPABASE_URL (or --storage-url)", file=sys.stderr)
        return 2
    opts = SeedOptions(
        database_url=settings.database_url.get_secret_value(),
        data_dir=args.data.resolve(),
        config_dir=args.config.resolve(),
        storage=storage,
        dry_run=args.dry_run,
        only=only,
        force=args.force,
    )
    try:
        report = run_seed(opts)
    except SeedDataError as exc:
        print("seed: the seed bundle is invalid:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        return 2
    except SeedLocked as exc:
        print(f"seed: {exc}", file=sys.stderr)
        return 3
    except SeedFailed as exc:
        print(exc.report.summary())
        _write_report(args.report, exc.report)
        print(f"seed: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"seed: {exc}", file=sys.stderr)
        return 2
    print(report.summary())
    _write_report(args.report, report)
    return 0
