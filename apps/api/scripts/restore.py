"""Restore a Naadi backup into a target database (segment 22; steps in docs/RUNBOOK.md "Restore").

    cd apps/api
    TARGET_DATABASE_URL=postgresql://... uv run python scripts/restore.py --date 2026-11-17 \\
        --identity ~/naadi-backup.agekey --repo arrowassassin/naadi-backups      # token: HF_BACKUP_TOKEN or HF_TOKEN
    # or --from-dir DIR (a local copy of the dataset, or a CI dry run); --storage re-uploads Storage objects

The target URL is read from the environment (TARGET_DATABASE_URL, or --target-env NAME), never from argv, and only
its host and database name are printed. The target must be EMPTY (no Naadi tables): restoring over a live database
is refused unless --allow-nonempty is given. After the restore, the Naadi Alembic revision must be this code's head
and every live_check schema/RLS check (plus the seed counts with --seed-checks) must pass; exit 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from naadi_api import migrate
from naadi_api.ops.backup import BackupError, BackupStore, HfDatasetStore, LocalStore
from naadi_api.ops.restore import (
    RestorePlan,
    compare_counts,
    dumps,
    fetch_and_decrypt,
    nearest_storage_backup,
    restore_database,
    restore_storage,
    row_counts,
    summary,
    verify_restored,
)


def _store(args: argparse.Namespace) -> BackupStore:
    if args.from_dir:
        return LocalStore(Path(args.from_dir))
    token = os.environ.get("HF_BACKUP_TOKEN") or os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit("set HF_BACKUP_TOKEN (or HF_TOKEN) to read the private backup dataset, or use --from-dir")
    store = HfDatasetStore(args.repo, token)
    store.verify()  # exists and is private
    return store


def _target_is_empty(dsn: str) -> bool:
    import psycopg

    with psycopg.connect(dsn) as conn:
        row = conn.execute("select to_regclass('public.naadi_alembic_version') is null").fetchone()
    return bool(row and row[0])


def _uploader(args: argparse.Namespace):  # type: ignore[no-untyped-def]
    from naadi_api.seed.storage import StorageTarget

    key = os.environ.get(args.storage_key_env)
    if not key:
        raise SystemExit(f"--storage needs the target's service key in ${args.storage_key_env}")
    target = (
        StorageTarget(key=key, storage_url=args.storage_url)
        if args.storage_url
        else StorageTarget(key=key, supabase_url=os.environ.get("TARGET_SUPABASE_URL"))
    )
    client = target.client()

    def upload(bucket: str, name: str, data: bytes) -> None:
        client.from_(bucket).upload(name, data, {"upsert": "true"})

    return upload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--date", required=True, type=date.fromisoformat, help="backup date, YYYY-MM-DD (UTC)")
    ap.add_argument("--identity", required=True, type=Path, help="the owner's age identity file (AGE-SECRET-KEY-...)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--repo", help="the private HF dataset, e.g. arrowassassin/naadi-backups")
    src.add_argument("--from-dir", help="a local directory laid out like the dataset (db/, storage/)")
    ap.add_argument("--target-env", default="TARGET_DATABASE_URL", help="env var holding the target URL")
    ap.add_argument("--skip-auth", action="store_true", help="do not restore auth.users / auth.identities")
    ap.add_argument("--allow-nonempty", action="store_true", help="restore even if the target has Naadi tables")
    ap.add_argument("--storage", action="store_true", help="also re-upload Storage objects from the nearest tarball")
    ap.add_argument("--storage-url", help="a bare storage-api URL (local stack/CI) instead of TARGET_SUPABASE_URL")
    ap.add_argument("--storage-key-env", default="TARGET_SERVICE_ROLE_KEY")
    ap.add_argument("--seed-checks", action="store_true", help="also check the seed row counts from data/ + config/")
    ap.add_argument("--compare-env", help="env var holding the SOURCE URL: every table's row count must match")
    ap.add_argument("--pg-bin", default="", help="directory holding pg_restore/psql 17 (default: PATH)")
    args = ap.parse_args(argv)

    dsn = os.environ.get(args.target_env)
    if not dsn:
        raise SystemExit(f"set ${args.target_env} to the target database URL")
    u = urlparse(dsn)
    print(f"target: {u.hostname}:{u.port or 5432}/{(u.path or '/').lstrip('/')}")
    if not args.allow_nonempty and not _target_is_empty(dsn):
        print("refused: the target already has Naadi tables (use a fresh project, or --allow-nonempty)")
        return 2
    identity = args.identity.expanduser().read_text()
    store = _store(args)
    plan = RestorePlan.for_date(args.date)
    pg = (lambda b: str(Path(args.pg_bin) / b)) if args.pg_bin else (lambda b: b)
    try:
        with tempfile.TemporaryDirectory(prefix="naadi-restore-") as tmp:
            work = Path(tmp)
            auth = fetch_and_decrypt(store, plan.auth_path, identity, work)
            db = fetch_and_decrypt(store, plan.db_path, identity, work)
            restore_database(
                auth,
                db,
                dsn,
                head=migrate.head_revision(),
                migrate_cmd=[sys.executable, "-m", "naadi_api.agent.migrate_all"],
                pg_restore=pg("pg_restore"),
                psql=pg("psql"),
                skip_auth=args.skip_auth,
                workdir=work,
            )
            print(f"database restored from {plan.db_path}")
            if args.storage:
                path = nearest_storage_backup(store, args.date)
                if path is None:
                    print("no Storage backup on or before that date")
                    return 1
                tarball = fetch_and_decrypt(store, path, identity, work)
                counts = restore_storage(tarball, _uploader(args))
                print(f"storage restored from {path}: {counts}")
    except BackupError as exc:
        # The operator's own terminal: the tool's last lines help, and the URL's password is never in them.
        print(f"restore failed: {exc.code}\n{exc.detail[-600:]}")
        return 1

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tests.db import live_check

    checks = live_check.checks(migrate.head_revision())
    if args.seed_checks:
        expected, keys = live_check.bundle_expectations()
        checks += live_check.seed_checks(expected, keys)
    results = verify_restored(dsn, checks)
    if args.compare_env:
        source = os.environ.get(args.compare_env)
        if not source:
            raise SystemExit(f"set ${args.compare_env} to the source database URL")
        results.update(compare_counts(row_counts(source), row_counts(dsn)))
    for name, (ok, detail) in results.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail[:80]}")
    print(dumps(results))
    return 0 if summary(results)["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
