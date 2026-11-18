"""Backups, restore and prune (segment 22): everything that runs without a database."""

from __future__ import annotations

import gzip
import io
import tarfile
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pyrage import x25519

from naadi_api.ops.backup import (
    BackupConfig,
    BackupError,
    HfDatasetStore,
    LocalStore,
    build_storage_tarball,
    decrypt_file,
    encrypt_file,
    parse_recipient,
    pg_env,
    retention_deletes,
    run_db_backup,
    run_storage_backup,
)
from naadi_api.ops.prune import rules, search_cache_hours
from naadi_api.ops.restore import RestorePlan, compare_counts, nearest_storage_backup, restore_storage

KEY = x25519.Identity.generate()


def cfg(**over: Any) -> BackupConfig:
    base = {"token": "t", "recipient": str(KEY.to_public()), "repo_id": "o/r", "database_url": "postgresql://u:pw@h/db"}
    base.update(over)
    return BackupConfig(**base)


def test_not_configured_names_the_missing_settings_only() -> None:
    assert cfg(token=None, recipient=None).missing == ["HF_BACKUP_TOKEN", "BACKUP_AGE_RECIPIENT"]
    with pytest.raises(BackupError) as e:
        run_db_backup(cfg(token=None), LocalStore(Path("/nonexistent")), today=date(2026, 11, 17), workdir=Path("."))
    assert e.value.code == "not_configured" and e.value.detail == "HF_BACKUP_TOKEN"


def test_invalid_recipient_is_an_outcome_not_a_crash() -> None:
    with pytest.raises(BackupError) as e:
        parse_recipient("age1-not-a-key")
    assert e.value.code == "invalid_recipient"


def test_encryption_round_trips_and_needs_the_identity(tmp_path: Path) -> None:
    src = tmp_path / "plain"
    src.write_bytes(b"secret rows" * 1000)
    enc = tmp_path / "x.age"
    encrypt_file(src, enc, parse_recipient(str(KEY.to_public())))
    assert b"secret rows" not in enc.read_bytes()
    out = tmp_path / "out"
    decrypt_file(enc, out, f"# comment\n{KEY}\n")
    assert out.read_bytes() == src.read_bytes()
    import pyrage

    with pytest.raises(pyrage.DecryptError):
        decrypt_file(enc, tmp_path / "no", f"{x25519.Identity.generate()}\n")
    with pytest.raises(BackupError):
        decrypt_file(enc, tmp_path / "no", "not a key")


def test_password_goes_to_the_environment_never_argv() -> None:
    env = pg_env("postgresql://user%40x:p%3Ass@db.example.com:6543/postgres?sslmode=require")
    assert env == {
        "PGHOST": "db.example.com",
        "PGPORT": "6543",
        "PGUSER": "user@x",
        "PGDATABASE": "postgres",
        "PGPASSWORD": "p:ss",
        "PGSSLMODE": "require",
    }
    with pytest.raises(BackupError):
        pg_env("mysql://x")


def test_retention_keeps_thirty_days_and_ignores_other_files() -> None:
    paths = ["db/2026-10-17.dump.age", "db/2026-10-18.auth.dump.age", "db/2026-11-17.dump.age", "db/README.md"]
    assert retention_deletes(paths, "db/", date(2026, 11, 17), 30) == ["db/2026-10-17.dump.age"]
    assert retention_deletes(["storage/2026-10-01.tar.gz.age"], "db/", date(2026, 11, 17), 30) == []


def test_a_db_backup_encrypts_uploads_and_prunes_in_one_commit(tmp_path: Path) -> None:
    calls: list[tuple[dict[str, Path], list[str]]] = []

    class Store(LocalStore):
        def commit(self, adds: Any, deletes: Any, message: str) -> None:
            calls.append((dict(adds), list(deletes)))
            super().commit(adds, deletes, message)

    store = Store(tmp_path / "ds")
    (tmp_path / "ds" / "db").mkdir(parents=True)
    (tmp_path / "ds" / "db" / "2026-09-01.dump.age").write_bytes(b"old")
    fake = tmp_path / "pg_dump"
    # A stand-in pg_dump: writes its --file argument and checks the password never reached argv.
    script = [
        "#!/bin/sh",
        'for a in "$@"; do case "$a" in',
        "  *pw*) exit 9;;",
        '  --file=*) printf DUMP > "${a#--file=}";;',
        "esac; done",
        '[ "$PGPASSWORD" = "pw" ] || exit 8',
    ]
    fake.write_text("\n".join(script) + "\n")
    fake.chmod(0o755)
    work = tmp_path / "w"
    work.mkdir()
    detail = run_db_backup(cfg(), store, today=date(2026, 11, 17), workdir=work, pg_dump=str(fake))
    assert detail["outcome"] == "ok" and detail["deleted"] == ["db/2026-09-01.dump.age"]
    assert sorted(f["path"] for f in detail["files"]) == ["db/2026-11-17.auth.dump.age", "db/2026-11-17.dump.age"]
    assert len(calls) == 1  # adds and the retention delete land together
    assert not list(work.glob("*.dump"))  # no plaintext left behind
    out = tmp_path / "plain"
    decrypt_file(tmp_path / "ds" / "db" / "2026-11-17.dump.age", out, str(KEY))
    assert out.read_bytes() == b"DUMP"


class FakeBucket:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def list(self, path: str | None = None, options: Any = None) -> list[dict[str, Any]]:
        prefix = path or ""
        seen: dict[str, dict[str, Any]] = {}
        for name, data in self.objects.items():
            if not name.startswith(prefix):
                continue
            rest = name[len(prefix) :]
            head, sep, _ = rest.partition("/")
            seen[head] = (
                {"name": head, "id": None} if sep else {"name": head, "id": "x", "metadata": {"size": len(data)}}
            )
        items = sorted(seen.values(), key=lambda i: i["name"])
        off = (options or {}).get("offset", 0)
        return items[off : off + 1000]

    def download(self, path: str) -> bytes:
        return self.objects[path]


def test_storage_tarball_skips_seed_figures_and_restores_safely(tmp_path: Path) -> None:
    buckets = [
        ("user-uploads", FakeBucket({"u1/a.pdf": b"A", "u1/deep/b.png": b"B"}), ()),
        ("content-public", FakeBucket({"figures/ch01.svg": b"<svg/>", "admin/logo.png": b"L"}), ("figures/",)),
    ]
    tarball = tmp_path / "s.tar.gz"
    counts = build_storage_tarball(buckets, tarball)
    assert counts == {"user-uploads": 2, "content-public": 1}
    got: list[tuple[str, str, bytes]] = []
    assert restore_storage(tarball, lambda b, n, d: got.append((b, n, d))) == {"user-uploads": 2, "content-public": 1}
    assert sorted(got) == [
        ("content-public", "admin/logo.png", b"L"),
        ("user-uploads", "u1/a.pdf", b"A"),
        ("user-uploads", "u1/deep/b.png", b"B"),
    ]
    # An archive with a path escape is refused, not extracted.
    evil = tmp_path / "evil.tar.gz"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        info = tarfile.TarInfo("user-uploads/../../etc/x")
        info.size = 1
        t.addfile(info, io.BytesIO(b"x"))
    evil.write_bytes(gzip.compress(buf.getvalue()))
    with pytest.raises(BackupError):
        restore_storage(evil, lambda *a: None)


def test_storage_backup_runs_without_a_database_url(tmp_path: Path) -> None:
    work = tmp_path / "w"
    work.mkdir()
    d = run_storage_backup(
        cfg(database_url=None),
        LocalStore(tmp_path / "ds"),
        [("vault", FakeBucket({"x/doc.pdf": b"D"}), ())],
        today=date(2026, 11, 15),
        workdir=work,
    )
    assert d["objects"] == {"vault": 1} and d["files"][0]["path"] == "storage/2026-11-15.tar.gz.age"
    assert nearest_storage_backup(LocalStore(tmp_path / "ds"), date(2026, 11, 17)) == "storage/2026-11-15.tar.gz.age"
    assert nearest_storage_backup(LocalStore(tmp_path / "ds"), date(2026, 11, 14)) is None


def test_hf_store_never_creates_and_refuses_a_public_dataset() -> None:
    class Info:
        private = False

    class Api:
        def dataset_info(self, repo_id: str) -> Info:
            return Info()

        def create_repo(self, *a: Any, **k: Any) -> None:  # pragma: no cover - must never be called
            raise AssertionError("created a repository")

    store = HfDatasetStore("o/r", "t", _api=Api())
    with pytest.raises(BackupError) as e:
        store.verify()
    assert e.value.code == "repo_not_private"

    from huggingface_hub.errors import RepositoryNotFoundError

    class Missing(Api):
        def dataset_info(self, repo_id: str) -> Info:
            import httpx

            req = httpx.Request("GET", "https://huggingface.co/api/datasets/o/r")
            raise RepositoryNotFoundError("nope", response=httpx.Response(404, request=req))

    with pytest.raises(BackupError) as e:
        HfDatasetStore("o/r", "t", _api=Missing()).verify()
    assert e.value.code == "repo_missing"


def test_restore_plan_and_count_comparison() -> None:
    p = RestorePlan.for_date(date(2026, 11, 17))
    assert (p.auth_path, p.db_path) == ("db/2026-11-17.auth.dump.age", "db/2026-11-17.dump.age")
    r = compare_counts({"public.a": 1, "auth.users": 2}, {"public.a": 1, "auth.users": 1})
    assert r == {"rows: auth.users": (False, "source 2, restored 1"), "rows: public.a": (True, "source 1, restored 1")}


def test_prune_rules_cover_the_retention_table() -> None:
    from naadi_api.config import load_models_config

    from ..conftest import REPO_ROOT

    hours = search_cache_hours(load_models_config(REPO_ROOT / "config"))
    assert hours >= 7 * 24
    by = {r.table: r.where for r in rules(hours)}
    assert set(by) == {"llm_usage", "search_cache", "sync_ops", "sync_tombstones", "agent_health", "search_usage"}
    assert "90 days" in by["llm_usage"] and "24 hours" in by["sync_ops"] and f"{hours} hours" in by["search_cache"]
    with pytest.raises(ValueError, match="positive"):
        rules(0)
