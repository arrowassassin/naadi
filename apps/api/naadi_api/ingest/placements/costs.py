"""`cost_entry`: a fee receipt becomes a cost-tracker entry (segment 17, ledger contract with S15).

Nothing is written before the student confirms (`apply` runs only after the confirm interrupt resumes). Proposing is
pure: no database, network or model. The amount, currency and date are read from the receipt's text with fixed
patterns; the preview carries those three values and a label from the document's own title, never raw receipt text,
names or numbers. The entry is the student's own `costs` row (service-role insert inside the placing transaction,
through the same rules as `POST /api/costs`); the receipt itself, if it holds personal details, never gets here: the
sensitivity gate sends it to the vault first.
"""

from __future__ import annotations

import datetime as dt
import re
import time
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from ...pathway import store as pathway_store
from ...pathway.db import PathwayError
from .registry import Candidate, IngestView, PlacementContext, PlacementRecord, register_placement

KIND = "cost_entry"
CURRENCIES = {"USD": "USD", "US$": "USD", "$": "USD", "INR": "INR", "RS": "INR", "RS.": "INR", "₹": "INR"}
_AMOUNT = r"(\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
_CUR = r"(USD|US\$|INR|Rs\.?|₹|\$)"
_BEFORE = re.compile(_CUR + r"\s?" + _AMOUNT, re.I)
_AFTER = re.compile(_AMOUNT + r"\s?" + r"(USD|INR)\b", re.I)
_TOTAL = re.compile(r"(total|amount paid|amount|paid|grand total)", re.I)
_ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_DMY = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](20\d{2})\b")


def amounts_in(text: str) -> list[tuple[Decimal, str, bool]]:
    """`(amount, currency, near_total_word)` for every money amount in `text`."""
    found: list[tuple[Decimal, str, bool]] = []
    for pattern, cur_first in ((_BEFORE, True), (_AFTER, False)):
        for m in pattern.finditer(text):
            cur_raw, num = (m.group(1), m.group(2)) if cur_first else (m.group(2), m.group(1))
            currency = CURRENCIES.get(cur_raw.upper().rstrip("."), CURRENCIES.get(cur_raw.upper()))
            if currency is None:
                continue
            try:
                value = Decimal(num.replace(",", ""))
            except InvalidOperation:
                continue
            if value <= 0 or value > Decimal(1_000_000):
                continue
            window = text[max(0, m.start() - 40) : m.start()]
            found.append((value, currency, bool(_TOTAL.search(window))))
    return found


def best_amount(text: str) -> tuple[Decimal, str] | None:
    """The amount the receipt is for: the one next to a "total" / "amount paid" word, else the largest."""
    found = amounts_in(text)
    if not found:
        return None
    totals = [f for f in found if f[2]]
    pick = max(totals or found, key=lambda f: f[0])
    return pick[0], pick[1]


def date_in(text: str) -> dt.date | None:
    for m in _ISO.finditer(text):
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            continue
    for m in _DMY.finditer(text):
        try:
            return dt.date(int(m[3]), int(m[2]), int(m[1]))  # day first, as Indian receipts write it
        except ValueError:
            continue
    return None


class CostEntry:
    kind = KIND
    inputs: frozenset[str] = frozenset()

    def propose(self, view: IngestView) -> list[Candidate]:
        if view.understanding.kind != "fee_receipt":
            return []
        text = view.doc.full_text()
        pick = best_amount(text)
        if pick is None:
            return []
        amount, currency = pick
        paid_on = date_in(text)
        label = (view.understanding.title or view.original_name or "").strip()[:120]
        preview: dict[str, Any] = {
            "label": label,
            "amount": f"{amount:.2f}",
            "currency": currency,
            "paid_on": paid_on.isoformat() if paid_on else None,
        }
        return [Candidate(KIND, 0.9, preview, dict(preview))]

    async def apply(self, ctx: PlacementContext, cand: Candidate) -> list[PlacementRecord]:
        p = cand.params
        try:
            amount = Decimal(str(p["amount"])).quantize(Decimal("0.01"))
        except (KeyError, InvalidOperation) as exc:
            raise PathwayError(422, "cost_invalid") from exc
        cur = await ctx.conn.execute("select value from public.app_config where key = 'pathway'")
        row = await cur.fetchone()
        offered = list((row["value"] if row else {}).get("currencies") or ["USD", "INR"])
        if p.get("currency") not in offered:
            raise PathwayError(422, "cost_currency_not_offered")
        paid_on = dt.date.fromisoformat(p["paid_on"]) if p.get("paid_on") else None
        label = str(p.get("label") or "").strip()[:200] or "Fee"
        cost_id = uuid.uuid5(ctx.item_id, "cost")
        now_ms = int(time.time() * 1000)
        _, created, _ = await pathway_store.create_cost(
            ctx.conn,
            ctx.user_id,
            cost_id=cost_id,
            task_id=None,
            stage_key=None,
            label=label,
            amount=amount,
            currency=str(p["currency"]),
            paid_on=paid_on,
            stamp=now_ms,
            now_ms=now_ms,
            max_costs=2000,
        )
        return [PlacementRecord("costs", str(cost_id), KIND)] if created else []

    async def undo(self, ctx: PlacementContext, records: list[PlacementRecord]) -> None:
        ids = [uuid.UUID(r.id) for r in records if r.table == "costs"]
        if ids:
            await ctx.conn.execute("delete from public.costs where user_id = %s and id = any(%s)", (ctx.user_id, ids))


register_placement(KIND, CostEntry(), requires_flag="pathway")
