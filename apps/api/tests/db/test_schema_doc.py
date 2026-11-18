"""db/schema.sql documents the contract; this keeps it equal to what the migrations actually create."""

from __future__ import annotations

import re

import psycopg

from naadi_api.agent.migrate_all import AEGRA_TABLES

from ..conftest import REPO_ROOT
from .rls import DB_MARKS

pytestmark = DB_MARKS

_TABLE = re.compile(r"^create table public\.(\w+) \($(.*?)^\);", re.MULTILINE | re.DOTALL)
_NOT_COLUMNS = {"primary", "unique", "foreign", "check", "constraint"}


def documented_tables() -> dict[str, list[str]]:
    text = (REPO_ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    out: dict[str, list[str]] = {}
    for name, body in _TABLE.findall(text):
        cols = []
        for line in body.splitlines():
            m = re.match(r"^  (\w+) ", line)
            if m and m.group(1) not in _NOT_COLUMNS:
                cols.append(m.group(1))
        out[name] = cols
    return out


def test_schema_doc_matches_the_migrated_database(pg: psycopg.Connection) -> None:
    rows = pg.execute(
        "select table_name, column_name from information_schema.columns where table_schema = 'public'"
        " and table_name <> all(array['naadi_alembic_version', 'alembic_version']) and table_name <> all(%s)"
        " order by table_name, ordinal_position",
        (list(AEGRA_TABLES),),
    ).fetchall()
    actual: dict[str, list[str]] = {}
    for table, column in rows:
        actual.setdefault(table, []).append(column)
    assert documented_tables() == actual
