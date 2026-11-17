"""S17's `cost_entry` placement for S15's ingest: a fee receipt becomes a proposal (amount, currency, date, label), and
only a confirmed placement writes a `costs` row; undo removes exactly it."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from naadi_api.ingest.extract.model import ExtractedDoc, Section
from naadi_api.ingest.placements import IngestView, handler_for, propose_all
from naadi_api.ingest.placements.costs import CostEntry, amounts_in, best_amount, date_in
from naadi_api.ingest.understand import Understanding


def view(text: str, kind: str = "fee_receipt", flags: dict[str, bool] | None = None) -> IngestView:
    return IngestView(
        item_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        input_kind="pdf",
        understanding=Understanding(kind=kind, title="Prometric seat receipt"),  # type: ignore[arg-type]
        doc=ExtractedDoc(kind="pdf", sections=[Section(heading="Receipt", text=text)], page_count=1),
        original_name="receipt.pdf",
        flags=flags if flags is not None else {"pathway": True, "library": True},
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Exam fee: $695.00\nTotal paid: USD 905.00", (Decimal("905.00"), "USD")),
        ("Amount paid ₹80,500.00 on 12/11/2026", (Decimal("80500.00"), "INR")),
        ("Rs. 1,200.50 courier", (Decimal("1200.50"), "INR")),
        ("580 USD application", (Decimal("580"), "USD")),
        ("Fees $110 and $460 and total $570", (Decimal("570"), "USD")),
        ("Fees $110 and $460", (Decimal("460"), "USD")),
    ],
)
def test_the_amount_a_receipt_is_for(text: str, expected: tuple[Decimal, str]) -> None:
    assert best_amount(text) == expected


def test_no_amount_no_proposal_and_nonsense_is_ignored() -> None:
    assert best_amount("Thank you for your payment") is None
    assert amounts_in("Order 123456789 on 2026") == []
    assert amounts_in("$0.00 and $99999999") == []


def test_dates_are_read_day_first_or_iso() -> None:
    assert date_in("Paid on 2026-11-12") == dt.date(2026, 11, 12)
    assert date_in("Date: 12/11/2026") == dt.date(2026, 11, 12)
    assert date_in("no date here") is None
    assert date_in("31/02/2026") is None


def test_only_a_fee_receipt_with_an_amount_is_proposed_and_the_preview_has_no_receipt_text() -> None:
    text = "Prometric seat receipt\nName: Asha Rao\nPassport K1234567\nTotal paid $210.00 on 2026-11-12"
    ranked = propose_all(view(text))
    cand = next(c for r, c in ranked if r.kind == "cost_entry")
    assert cand.preview == {
        "label": "Prometric seat receipt",
        "amount": "210.00",
        "currency": "USD",
        "paid_on": "2026-11-12",
    }
    assert "Asha" not in str(cand.preview) and "K1234567" not in str(cand.preview)
    assert CostEntry().propose(view(text, kind="reading")) == []
    assert CostEntry().propose(view("no money here")) == []


def test_the_handler_is_off_while_the_pathway_flag_is_off() -> None:
    assert handler_for("cost_entry", {"pathway": True}) is not None
    assert handler_for("cost_entry", {"pathway": False}) is None
    assert all(r.kind != "cost_entry" for r, _ in propose_all(view("Total $10", flags={"library": True})))


@pytest.fixture(scope="module")
def url() -> str:
    from tests.db.rls import database_url, ensure_migrated, require_local

    u = database_url()
    require_local(u)
    ensure_migrated(u)
    return u


@pytest.mark.db
@pytest.mark.integration
async def test_apply_writes_the_students_own_cost_once_and_undo_removes_it(url: str) -> None:
    from naadi_api.ingest.placements.registry import PlacementContext
    from tests.db.rls import create_auth_user_async

    async with await psycopg.AsyncConnection.connect(url, autocommit=True, row_factory=dict_row) as conn:
        a = await create_auth_user_async(conn)
        b = await create_auth_user_async(conn)
        try:
            v = view("Total paid $210.00 on 2026-11-12")
            cand = CostEntry().propose(v)[0]
            ctx = PlacementContext(conn=conn, user_id=a, item_id=v.item_id, source_id="user-provided", view=v)
            records = await CostEntry().apply(ctx, cand)
            assert [r.table for r in records] == ["costs"]
            row = await (await conn.execute("select * from public.costs where id = %s", (records[0].id,))).fetchone()
            assert row is not None and row["user_id"] == a
            assert (row["label"], str(row["amount"]), row["currency"], row["paid_on"]) == (
                "Prometric seat receipt",
                "210.00",
                "USD",
                dt.date(2026, 11, 12),
            )
            # applying again (a retry of the same item) writes nothing new
            assert await CostEntry().apply(ctx, cand) == []
            n = await (await conn.execute("select count(*) as n from public.costs where user_id = %s", (a,))).fetchone()
            assert n is not None and n["n"] == 1
            # undo touches only the owner's row
            other = PlacementContext(conn=conn, user_id=b, item_id=v.item_id, source_id="user-provided", view=v)
            await CostEntry().undo(other, records)
            assert (
                await (
                    await conn.execute("select count(*) as n from public.costs where id = %s", (records[0].id,))
                ).fetchone()
                or {"n": 0}
            )["n"] == 1
            await CostEntry().undo(ctx, records)
            await CostEntry().undo(ctx, records)  # idempotent
            gone = await (
                await conn.execute("select count(*) as n from public.costs where user_id = %s", (a,))
            ).fetchone()
            assert gone is not None and gone["n"] == 0
        finally:
            await conn.execute("delete from auth.users where id = any(%s)", ([a, b],))


@pytest.mark.db
@pytest.mark.integration
async def test_a_currency_the_pathway_does_not_offer_is_refused(url: str) -> None:
    from naadi_api.ingest.placements.registry import PlacementContext
    from naadi_api.pathway.db import PathwayError
    from tests.db.rls import create_auth_user_async

    async with await psycopg.AsyncConnection.connect(url, autocommit=True, row_factory=dict_row) as conn:
        a = await create_auth_user_async(conn)
        try:
            v = view("Total paid $210.00")
            cand = CostEntry().propose(v)[0]
            bad: Any = type(cand)(cand.kind, cand.score, cand.preview, {**cand.params, "currency": "EUR"})
            with pytest.raises(PathwayError):
                await CostEntry().apply(
                    PlacementContext(conn=conn, user_id=a, item_id=v.item_id, source_id="u", view=v), bad
                )
        finally:
            await conn.execute("delete from auth.users where id = any(%s)", ([a],))
