"""Segment 22 ops against a real database: prune, the backup health component, and a backup dry run.

The dry run (encrypt to a throwaway key, write to a local "dataset", decrypt, list the archive, delete) needs a
pg_dump that matches the server's major version (17): NAADI_PG_BIN, or /usr/lib/postgresql/17/bin. CI installs it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from pydantic import SecretStr
from pyrage import x25519

from naadi_api.db import Database
from naadi_api.health.checks import NotConfigured, backup_check
from naadi_api.ops.backup import BackupConfig, LocalStore, decrypt_file, run_db_backup
from naadi_api.ops.prune import prune

from ..conftest import make_settings

pytestmark = pytest.mark.db


def _pg_bin() -> Path | None:
    for d in (os.environ.get("NAADI_PG_BIN"), "/usr/lib/postgresql/17/bin"):
        if d and (Path(d) / "pg_dump").is_file():
            return Path(d)
    found = shutil.which("pg_dump")
    if found:
        out = subprocess.run(  # noqa: S603 - the PATH's own pg_dump
            [found, "--version"], capture_output=True, text=True, check=False
        ).stdout
        if " 17." in out or " 18." in out:
            return Path(found).parent
    return None


async def test_prune_deletes_only_rows_past_retention(migrated_url: str) -> None:
    async with await psycopg.AsyncConnection.connect(migrated_url) as conn, conn.transaction(force_rollback=True):
        await conn.execute(
            "insert into public.llm_usage (provider, created_at) values"
            " ('openrouter', now() - interval '91 days'), ('openrouter', now() - interval '89 days')"
        )
        await conn.execute(
            "insert into public.search_cache (query_norm, results, created_at) values"
            " ('s22-old', '[]', now() - interval '40 days'), ('s22-new', '[]', now() - interval '1 day')"
        )
        await conn.execute(
            "insert into public.agent_health (kind, trigger, ok, created_at) values"
            " ('prune', 'cron', true, now() - interval '100 days'), ('prune', 'cron', true, now())"
        )
        before = await (await conn.execute("select count(*) from public.llm_usage")).fetchone()
        deleted = await prune(conn, cache_hours=720)
        assert deleted["llm_usage"] >= 1 and deleted["search_cache"] >= 1 and deleted["agent_health"] >= 1
        left = await (
            await conn.execute(
                "select (select count(*) from public.search_cache where query_norm like 's22-%'),"
                " (select count(*) from public.llm_usage where created_at < now() - interval '90 days'),"
                " (select count(*) from public.agent_health where kind = 'prune')"
            )
        ).fetchone()
        assert left == (1, 0, 1)
        assert before is not None
        # Tables of segments that are not merged yet are reported, not failed on.
        assert set(deleted) >= {"sync_ops", "sync_tombstones"}


def test_backup_kinds_are_admitted(pg: psycopg.Connection) -> None:
    pg.execute(
        "insert into public.agent_health (kind, trigger, ok) values ('backup', 'cron', true), ('prune', 'cron', true)"
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute("insert into public.agent_health (kind, trigger, ok) values ('nope', 'cron', true)")


async def test_backup_health_states(migrated_url: str) -> None:
    unset = backup_check(make_settings(database_url=migrated_url), Database(SecretStr(migrated_url)))
    with pytest.raises(NotConfigured) as e:
        await unset()
    assert e.value.status == "degraded" and e.value.backup is not None
    assert e.value.backup.reason == "not_configured"
    assert e.value.backup.missing == ["HF_BACKUP_TOKEN", "BACKUP_AGE_RECIPIENT"]

    settings = make_settings(database_url=migrated_url, hf_backup_token="t", backup_age_recipient="age1x")
    db = Database(SecretStr(migrated_url))
    await db.open()
    try:
        with psycopg.connect(migrated_url, autocommit=True) as c:
            c.execute("delete from public.agent_health where kind = 'backup'")
        now = datetime.now(UTC)
        out = await backup_check(settings, db, clock=lambda: now)()
        assert out.status == "degraded" and out.backup is not None and out.backup.reason == "never_ran"

        def add(ok: bool, kind: str, at: datetime, outcome: str = "ok") -> None:
            with psycopg.connect(migrated_url, autocommit=True) as c:
                c.execute(
                    "insert into public.agent_health (kind, trigger, ok, detail, created_at)"
                    " values ('backup', 'cron', %s, %s::jsonb, %s)",
                    (ok, json.dumps({"kind": kind, "outcome": outcome}), at),
                )

        add(True, "db", now - timedelta(hours=2))
        out = await backup_check(settings, db, clock=lambda: now)()
        assert out.status == "ok" and out.backup is not None and out.backup.reason == "ok"
        add(False, "db", now - timedelta(minutes=5), "pg_dump_failed")
        out = await backup_check(settings, db, clock=lambda: now)()
        assert out.backup is not None and (out.backup.reason, out.backup.last_outcome) == ("failed", "pg_dump_failed")
        out = await backup_check(settings, db, clock=lambda: now + timedelta(days=2))()
        assert out.status == "degraded"
    finally:
        with psycopg.connect(migrated_url, autocommit=True) as c:
            c.execute("delete from public.agent_health where kind = 'backup'")
        await db.close()


def test_backup_dry_run_round_trip(migrated_url: str, tmp_path: Path) -> None:
    pg_bin = _pg_bin()
    if pg_bin is None:
        pytest.skip("needs pg_dump 17 (NAADI_PG_BIN or /usr/lib/postgresql/17/bin)")
    ident = x25519.Identity.generate()
    cfg = BackupConfig(token="unused", recipient=str(ident.to_public()), repo_id="local", database_url=migrated_url)
    store = LocalStore(tmp_path / "test-path")
    work = tmp_path / "work"
    work.mkdir()
    today = date.today()
    detail = run_db_backup(cfg, store, today=today, workdir=work, pg_dump=str(pg_bin / "pg_dump"))
    assert detail["outcome"] == "ok" and len(detail["files"]) == 2
    assert not list(work.glob("*.dump")), "plaintext dumps must not outlive the run"
    plain = tmp_path / "public.dump"
    decrypt_file(tmp_path / "test-path" / f"db/{today.isoformat()}.dump.age", plain, str(ident))
    toc = subprocess.run(  # noqa: S603 - fixed argv
        [str(pg_bin / "pg_restore"), "--list", str(plain)], capture_output=True, text=True, check=True
    ).stdout
    for table in ("naadi_alembic_version", "profiles", "chapters", "agent_health", "assistant", "thread"):
        assert f"TABLE public {table} " in toc, table
    assert "TABLE DATA public search_cache" not in toc  # cache rows are excluded
    assert "TABLE DATA public chapters" in toc
    # ... then delete the test path, as the CI dry run does.
    shutil.rmtree(tmp_path / "test-path")
