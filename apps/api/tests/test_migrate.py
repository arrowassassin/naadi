"""Unit tests for the migration runner (no database; the `db` suite covers real migrations)."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory

from naadi_api import migrate

from .conftest import REPO_ROOT, LogCapture, make_settings


def test_schema_doc_lists_the_pathway_stage_keys_from_the_data() -> None:
    stages = json.loads((REPO_ROOT / "data/pathway/pathway_stages.json").read_text(encoding="utf-8"))
    keys = [s["key"] for s in stages]
    doc = (REPO_ROOT / "db/schema.sql").read_text(encoding="utf-8")
    comment = doc.split("-- pathway_stages.key:", 1)[1].split("create table", 1)[0]
    listed = re.findall(r"[a-z0-9_]+", comment.split("(", 1)[1].split(")", 1)[0].replace("--", " "))
    assert listed == keys
    assert len(keys) == 16


def test_sqlalchemy_url_uses_psycopg3() -> None:
    url = migrate.sqlalchemy_url(
        "postgresql://postgres.ref:p%40ss@aws-0-x.pooler.supabase.com:5432/postgres?sslmode=require"
    )
    assert url.startswith("postgresql+psycopg://postgres.ref:")
    assert url.endswith("@aws-0-x.pooler.supabase.com:5432/postgres?sslmode=require")
    assert migrate.sqlalchemy_url("postgres://u:p@h/db").startswith("postgresql+psycopg://")


def test_sqlalchemy_url_rejects_other_backends() -> None:
    with pytest.raises(ValueError, match="postgresql"):
        migrate.sqlalchemy_url("mysql://u:p@h/db")


def test_revisions_form_one_linear_chain_with_real_downgrades() -> None:
    script = ScriptDirectory.from_config(migrate.alembic_config())
    revs = list(script.walk_revisions())
    assert [r.revision for r in reversed(revs)] == ["0001", "0002"]
    assert len(script.get_heads()) == 1
    for r in revs:
        assert r.path is not None
        tree = ast.parse(Path(r.path).read_text(encoding="utf-8"))
        funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
        assert {"upgrade", "downgrade"} <= set(funcs), r.revision
        body = ast.dump(funcs["downgrade"])
        assert "NotImplementedError" not in body and len(funcs["downgrade"].body) > 1, r.revision


def test_alembic_ini_holds_no_database_url() -> None:
    text = (migrate.MIGRATIONS_DIR / "alembic.ini").read_text(encoding="utf-8")
    assert "sqlalchemy.url" not in text
    assert "postgres" not in text.split("[loggers]")[0].replace("postgresql://", "")


def test_run_without_database_url_fails_clearly(logs: LogCapture) -> None:
    assert migrate.run(make_settings(database_url=None)) == 2
    line = next(e for e in logs.lines() if e["event"] == "migrate.failed")
    assert line["error"] == "DatabaseNotConfigured"


def test_run_if_configured_skips_without_database_url(logs: LogCapture) -> None:
    assert migrate.run(make_settings(database_url=None), if_configured=True) == 0
    assert any(e["event"] == "migrate.skipped" for e in logs.lines())


def test_run_failure_is_one_scrubbed_line(logs: LogCapture) -> None:
    # Nothing listens on port 9 locally: connection refused, quickly.
    url = "postgresql://postgres.someref:Sup3r-Secret-Pw@127.0.0.1:9/postgres"
    assert migrate.run(make_settings(database_url=url)) == 1
    failed = [e for e in logs.lines() if e["event"] == "migrate.failed"]
    assert len(failed) == 1
    assert failed[0]["error"] == "OperationalError"
    assert "Sup3r-Secret-Pw" not in logs.text
    assert "hint" in failed[0]
