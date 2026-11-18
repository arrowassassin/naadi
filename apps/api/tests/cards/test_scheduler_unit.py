"""Unit tests (no database): the scheduler against the shared golden vectors, the seeded fuzz RNG against values
computed by JavaScript, local-day arithmetic across DST, and card faces."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fsrs import Scheduler

from naadi_api.cards import docs, stats
from naadi_api.cards.scheduler import (
    NEW,
    FsrsParams,
    Memory,
    epoch_ms,
    fnv1a32,
    mulberry32_first,
    review,
    validate_params,
)
from naadi_api.config_api.defaults import file_values
from naadi_api.settings import REPO_ROOT

from .golden import CONFIGS, DEFAULT_WEIGHTS, GOLDEN_PATH, render

# Computed with apps/web/src/cards/fsrs.ts (fnv1a32 over UTF-16 code units, then the first mulberry32 value).
JS_RNG = [
    ("", 2166136261, 0.6112444521859288),
    ("a", 3826002220, 0.621685681398958),
    ("naadi", 3247223012, 0.4315809740219265),
    ("golden-x12", 2448224046, 0.08570060040801764),
    ("ä€😀", 922931504, 0.2629598416388035),
    ("123e4567-e89b-12d3-a456-4266141740003", 1855478308, 0.16679433081299067),
]


def test_golden_file_is_current() -> None:
    """The committed vectors are what the scheduler produces now (regenerate: python -m tests.cards.golden)."""
    assert GOLDEN_PATH.read_text(encoding="utf-8") == render()


def test_golden_vectors_replay_exactly() -> None:
    data = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert len(data["cases"]) >= 300
    zones = {c["zone"] for c in data["cases"]}
    assert {"America/New_York", "Europe/London", "Australia/Lord_Howe", "Pacific/Chatham", "Asia/Kolkata"} <= zones
    n = 0
    for case in data["cases"]:
        params = FsrsParams.from_config(data["configs"][case["config"]])
        tz = ZoneInfo(case["zone"])
        m = Memory()
        for r in case["reviews"]:
            at = datetime.fromisoformat(r["at"])
            assert epoch_ms(at) == r["at_ms"]
            out = review(params, case["card_id"], m, r["rating"], at)
            m, e = out.memory, r["expect"]
            assert m.due is not None
            got = (m.state, m.step, epoch_ms(m.due), out.scheduled_days, out.elapsed_days, m.reps, m.lapses)
            want = (e["state"], e["step"], e["due_ms"], e["scheduled_days"], e["elapsed_days"], e["reps"], e["lapses"])
            assert got == want, (case["id"], r)
            assert (m.stability, m.difficulty) == (e["stability"], e["difficulty"]), (case["id"], r)
            assert stats.local_date(m.due, tz).isoformat() == e["due_local_date"]
            n += 1
    assert n > 3000


def test_default_config_is_the_package_and_ts_fsrs_defaults() -> None:
    shipped = file_values(REPO_ROOT / "config")["fsrs"]
    assert shipped == CONFIGS["defaults"]
    pkg = Scheduler()
    assert tuple(shipped["weights"]) == pkg.parameters == DEFAULT_WEIGHTS
    assert shipped["desired_retention"] == pkg.desired_retention
    assert shipped["learning_steps_s"] == [int(s.total_seconds()) for s in pkg.learning_steps]
    assert shipped["relearning_steps_s"] == [int(s.total_seconds()) for s in pkg.relearning_steps]
    assert shipped["maximum_interval"] == pkg.maximum_interval


@pytest.mark.parametrize(("seed", "hash_", "first"), JS_RNG)
def test_fuzz_rng_matches_javascript(seed: str, hash_: int, first: float) -> None:
    assert fnv1a32(seed) == hash_
    assert mulberry32_first(hash_) == first


def test_fuzz_is_seeded_by_card_and_review_count() -> None:
    params = FsrsParams.from_config(CONFIGS["fuzz"])
    at = datetime(2026, 3, 1, 9, tzinfo=UTC)
    m = Memory(state=2, stability=40.0, difficulty=5.0, due=at, last_review=at - timedelta(days=30), reps=6)
    a1 = review(params, "card-a", m, 3, at)
    assert review(params, "card-a", m, 3, at) == a1  # deterministic
    spread = {review(params, f"card-{i}", m, 3, at).scheduled_days for i in range(40)}
    assert len(spread) > 3  # fuzz on: different cards land on different days
    plain = review(FsrsParams.from_config(CONFIGS["defaults"]), "card-a", m, 3, at)
    assert min(spread) <= plain.scheduled_days <= max(spread)


def test_new_card_good_goes_to_the_second_learning_step() -> None:
    at = datetime(2026, 11, 16, 8, tzinfo=UTC)
    out = review(FsrsParams.from_config(CONFIGS["defaults"]), "c", Memory(), 3, at)
    assert (out.memory.state, out.memory.step, out.memory.due) == (1, 1, at + timedelta(minutes=10))
    assert out.memory.reps == 1 and out.elapsed_days == 0 and out.scheduled_days == 0
    assert NEW == 0


def test_review_needs_an_aware_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        review(FsrsParams.from_config(CONFIGS["defaults"]), "c", Memory(), 3, datetime(2026, 1, 1))


def test_lapse_counts_only_from_review_state() -> None:
    p = FsrsParams.from_config(CONFIGS["defaults"])
    at = datetime(2026, 1, 1, tzinfo=UTC)
    m = review(p, "c", Memory(), 4, at).memory  # Easy: straight to review
    assert m.state == 2
    after = review(p, "c", m, 1, at + timedelta(days=10)).memory
    assert (after.state, after.lapses) == (3, 1)
    again = review(p, "c", after, 1, at + timedelta(days=10, minutes=11)).memory
    assert again.lapses == 1  # Again while relearning is not another lapse


def test_weights_out_of_bounds_are_refused() -> None:
    validate_params(DEFAULT_WEIGHTS)
    with pytest.raises(ValueError, match="out of bounds"):
        validate_params([*DEFAULT_WEIGHTS[:20], 0.95])
    with pytest.raises(ValueError, match="21 parameters"):
        validate_params(DEFAULT_WEIGHTS[:19])


# ── local days ──


@pytest.mark.parametrize(
    ("zone", "now", "today", "tomorrow", "after"),
    [
        # New York spring forward: 8 March 2026 is 23 hours long
        ("America/New_York", "2026-03-08T12:00Z", "2026-03-08T05:00Z", "2026-03-09T04:00Z", "2026-03-10T04:00Z"),
        # fall back: 1 November 2026 is 25 hours long
        ("America/New_York", "2026-11-01T12:00Z", "2026-11-01T04:00Z", "2026-11-02T05:00Z", "2026-11-03T05:00Z"),
        # Lord Howe moves by 30 minutes (UTC+11 -> +10:30 on 5 April 2026)
        ("Australia/Lord_Howe", "2026-04-05T06:00Z", "2026-04-04T13:00Z", "2026-04-05T13:30Z", "2026-04-06T13:30Z"),
        ("Asia/Kolkata", "2026-11-16T20:00Z", "2026-11-16T18:30Z", "2026-11-17T18:30Z", "2026-11-18T18:30Z"),
        ("Pacific/Chatham", "2026-11-16T20:00Z", "2026-11-16T10:15Z", "2026-11-17T10:15Z", "2026-11-18T10:15Z"),
    ],
)
def test_day_bounds_follow_the_zone(zone: str, now: str, today: str, tomorrow: str, after: str) -> None:
    def t(s: str) -> datetime:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    assert stats.day_bounds(t(now), ZoneInfo(zone)) == (t(today), t(tomorrow), t(after))


def test_skipped_midnight_starts_the_day_at_the_first_real_instant() -> None:
    # America/Santiago skips 00:00-01:00 on 6 Sept 2026 (DST starts at midnight)
    tz = ZoneInfo("America/Santiago")
    start = stats.start_of_day(date(2026, 9, 6), tz)
    assert start.astimezone(tz).date() == date(2026, 9, 6)
    assert (start - timedelta(seconds=1)).astimezone(tz).date() == date(2026, 9, 5)


def test_streak_counts_back_from_today_or_yesterday() -> None:
    today = date(2026, 11, 16)
    days = {today - timedelta(days=i) for i in range(5)}
    assert stats.streak(days, today) == 5
    assert stats.streak(days - {today}, today) == 4  # not yet reviewed today: the streak is still alive
    assert stats.streak({today - timedelta(days=2)}, today) == 0
    assert stats.streak(set(), today) == 0


def test_zone_validation_and_fallback() -> None:
    assert stats.valid_zone("Asia/Kolkata") and not stats.valid_zone("Mars/Olympus") and not stats.valid_zone("../x")
    assert str(stats.zone("Mars/Olympus")) == stats.DEFAULT_ZONE and str(stats.zone(None)) == stats.DEFAULT_ZONE


# ── faces ──


def test_text_becomes_paragraphs_and_line_breaks_never_markup() -> None:
    text = "  Which RTA causes\nhyperkalemia?\n\n\n\n<b>Type 4</b> & co  "
    doc = docs.text_to_doc(text)
    assert doc == {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "Which RTA causes"},
                    {"type": "hardBreak"},
                    {"type": "text", "text": "hyperkalemia?"},
                ],
            },
            {"type": "paragraph", "content": [{"type": "text", "text": "<b>Type 4</b> & co"}]},
        ],
    }
    assert (
        docs.text_to_html(text) == "<p>Which RTA causes<br>hyperkalemia?</p><p>&lt;b&gt;Type 4&lt;/b&gt; &amp; co</p>"
    )
    assert docs.doc_to_text(doc) == "Which RTA causes\nhyperkalemia?\n\n<b>Type 4</b> & co"


def test_html_only_faces_render_as_their_text() -> None:
    markup = "<p>Low <b>aldo</b><br>K<sup>+</sup> high</p><script>alert(1)</script><p>Type 4</p>"
    assert docs.html_to_text(markup) == "Low aldo\nK+ high\n\nType 4"
    assert docs.face_doc(None, markup)["content"][1] == {
        "type": "paragraph",
        "content": [{"type": "text", "text": "Type 4"}],
    }
    assert docs.face_text({"type": "doc", "content": []}, markup) == ""
