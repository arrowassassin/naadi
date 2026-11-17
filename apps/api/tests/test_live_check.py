"""Unit tests for the live schema checker's evaluators (no network, no database)."""

from __future__ import annotations

from typing import Any

import pytest

from .db.live_check import USER_TABLES, evaluate_user_tables, ok_column

ALL = ["DELETE", "INSERT", "SELECT", "UPDATE"]


def good_rows() -> list[dict[str, Any]]:
    return [{"table_name": t, "exists": True, "forced": True, "cmds": list(ALL)} for t in USER_TABLES]


def test_all_user_tables_pass() -> None:
    assert len(USER_TABLES) == 28
    assert evaluate_user_tables(good_rows()) == (True, "28 tables")


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda rows: rows[3].update(cmds=[]), "card_reviews: policies []"),  # zero policies
        (lambda rows: rows[4].update(cmds=["INSERT", "SELECT", "UPDATE"]), "notes: policies"),
        (lambda rows: rows[0].update(forced=False), "profiles: RLS not forced"),
        (lambda rows: rows[7].update(exists=False, cmds=[]), "uploads: missing"),
        (lambda rows: rows.pop(), "push_subscriptions: missing"),
        (lambda rows: rows[5].update(cmds=[*ALL, "SELECT"]), "notebooks: policies"),
    ],
)
def test_mutated_fixture_fails(mutate: Any, expected: str) -> None:
    rows = good_rows()
    mutate(rows)
    ok, detail = evaluate_user_tables(rows)
    assert not ok
    assert expected in detail


def test_ok_column() -> None:
    assert ok_column([{"ok": True, "detail": "x"}]) == (True, "x")
    assert ok_column([{"ok": False}]) == (False, "")
    assert ok_column([]) == (False, "no row")
