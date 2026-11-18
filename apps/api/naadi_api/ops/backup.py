"""Backups (segment 22): nightly database dump and weekly Storage tarball, age-encrypted, to a private HF dataset.

Runs on the Space as Aegra system crons (naadi_api.agent.graphs.backup), never in GitHub Actions.

- Database (nightly 03:00 UTC): `pg_dump --format=custom --no-owner` of schema `public` (Naadi's tables and Aegra's,
  which live there too; `search_cache` is dumped without its rows, it is a 7-day cache) plus a data-only dump of
  `auth.users` and `auth.identities` (without them the restored profiles and every user-owned row would point at
  nobody). Aegra's checkpoint TTL (aegra.json: 43200 min = 30 days) keeps checkpoints older than 30 days out.
- Storage (weekly, Sunday 03:30 UTC): one tar.gz of every object in `user-uploads`, `vault` and `generated-media`, and
  of `content-public` / `content-private` except what the seed creates (`content-public/figures/`, restored by
  re-running the seed), with a manifest of names and sizes.
- Each file is encrypted with age to the owner's public key (`BACKUP_AGE_RECIPIENT`); the private key never leaves
  the owner. Files land in the dataset (`BACKUP_REPO_ID`, default `arrowassassin/naadi-backups`) as
  `db/YYYY-MM-DD.dump.age`, `db/YYYY-MM-DD.auth.dump.age` and `storage/YYYY-MM-DD.tar.gz.age`, in one commit that also
  deletes files past retention (database 30 days, Storage 35 days = five weekly copies).
- The job never creates a repository and refuses to upload unless the dataset exists AND is private.
- Until `HF_BACKUP_TOKEN` and `BACKUP_AGE_RECIPIENT` are both set it records `not_configured` and does nothing else.

The connection string's password reaches pg_dump through the environment (PGPASSWORD), never argv, and nothing
here logs a secret, a row or an object's content. The `detail` recorded in agent_health holds only outcomes,
paths, sizes and sha256 digests of the ENCRYPTED files.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, unquote, urlparse

import pyrage
from pyrage import x25519

from ..logs import get_logger
from ..scrub import scrub

log = get_logger("naadi.ops.backup")

DB_PREFIX = "db/"
STORAGE_PREFIX = "storage/"
DB_RETENTION_DAYS = 30
STORAGE_RETENTION_DAYS = 35
EXCLUDE_TABLE_DATA = ("public.search_cache",)
AUTH_TABLES = ("auth.users", "auth.identities")
# (bucket config key, object-name prefixes the seed owns there and restores itself)
STORAGE_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("user_uploads", ()),
    ("vault", ()),
    ("generated_media", ()),
    ("content_public", ("figures/",)),
    ("content_private", ()),
)
PG_DUMP_TIMEOUT_S = 1800
_DATED = re.compile(r"^(\d{4}-\d{2}-\d{2})\.")


class BackupError(Exception):
    """A run that cannot proceed; `code` is the outcome recorded (never a secret or a message from a server)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


# ── Configuration ──────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BackupConfig:
    token: str | None
    recipient: str | None
    repo_id: str
    database_url: str | None

    @property
    def missing(self) -> list[str]:
        out = []
        if not self.token:
            out.append("HF_BACKUP_TOKEN")
        if not self.recipient:
            out.append("BACKUP_AGE_RECIPIENT")
        if not self.database_url:
            out.append("DATABASE_URL")
        return out

    @classmethod
    def from_settings(cls, settings: Any) -> BackupConfig:
        tok = settings.hf_backup_token.get_secret_value() if settings.hf_backup_token else None
        dsn = settings.database_url.get_secret_value() if settings.database_url else None
        return cls(
            token=tok, recipient=settings.backup_age_recipient, repo_id=settings.backup_repo_id, database_url=dsn
        )


def parse_recipient(text: str) -> x25519.Recipient:
    try:
        return x25519.Recipient.from_str(text.strip())
    except Exception as exc:
        raise BackupError("invalid_recipient") from exc


# ── Encryption ─────────────────────────────────────────────────────────────────────────────────────────────────


def encrypt_file(src: Path, dest: Path, recipient: x25519.Recipient) -> None:
    with src.open("rb") as i, dest.open("wb") as o:
        pyrage.encrypt_io(i, o, [recipient])


def decrypt_file(src: Path, dest: Path, identity_text: str) -> None:
    idents = [
        x25519.Identity.from_str(line.strip())
        for line in identity_text.splitlines()
        if line.strip().startswith("AGE-SECRET-KEY-")
    ]
    if not idents:
        raise BackupError("no_identity")
    with src.open("rb") as i, dest.open("wb") as o:
        pyrage.decrypt_io(i, o, idents)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Where backups live ─────────────────────────────────────────────────────────────────────────────────────────


class BackupStore(Protocol):
    def verify(self) -> None: ...
    def list_paths(self, prefix: str) -> list[str]: ...
    def commit(self, adds: Mapping[str, Path], deletes: Sequence[str], message: str) -> None: ...
    def download(self, path: str, dest_dir: Path) -> Path: ...


@dataclass
class LocalStore:
    """A directory standing in for the dataset (CI dry runs, the weekly restore test, a manual restore)."""

    root: Path

    def verify(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def list_paths(self, prefix: str) -> list[str]:
        base = self.root / prefix
        if not base.is_dir():
            return []
        return sorted(f"{prefix}{p.name}" for p in base.iterdir() if p.is_file())

    def commit(self, adds: Mapping[str, Path], deletes: Sequence[str], message: str) -> None:
        for path, src in adds.items():
            dest = self.root / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        for path in deletes:
            (self.root / path).unlink(missing_ok=True)

    def download(self, path: str, dest_dir: Path) -> Path:
        src = self.root / path
        if not src.is_file():
            raise BackupError("not_found", path)
        dest = dest_dir / Path(path).name
        shutil.copyfile(src, dest)
        return dest


@dataclass
class HfDatasetStore:
    """The private HF dataset, through huggingface_hub. Never creates the repository."""

    repo_id: str
    token: str
    _api: Any = field(default=None, repr=False)

    def api(self) -> Any:
        if self._api is None:
            from huggingface_hub import HfApi

            self._api = HfApi(token=self.token)
        return self._api

    def verify(self) -> None:
        from huggingface_hub.errors import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError

        try:
            info = self.api().dataset_info(self.repo_id)
        except (RepositoryNotFoundError, GatedRepoError) as exc:
            raise BackupError("repo_missing") from exc
        except HfHubHTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise BackupError("hub_unauthorized" if status in (401, 403) else "hub_unreachable") from exc
        if not getattr(info, "private", False):
            raise BackupError("repo_not_private")

    def list_paths(self, prefix: str) -> list[str]:
        files = self.api().list_repo_files(self.repo_id, repo_type="dataset")
        return sorted(f for f in files if f.startswith(prefix))

    def commit(self, adds: Mapping[str, Path], deletes: Sequence[str], message: str) -> None:
        from huggingface_hub import CommitOperationAdd, CommitOperationDelete

        ops: list[Any] = [CommitOperationAdd(path_in_repo=p, path_or_fileobj=str(src)) for p, src in adds.items()]
        ops += [CommitOperationDelete(path_in_repo=p) for p in deletes]
        self.api().create_commit(self.repo_id, repo_type="dataset", operations=ops, commit_message=message)

    def download(self, path: str, dest_dir: Path) -> Path:
        from huggingface_hub import hf_hub_download

        return Path(hf_hub_download(self.repo_id, path, repo_type="dataset", token=self.token, local_dir=str(dest_dir)))


def retention_deletes(paths: Iterable[str], prefix: str, today: date, keep_days: int) -> list[str]:
    """Dated files under `prefix` older than `keep_days` (today's file is day 0)."""
    cutoff = today - timedelta(days=keep_days)
    out = []
    for p in paths:
        if not p.startswith(prefix):
            continue
        m = _DATED.match(p[len(prefix) :])
        if not m:
            continue
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if d < cutoff:
            out.append(p)
    return sorted(out)


# ── Database dump ──────────────────────────────────────────────────────────────────────────────────────────────


def pg_env(dsn: str) -> dict[str, str]:
    """libpq environment for `dsn`: the password travels as PGPASSWORD, never on a command line."""
    u = urlparse(dsn)
    if u.scheme not in ("postgres", "postgresql"):
        raise BackupError("bad_database_url")
    env = {
        "PGHOST": u.hostname or "localhost",
        "PGPORT": str(u.port or 5432),
        "PGUSER": unquote(u.username or "postgres"),
        "PGDATABASE": (u.path or "/postgres").lstrip("/") or "postgres",
    }
    if u.password:
        env["PGPASSWORD"] = unquote(u.password)
    q = parse_qs(u.query)
    if "sslmode" in q:
        env["PGSSLMODE"] = q["sslmode"][0]
    return env


def _run(cmd: Sequence[str], env: Mapping[str, str], secrets: Sequence[str], timeout: float, code: str) -> None:
    full_env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    full_env.update(env)
    try:
        r = subprocess.run(  # noqa: S603 - fixed argv, no shell
            list(cmd), env=full_env, capture_output=True, timeout=timeout, check=False
        )
    except FileNotFoundError as exc:
        raise BackupError(f"{code}_missing_binary") from exc
    except subprocess.TimeoutExpired as exc:
        raise BackupError(f"{code}_timeout") from exc
    if r.returncode != 0:
        msg = scrub(r.stderr.decode("utf-8", "replace")[-400:], list(secrets), max_len=300)
        log.warning(f"{code}_failed", returncode=r.returncode, stderr=msg)
        raise BackupError(f"{code}_failed", msg)


def dump_database(dsn: str, workdir: Path, *, pg_dump: str = "pg_dump") -> dict[str, Path]:
    """Two custom-format archives: `public` (schema + data) and the auth users (data only)."""
    env = pg_env(dsn)
    secrets = [env.get("PGPASSWORD", ""), dsn]
    main = workdir / "public.dump"
    auth = workdir / "auth.dump"
    base = [pg_dump, "--format=custom", "--no-owner", "--compress=6"]
    _run(
        [*base, "--schema=public", *(f"--exclude-table-data={t}" for t in EXCLUDE_TABLE_DATA), f"--file={main}"],
        env,
        secrets,
        PG_DUMP_TIMEOUT_S,
        "pg_dump",
    )
    _run([*base, "--data-only", *(f"--table={t}" for t in AUTH_TABLES), f"--file={auth}"], env, secrets, 600, "pg_dump")
    return {"dump": main, "auth.dump": auth}


# ── Storage tarball ────────────────────────────────────────────────────────────────────────────────────────────


class BucketReader(Protocol):
    def list(self, path: str | None = ..., options: Any = ...) -> list[dict[str, Any]]: ...
    def download(self, path: str) -> bytes: ...


def walk_bucket(bucket: BucketReader, prefix: str = "") -> Iterator[tuple[str, int]]:
    """(object name, size) for every object under `prefix`, folders followed (storage3 lists one level)."""
    offset = 0
    while True:
        page = bucket.list(
            prefix or None, {"limit": 1000, "offset": offset, "sortBy": {"column": "name", "order": "asc"}}
        )
        for item in page:
            name = f"{prefix}{item['name']}"
            if item.get("id") is None:  # a folder
                yield from walk_bucket(bucket, f"{name}/")
            else:
                yield name, int((item.get("metadata") or {}).get("size") or 0)
        if len(page) < 1000:
            return
        offset += len(page)


def build_storage_tarball(
    buckets: Sequence[tuple[str, BucketReader, Sequence[str]]], dest: Path, *, now: datetime | None = None
) -> dict[str, int]:
    """tar.gz of `bucket/<object>` for every object not under the bucket's skipped prefixes; returns counts."""
    counts: dict[str, int] = {}
    manifest: list[dict[str, Any]] = []
    with gzip.open(dest, "wb", compresslevel=6) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        for bucket_id, bucket, skip in buckets:
            n = 0
            for name, _size in walk_bucket(bucket):
                if any(name.startswith(s) for s in skip):
                    continue
                data = bucket.download(name)
                info = tarfile.TarInfo(f"{bucket_id}/{name}")
                info.size = len(data)
                info.mtime = int((now or datetime.now(UTC)).timestamp())
                tar.addfile(info, io.BytesIO(data))
                manifest.append({"bucket": bucket_id, "name": name, "bytes": len(data)})
                n += 1
            counts[bucket_id] = n
        body = json.dumps({"objects": manifest}, sort_keys=True).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    return counts


# ── Runs ───────────────────────────────────────────────────────────────────────────────────────────────────────


def _upload(
    store: BackupStore,
    plain: Mapping[str, Path],
    recipient: x25519.Recipient,
    prefix: str,
    today: date,
    keep_days: int,
    workdir: Path,
    message: str,
) -> dict[str, Any]:
    adds: dict[str, Path] = {}
    files = []
    for suffix, src in plain.items():
        enc = workdir / f"{today.isoformat()}.{suffix}.age"
        encrypt_file(src, enc, recipient)
        src.unlink()  # the plaintext never outlives the run
        path = f"{prefix}{enc.name}"
        adds[path] = enc
        files.append({"path": path, "bytes": enc.stat().st_size, "sha256": sha256_of(enc)})
    existing = store.list_paths(prefix)
    deletes = [p for p in retention_deletes(existing, prefix, today, keep_days) if p not in adds]
    store.commit(adds, deletes, message)
    return {"files": files, "deleted": deletes, "kept": len(set(existing) - set(deletes)) + len(adds)}


def run_db_backup(
    cfg: BackupConfig,
    store: BackupStore,
    *,
    today: date,
    workdir: Path,
    pg_dump: str = "pg_dump",
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """One nightly database backup. Returns the agent_health detail; raises BackupError with an outcome code."""
    if cfg.missing:
        raise BackupError("not_configured", ",".join(cfg.missing))
    recipient = parse_recipient(str(cfg.recipient))
    store.verify()
    assert cfg.database_url is not None
    dumps = dump_database(cfg.database_url, workdir, pg_dump=pg_dump)
    out = _upload(
        store, dumps, recipient, DB_PREFIX, today, DB_RETENTION_DAYS, workdir, f"db backup {today.isoformat()}"
    )
    return {"outcome": "ok", "kind": "db", "date": today.isoformat(), **out}


def run_storage_backup(
    cfg: BackupConfig,
    store: BackupStore,
    buckets: Sequence[tuple[str, BucketReader, Sequence[str]]],
    *,
    today: date,
    workdir: Path,
) -> dict[str, Any]:
    if not cfg.token or not cfg.recipient:
        raise BackupError("not_configured", ",".join(m for m in cfg.missing if m != "DATABASE_URL"))
    recipient = parse_recipient(cfg.recipient)
    store.verify()
    tarball = workdir / "storage.tar.gz"
    counts = build_storage_tarball(buckets, tarball)
    out = _upload(
        store,
        {"tar.gz": tarball},
        recipient,
        STORAGE_PREFIX,
        today,
        STORAGE_RETENTION_DAYS,
        workdir,
        f"storage backup {today.isoformat()}",
    )
    return {"outcome": "ok", "kind": "storage", "date": today.isoformat(), "objects": counts, **out}
