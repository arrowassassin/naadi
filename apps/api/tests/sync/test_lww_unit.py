"""Last writer wins: the golden vectors shared with the web client, and the endpoint rule for stamp-less writes."""

from __future__ import annotations

import json
from typing import Any

import pytest

from naadi_api.sync.lww import FUTURE_SKEW_MS, clamp, decide, decide_write, stamp_of

from ..conftest import REPO_ROOT

VECTORS: list[dict[str, Any]] = json.loads(
    (REPO_ROOT / "packages" / "shared" / "fixtures" / "lww_vectors.json").read_text(encoding="utf-8")
)
NOW = 1_763_400_000_000


def test_the_vector_file_covers_every_case() -> None:
    names = [v["name"] for v in VECTORS]
    assert len(names) == len(set(names))
    assert any(v["is_delete"] for v in VECTORS) and any(not v["is_delete"] for v in VECTORS)
    assert any(v["stored_ms"] is None for v in VECTORS)
    assert any(v["tombstone_ms"] is not None for v in VECTORS)
    assert any(v["incoming_ms"] > v["server_now_ms"] + FUTURE_SKEW_MS for v in VECTORS)  # clamp
    assert any(v["incoming_ms"] == v["stored_ms"] for v in VECTORS)  # ties
    for v in VECTORS:
        assert set(v) == {"name", "stored_ms", "tombstone_ms", "incoming_ms", "server_now_ms", "is_delete", "expect"}
        assert set(v["expect"]) == {"applied", "effective_ms"}


@pytest.mark.parametrize("v", VECTORS, ids=lambda v: v["name"])
def test_golden_vector(v: dict[str, Any]) -> None:
    d = decide(v["stored_ms"], v["tombstone_ms"], v["incoming_ms"], v["server_now_ms"], v["is_delete"])
    assert (d.applied, d.effective_ms) == (v["expect"]["applied"], v["expect"]["effective_ms"])


def test_both_orders_converge() -> None:
    """Two devices' edits applied in either order leave the same winner."""
    a, b = NOW - 2000, NOW - 1000
    first = decide(None, None, a, NOW, False)
    ab = decide(first.effective_ms, None, b, NOW, False)
    first = decide(None, None, b, NOW, False)
    ba = decide(first.effective_ms, None, a, NOW, False)
    assert ab.applied and not ba.applied
    assert ab.effective_ms == ba.effective_ms == b


@pytest.mark.parametrize(
    ("edit", "delete", "deleted"),
    [(NOW - 1000, NOW - 2000, False), (NOW - 2000, NOW - 1000, True), (NOW - 1500, NOW - 1500, None)],
)
def test_tombstone_versus_edit_in_both_orders(edit: int, delete: int, deleted: bool | None) -> None:
    # Order 1: the edit lands, then the delete.
    e1 = decide(NOW - 5000, None, edit, NOW, False)
    d1 = decide(e1.effective_ms, None, delete, NOW, True)
    # Order 2: the delete lands (tombstone), then the edit.
    d2 = decide(NOW - 5000, None, delete, NOW, True)
    e2 = decide(None, d2.effective_ms, edit, NOW, False)
    if deleted is None:  # a tie: whichever the server holds first stays
        assert not d1.applied and not e2.applied
        return
    assert d1.applied is deleted
    assert e2.applied is (not deleted)
    final1 = "deleted" if d1.applied else "edited"
    final2 = "edited" if e2.applied else "deleted"
    assert final1 == final2


def test_a_state_after_a_losing_write_says_deleted() -> None:
    assert decide(None, NOW - 1000, NOW - 2000, NOW, False).deleted is True
    assert decide(NOW - 1000, None, NOW - 2000, NOW, True).deleted is False
    assert decide(NOW - 1000, NOW - 2000, NOW - 3000, NOW, False).deleted is False


def test_clamp_and_stamp() -> None:
    assert clamp(NOW + 10 * FUTURE_SKEW_MS, NOW) == NOW + FUTURE_SKEW_MS
    assert clamp(NOW - 1, NOW) == NOW - 1
    assert stamp_of(None, NOW) == NOW
    assert stamp_of(NOW + 10 * FUTURE_SKEW_MS, NOW) == NOW + FUTURE_SKEW_MS


def test_a_write_without_a_stamp_always_applies_above_what_is_held() -> None:
    assert decide_write(None, None, None, NOW, False).effective_ms == NOW
    # a stored stamp clamped into the future: the server-stamped write still applies, one ms above it
    held = NOW + FUTURE_SKEW_MS
    d = decide_write(None, held, None, NOW, False)
    assert d.applied and d.effective_ms == held + 1
    assert decide_write(None, None, NOW + 5, NOW, True).effective_ms == NOW + 6
    # with a stamp it is the vector rule
    assert not decide_write(NOW - 2, NOW - 1, None, NOW, False).applied
