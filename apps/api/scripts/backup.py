"""Run a database backup to a LOCAL directory (segment 22): CI's dry run on every push, the weekly restore test, and a
manual backup before risky maintenance. The Space's own nightly job (naadi_api.agent.graphs.backup) is the one that
pushes to the private dataset.

    cd apps/api
    DATABASE_URL=postgresql://... uv run python scripts/backup.py --to-dir ../../.tmp/backups \\
        --recipient age1... [--date 2026-11-17] [--pg-bin /usr/lib/postgresql/17/bin]
    # --new-key KEYFILE: make a throwaway age key pair (CI); its public half is the recipient

Prints the files written (paths, sizes, sha256 of the encrypted files) and nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

from pyrage import x25519

from naadi_api.ops.backup import BackupConfig, BackupError, LocalStore, run_db_backup


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--to-dir", required=True, type=Path)
    key = ap.add_mutually_exclusive_group(required=True)
    key.add_argument("--recipient", help="age public key (age1...)")
    key.add_argument("--new-key", type=Path, help="write a new throwaway identity here and encrypt to it")
    ap.add_argument("--date", type=date.fromisoformat, default=None)
    ap.add_argument("--pg-bin", default="", help="directory holding pg_dump 17 (default: PATH)")
    args = ap.parse_args(argv)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("set DATABASE_URL")
    if args.new_key:
        ident = x25519.Identity.generate()
        args.new_key.write_text(f"{ident}\n")
        args.new_key.chmod(0o600)
        recipient = str(ident.to_public())
    else:
        recipient = args.recipient
    # A LocalStore needs no token; the field only has to be non-empty for BackupConfig.missing.
    cfg = BackupConfig(token="unused", recipient=recipient, repo_id="local", database_url=dsn)  # noqa: S106
    pg_dump = str(Path(args.pg_bin) / "pg_dump") if args.pg_bin else "pg_dump"
    try:
        with tempfile.TemporaryDirectory(prefix="naadi-backup-") as tmp:
            detail = run_db_backup(
                cfg,
                LocalStore(args.to_dir),
                today=args.date or datetime.now(UTC).date(),
                workdir=Path(tmp),
                pg_dump=pg_dump,
            )
    except BackupError as exc:
        print(json.dumps({"outcome": exc.code}))
        return 1
    print(json.dumps(detail, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
