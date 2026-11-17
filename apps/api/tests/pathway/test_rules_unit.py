"""Pure rules: re-verify (day 90 vs 91, flagged), due rules, official hosts, expiry, money per currency, LWW."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from naadi_api.pathway import lww, rules
from naadi_api.pathway.store import summarise_costs

TODAY = dt.date(2026, 11, 17)


@pytest.mark.parametrize(
    ("age_days", "flagged", "expected"),
    [
        (0, False, None),
        (89, False, None),
        (90, False, None),
        (91, False, "age"),
        (400, False, "age"),
        (0, True, "flagged"),
        (200, True, "flagged"),
    ],
)
def test_reverify_rule(age_days: int, flagged: bool, expected: str | None) -> None:
    assert rules.reverify_reason(TODAY - dt.timedelta(days=age_days), flagged, TODAY, 90) == expected


def test_a_task_never_verified_is_stale_and_the_window_is_configurable() -> None:
    assert rules.reverify_reason(None, False, TODAY, 90) == "age"
    assert rules.reverify_reason(TODAY - dt.timedelta(days=31), False, TODAY, 30) == "age"
    assert rules.reverify_reason(TODAY - dt.timedelta(days=30), False, TODAY, 30) is None
    assert rules.days_since(TODAY + dt.timedelta(days=3), TODAY) == 0  # never negative
    assert rules.days_since(None, TODAY) is None


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ("start", {"kind": "anchor", "anchor": "start", "days": 0}),
        ("start+7d", {"kind": "anchor", "anchor": "start", "days": 7}),
        ("step1_date-60d", {"kind": "anchor", "anchor": "step1_date", "days": -60}),
        ("residency_end-540d", {"kind": "anchor", "anchor": "residency_end", "days": -540}),
        ("observership_start-90d", {"kind": "anchor", "anchor": "observership_start", "days": -90}),
        ("date:2027-02-15", {"kind": "date", "date": "2027-02-15"}),
        ("annual:04-15", {"kind": "annual", "month": 4, "day": 15}),
        ("whenever", None),
        ("date:2027-13-45", None),
        ("annual:13-01", None),
        ("", None),
        (None, None),
    ],
)
def test_due_rules_become_structure(rule: str | None, expected: dict[str, object] | None) -> None:
    assert rules.parse_due_rule(rule) == expected


def test_every_seeded_due_rule_parses() -> None:
    import json
    from pathlib import Path

    tasks = json.loads((Path(__file__).resolve().parents[4] / "data/pathway/pathway_tasks.json").read_text())
    bad = sorted({t["due_rule"] for t in tasks if rules.parse_due_rule(t["due_rule"]) is None})
    assert bad == []


def test_official_hosts() -> None:
    domains = ["ecfmg.org", "usmle.org"]
    assert rules.is_official_url("https://www.ecfmg.org/x", domains)
    assert rules.is_official_url("https://ECFMG.org", domains)
    assert not rules.is_official_url("https://evilecfmg.org/x", domains)
    assert not rules.is_official_url("https://ecfmg.org.evil.com/", domains)
    assert not rules.is_official_url("http://www.ecfmg.org/x", domains)  # https only
    assert not rules.is_official_url("javascript:alert(1)", domains)
    assert not rules.is_official_url(None, domains)
    assert rules.https_host("https://user:pw@www.usmle.org:8443/a") == "www.usmle.org"


@pytest.mark.parametrize(
    ("expires", "expected"),
    [
        (None, (None, "none")),
        (TODAY + dt.timedelta(days=61), (61, "ok")),
        (TODAY + dt.timedelta(days=60), (60, "ok")),
        (TODAY + dt.timedelta(days=59), (59, "soon")),
        (TODAY, (0, "soon")),
        (TODAY - dt.timedelta(days=1), (-1, "expired")),
    ],
)
def test_vault_expiry(expires: dt.date | None, expected: tuple[int | None, str]) -> None:
    assert rules.expiry_state(expires, TODAY, 60) == expected


def test_money_is_summed_per_currency_and_never_converted() -> None:
    m: dict[str, Decimal] = {}
    rules.add_money(m, "USD", Decimal("10.10"))
    rules.add_money(m, "INR", Decimal("800"))
    rules.add_money(m, "USD", Decimal("0.20"))
    rules.add_money(m, "USD", None)
    assert m == {"USD": Decimal("10.30"), "INR": Decimal("800")}
    assert rules.money_list({"USD": Decimal(0), "INR": Decimal(5)}) == [("INR", Decimal(5))]


def test_summary_totals_by_stage_and_currency() -> None:
    stages = [{"key": "a"}, {"key": "b"}, {"key": "c"}]
    planned = [{"stage_key": "a", "planned": "100"}, {"stage_key": "b", "planned": "0"}]
    mk = lambda i, stage, amount, cur, paid: {  # noqa: E731
        "id": i,
        "stage_key": stage,
        "label": f"L{i}",
        "amount": amount,
        "currency": cur,
        "paid_on": dt.date(2026, 1, 1) if paid else None,
        "created_at": dt.datetime(2026, 1, i, tzinfo=dt.UTC),
    }
    entries = [
        mk(1, "a", "10.10", "USD", True),
        mk(2, "a", "20", "USD", True),
        mk(3, "a", "800", "INR", True),
        mk(4, "b", "5", "USD", False),
        mk(5, None, "7", "INR", True),
        mk(6, "a", "3", "USD", False),
    ]
    s = summarise_costs(stages, planned, entries, ["USD", "INR"])
    by = {x["stage_key"]: x for x in s["by_stage"]}
    assert [x["stage_key"] for x in s["by_stage"]] == ["a", "b", None]  # stage order, unfiled last, c omitted (empty)
    assert by["a"]["paid"] == [{"currency": "INR", "amount": "800.00"}, {"currency": "USD", "amount": "30.10"}]
    assert by["a"]["upcoming"] == [{"currency": "USD", "amount": "3.00"}]
    assert by["a"]["planned_usd"] == "100.00" and by["a"]["entries"] == 4
    assert s["paid"] == [{"currency": "INR", "amount": "807.00"}, {"currency": "USD", "amount": "30.10"}]
    assert s["upcoming"] == [{"currency": "USD", "amount": "8.00"}]
    assert s["planned_usd"] == "100.00"
    assert s["next_fee"]["id"] == 4  # the oldest entry without a paid date
    empty = summarise_costs(stages, [], [], ["USD"])
    assert empty["paid"] == [] and empty["next_fee"] is None and empty["by_stage"] == []


def test_lww_rules() -> None:
    now = 1_000_000_000_000
    assert lww.incoming_wins(5, None, now)  # nothing stored
    assert lww.incoming_wins(11, 10, now)
    assert not lww.incoming_wins(10, 10, now)  # a tie goes to the server
    assert not lww.incoming_wins(9, 10, now)
    far = now + 24 * 3600 * 1000
    assert lww.clamp_stamp(far, now) == now + lww.MAX_SKEW_MS
    assert lww.clamp_stamp(-5, now) == 0
    stored = lww.clamp_stamp(far, now)
    assert lww.incoming_wins(now + lww.MAX_SKEW_MS + 1, stored, now) is False  # clamped again: no freezing beyond skew
    assert lww.incoming_wins(stored + 1, stored, stored + 10_000)
