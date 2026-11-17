"""Restore a backup made by naadi_api.ops.backup (segment 22). CLI: `apps/api/scripts/restore.py`.

Steps (each one fails loudly; nothing is retried behind the operator's back):

1. fetch `db/<date>.auth.dump.age` and `db/<date>.dump.age` (from the private dataset, or a local directory);
2. decrypt them with the owner's age identity (a file only the owner holds);
3. check the archive's Naadi Alembic revision equals this code's head (restore with the release that made it);
4. build the schema from this code: `python -m naadi_api.agent.migrate_all` against the target (Aegra's and Naadi's
   migrations, RLS, Storage buckets and policies, closed default privileges: exactly what the Space runs);
5. empty every `public` table, then `pg_restore --data-only` the auth users (the target's `auth` schema is
   Supabase Auth's) and `public`, triggers off for the load (session_replication_role = replica),
   `--exit-on-error --single-transaction`;
6. optionally re-upload Storage objects from `storage/<date>.tar.gz.age` (the tarball nearest before the date);
7. verify: the Naadi Alembic revision equals this code's head, and every live_check schema/RLS check passes.

The target connection string is read from an environment variable (default TARGET_DATABASE_URL), never argv.
"""

from __future__ import annotations

import gzip
import io
import json
import re
import subprocess
import tarfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ..logs import get_logger
from .backup import DB_PREFIX, STORAGE_PREFIX, BackupError, BackupStore, decrypt_file, pg_env

log = get_logger("naadi.ops.restore")

# Every public table emptied before the data load (the target's migrations seed some rows, e.g. sources).
TRUNCATE_SQL = """
do $$
declare t text;
begin
  select string_agg(format('%I.%I', schemaname, tablename), ', ') into t from pg_tables where schemaname = 'public';
  if t is not null then
    execute 'truncate table ' || t || ' restart identity cascade';
  end if;
end
$$;
"""
# Triggers (updated_at stamps, status-transition guards, FK checks) are for live writes, not for reloading rows
# that were valid when dumped. Supabase lets the `postgres` role set this for its session.
REPLICA_SQL = "set session_replication_role = replica"
_COPY_REVISION = re.compile(r"^COPY public\.naadi_alembic_version .*?\n(\w+)\n", re.S | re.M)


@dataclass(frozen=True)
class RestorePlan:
    day: date
    auth_path: str
    db_path: str

    @classmethod
    def for_date(cls, d: date) -> RestorePlan:
        return cls(d, f"{DB_PREFIX}{d.isoformat()}.auth.dump.age", f"{DB_PREFIX}{d.isoformat()}.dump.age")


def _env_for(dsn: str) -> dict[str, str]:
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env.update(pg_env(dsn))
    return env


def _run(cmd: Sequence[str], env: Mapping[str, str], what: str, timeout: float = 3600) -> str:
    import os

    full = dict(env) if env else dict(os.environ)  # callers pass a complete environment (see _env_for)
    r = subprocess.run(list(cmd), env=full, capture_output=True, timeout=timeout, check=False)  # noqa: S603
    if r.returncode != 0:
        tail = r.stderr.decode("utf-8", "replace")[-800:]
        raise BackupError(f"{what}_failed", tail)
    return r.stdout.decode("utf-8", "replace")


def archive_revision(db_dump: Path, pg_restore: str) -> str | None:
    """The Naadi Alembic revision recorded in a database archive."""
    sql = _run(
        [pg_restore, "--data-only", "--table=naadi_alembic_version", "--file=-", str(db_dump)], {}, "pg_restore_toc"
    )
    m = _COPY_REVISION.search(sql)
    return m.group(1) if m else None


def restore_database(
    auth_dump: Path,
    db_dump: Path,
    target_dsn: str,
    *,
    head: str,
    migrate_cmd: Sequence[str],
    pg_restore: str = "pg_restore",
    psql: str = "psql",
    skip_auth: bool = False,
    workdir: Path,
) -> None:
    """Schema from this code (`migrate_cmd`: Aegra's and Naadi's migrations, RLS, buckets, policies), then the rows.

    The archive must come from the same schema revision as this code (`head`): restore with the release that made the
    backup, then deploy the current one, whose startup migrations upgrade the restored data like any database.
    """
    rev = archive_revision(db_dump, pg_restore)
    if rev != head:
        raise BackupError("revision_mismatch", f"archive {rev}, code {head}")
    env = _env_for(target_dsn)
    _run(list(migrate_cmd), {**env, "DATABASE_URL": target_dsn}, "migrate")
    _run([psql, "-v", "ON_ERROR_STOP=1", "-q", "-c", TRUNCATE_SQL], env, "truncate")
    for dump, what in ((auth_dump, "auth"), (db_dump, "public")):
        if what == "auth" and skip_auth:
            continue
        # pg_restore writes the archive's data as SQL; psql loads it in ONE transaction after `set
        # session_replication_role` (a startup option would be refused for a non-superuser).
        sql = workdir / f"{what}.sql"
        _run([pg_restore, "--data-only", "--no-owner", f"--file={sql}", str(dump)], env, f"pg_restore_{what}")
        _run(
            [psql, "-X", "-q", "-v", "ON_ERROR_STOP=1", "--single-transaction", "-c", REPLICA_SQL, "-f", str(sql)],
            env,
            f"load_{what}",
        )
        sql.unlink()


def fetch_and_decrypt(store: BackupStore, path: str, identity_text: str, workdir: Path) -> Path:
    enc = store.download(path, workdir)
    plain = workdir / enc.name.removesuffix(".age")
    decrypt_file(enc, plain, identity_text)
    enc.unlink()
    return plain


def nearest_storage_backup(store: BackupStore, on_or_before: date) -> str | None:
    best: tuple[str, str] | None = None
    for p in store.list_paths(STORAGE_PREFIX):
        stem = p[len(STORAGE_PREFIX) :][:10]
        if stem <= on_or_before.isoformat() and (best is None or stem > best[0]):
            best = (stem, p)
    return best[1] if best else None


def restore_storage(tarball: Path, upload: Callable[[str, str, bytes], None]) -> dict[str, int]:
    """Re-upload every object of a Storage tarball through `upload(bucket, name, data)`; returns counts."""
    counts: dict[str, int] = {}
    with gzip.open(tarball, "rb") as gz, tarfile.open(fileobj=io.BytesIO(gz.read()), mode="r") as tar:
        for m in tar.getmembers():
            if not m.isfile() or m.name == "manifest.json":
                continue
            # Never trust paths from an archive: only bucket/name, no absolute paths or parent references.
            if m.name.startswith("/") or ".." in m.name.split("/"):
                raise BackupError("unsafe_archive_path", m.name[:120])
            bucket, _, name = m.name.partition("/")
            f = tar.extractfile(m)
            if f is None or not name:
                continue
            upload(bucket, name, f.read())
            counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def verify_restored(target_dsn: str, checks: Sequence[Any]) -> dict[str, tuple[bool, str]]:
    """Run `checks` (objects with `name`, `sql`, `evaluate(rows)`: tests/db/live_check.py's schema, RLS and seed
    checks) with psycopg against the restored database."""
    import psycopg
    from psycopg.rows import dict_row

    out: dict[str, tuple[bool, str]] = {}
    with psycopg.connect(target_dsn, autocommit=True) as conn:
        for c in checks:
            try:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(c.sql)
                    out[c.name] = c.evaluate(cur.fetchall())
            except psycopg.Error as exc:
                out[c.name] = (False, type(exc).__name__)
    return out


COUNT_TABLES_SQL = (
    "select format('%I.%I', schemaname, tablename) from pg_tables where schemaname = 'public'"
    " union all select 'auth.users' union all select 'auth.identities' order by 1"
)


def row_counts(dsn: str) -> dict[str, int]:
    """Exact row counts of every public table and the auth users (search_cache is backed up without rows)."""
    import psycopg
    from psycopg import sql

    out: dict[str, int] = {}
    with psycopg.connect(dsn, autocommit=True) as conn:
        names = [r[0] for r in conn.execute(COUNT_TABLES_SQL).fetchall()]
        for name in names:
            schema, table = name.split(".", 1)
            q = sql.SQL("select count(*) from {}.{}").format(sql.Identifier(schema), sql.Identifier(table.strip('"')))
            row = conn.execute(q).fetchone()
            out[name] = int(row[0]) if row else 0
    out.pop("public.search_cache", None)
    return out


def compare_counts(source: Mapping[str, int], target: Mapping[str, int]) -> dict[str, tuple[bool, str]]:
    out: dict[str, tuple[bool, str]] = {}
    for name in sorted(set(source) | set(target)):
        a, b = source.get(name), target.get(name)
        out[f"rows: {name}"] = (a == b, f"source {a}, restored {b}")
    return out


def summary(results: Mapping[str, tuple[bool, str]]) -> dict[str, Any]:
    failed = sorted(k for k, (ok, _) in results.items() if not ok)
    return {"checks": len(results), "failed": failed, "ok": not failed}


def dumps(results: Mapping[str, tuple[bool, str]]) -> str:
    return json.dumps(summary(results), indent=2)
