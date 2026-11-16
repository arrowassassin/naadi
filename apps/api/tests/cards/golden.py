"""Shared FSRS golden vectors: packages/shared/fixtures/fsrs-golden.json.

Built from the API's scheduler (the official `fsrs` package) and replayed by the web's ts-fsrs scheduler
(apps/web/src/cards/fsrs.test.ts), so a drift on either side fails a test. `test_golden.py` fails when the file is
stale; regenerate with

    cd apps/api && uv run python -m tests.cards.golden

Cases give review times as local wall-clock times in an IANA zone (around midnight and DST changes, including
Lord Howe's 30-minute shift and Chatham's +12:45), so both sides must agree on the instant and on the local
calendar day a card falls due in that zone.
"""

from __future__ import annotations

import json
import random
import sys
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from naadi_api.cards.scheduler import FsrsParams, Memory, epoch_ms, review
from naadi_api.cards.stats import local_date
from naadi_api.settings import REPO_ROOT

GOLDEN_PATH = REPO_ROOT / "packages" / "shared" / "fixtures" / "fsrs-golden.json"
DEFAULT_WEIGHTS = (
    0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722, 0.1666, 0.796,
    1.4835, 0.0614, 0.2629, 1.6483, 0.6014, 1.8729, 0.5425, 0.0912, 0.0658, 0.1542,
)  # fmt: skip

CONFIGS: dict[str, dict[str, Any]] = {
    "defaults": {
        "weights": list(DEFAULT_WEIGHTS),
        "desired_retention": 0.9,
        "learning_steps_s": [60, 600],
        "relearning_steps_s": [600],
        "maximum_interval": 36500,
        "enable_fuzz": False,
    },
    "fuzz": {
        "weights": list(DEFAULT_WEIGHTS),
        "desired_retention": 0.9,
        "learning_steps_s": [60, 600],
        "relearning_steps_s": [600],
        "maximum_interval": 36500,
        "enable_fuzz": True,
    },
    "custom": {
        "weights": [
            0.4,
            1.2,
            3.1,
            15.7,
            7.2,
            0.53,
            1.46,
            0.005,
            1.55,
            0.12,
            1.02,
            1.94,
            0.11,
            0.3,
            2.27,
            0.23,
            2.99,
            0.52,
            0.66,
            0.1,
            0.2,
        ],
        "desired_retention": 0.85,
        "learning_steps_s": [120, 1800, 86400 * 2],
        "relearning_steps_s": [300, 3600],
        "maximum_interval": 180,
        "enable_fuzz": True,
    },
}

# (zone, [(local wall-clock time, rating), ...]): around midnight and DST changes. A wall time in a DST gap or
# overlap is resolved the way zoneinfo does with fold=0 (the earlier offset); the instant is what both sides use.
HANDPICKED: list[tuple[str, str, list[tuple[str, int]]]] = [
    (
        "ny-spring-forward",
        "America/New_York",
        [
            ("2026-03-07T23:50", 3),
            ("2026-03-08T00:05", 3),
            ("2026-03-08T01:59", 3),
            ("2026-03-08T03:01", 4),
            ("2026-03-10T23:59", 3),
            ("2026-03-18T00:00", 2),
            ("2026-04-02T12:00", 1),
            ("2026-04-02T12:12", 3),
        ],
    ),
    (
        "ny-fall-back",
        "America/New_York",
        [
            ("2026-10-31T23:30", 3),
            ("2026-11-01T00:59", 3),
            ("2026-11-01T01:30", 3),
            ("2026-11-01T02:30", 3),
            ("2026-11-04T01:30", 3),
            ("2026-11-15T23:59", 4),
            ("2027-01-20T08:00", 1),
        ],
    ),
    (
        "london-both",
        "Europe/London",
        [
            ("2026-03-28T23:55", 3),
            ("2026-03-29T00:30", 3),
            ("2026-03-29T02:30", 3),
            ("2026-03-31T00:01", 3),
            ("2026-10-24T23:59", 3),
            ("2026-10-25T01:30", 2),
            ("2026-10-26T00:00", 3),
        ],
    ),
    (
        "lord-howe-half-hour",
        "Australia/Lord_Howe",
        [
            ("2026-04-04T23:50", 3),
            ("2026-04-05T01:45", 3),
            ("2026-04-05T02:15", 3),
            ("2026-04-06T00:10", 3),
            ("2026-10-03T23:58", 1),
            ("2026-10-04T02:10", 3),
            ("2026-10-04T02:40", 3),
        ],
    ),
    (
        "chatham-1245",
        "Pacific/Chatham",
        [
            ("2026-04-04T23:59", 3),
            ("2026-04-05T03:30", 3),
            ("2026-04-06T00:00", 4),
            ("2026-09-27T02:50", 3),
            ("2026-09-27T03:50", 1),
            ("2026-09-27T04:05", 3),
        ],
    ),
    (
        "st-johns-half",
        "America/St_Johns",
        [
            ("2026-11-16T23:59", 3),
            ("2026-11-17T00:01", 3),
            ("2026-11-17T00:20", 3),
            ("2026-11-18T23:30", 3),
            ("2026-12-31T23:59", 4),
            ("2027-03-14T01:59", 1),
            ("2027-03-14T03:00", 3),
        ],
    ),
    (
        "kolkata-midnight",
        "Asia/Kolkata",
        [
            ("2026-11-16T23:59", 3),
            ("2026-11-17T00:00", 3),
            ("2026-11-17T00:11", 3),
            ("2026-11-17T23:59", 3),
            ("2026-11-21T05:30", 3),
            ("2026-12-10T05:29", 2),
            ("2027-02-01T21:00", 4),
        ],
    ),
    (
        "lapse-and-relearn",
        "UTC",
        [
            ("2026-01-01T09:00", 4),
            ("2026-01-20T09:00", 1),
            ("2026-01-20T09:10", 1),
            ("2026-01-20T09:30", 2),
            ("2026-01-20T10:00", 3),
            ("2026-01-25T09:00", 3),
            ("2026-03-01T09:00", 1),
            ("2026-03-01T09:11", 4),
        ],
    ),
    (
        "hard-on-later-step",
        "UTC",
        [
            ("2026-05-01T08:00", 3),
            ("2026-05-01T08:10", 2),
            ("2026-05-01T08:20", 2),
            ("2026-05-01T08:30", 3),
            ("2026-05-03T08:00", 2),
            ("2026-05-03T08:01", 1),
            ("2026-05-03T08:11", 2),
        ],
    ),
    (
        "long-gaps-and-cap",
        "UTC",
        [
            ("2026-01-01T00:00", 4),
            ("2027-01-01T00:00", 4),
            ("2029-01-01T00:00", 4),
            ("2035-06-01T00:00", 4),
            ("2050-01-01T00:00", 4),
        ],
    ),
]
RANDOM_CASES = 120
RANDOM_ZONES = ("UTC", "America/New_York", "Europe/London", "Asia/Kolkata", "Australia/Lord_Howe", "Pacific/Chatham")


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _random_cases() -> list[tuple[str, str, list[tuple[datetime, int]]]]:
    rng = random.Random(20261116)  # noqa: S311 - reproducible test data, not security
    out: list[tuple[str, str, list[tuple[datetime, int]]]] = []
    for i in range(RANDOM_CASES):
        zone = RANDOM_ZONES[i % len(RANDOM_ZONES)]
        t = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(milliseconds=rng.randrange(0, 400 * 86_400_000))
        seq: list[tuple[datetime, int]] = []
        for _ in range(rng.randrange(2, 16)):
            seq.append((t, rng.choice((1, 2, 3, 3, 3, 4))))
            t += timedelta(
                milliseconds=rng.choice(
                    (
                        rng.randrange(1_000, 3_600_000),
                        rng.randrange(0, 40 * 86_400_000),
                        86_400_000 * rng.randrange(1, 4) - rng.randrange(0, 7_200_000),
                        rng.randrange(0, 300 * 86_400_000),
                    )
                )
            )
        out.append((f"random-{i:03d}", zone, seq))
    return out


def _run(cfg_name: str, case_id: str, zone: str, reviews: list[tuple[datetime, int]]) -> dict[str, Any]:
    params = FsrsParams.from_config(CONFIGS[cfg_name])
    tz = ZoneInfo(zone)
    card_id = f"golden-{case_id}"
    memory = Memory()
    steps = []
    for at, rating in reviews:
        out = review(params, card_id, memory, rating, at)
        memory = out.memory
        assert memory.due is not None and memory.stability is not None and memory.difficulty is not None
        steps.append(
            {
                "at": _iso(at),
                "at_ms": epoch_ms(at),
                "rating": rating,
                "expect": {
                    "state": memory.state,
                    "step": memory.step,
                    "stability": memory.stability,
                    "difficulty": memory.difficulty,
                    "due": _iso(memory.due),
                    "due_ms": epoch_ms(memory.due),
                    "due_local_date": local_date(memory.due, tz).isoformat(),
                    "elapsed_days": out.elapsed_days,
                    "scheduled_days": out.scheduled_days,
                    "reps": memory.reps,
                    "lapses": memory.lapses,
                },
            }
        )
    return {"id": f"{cfg_name}/{case_id}", "config": cfg_name, "card_id": card_id, "zone": zone, "reviews": steps}


def build() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for cfg_name in CONFIGS:
        for case_id, zone, local in HANDPICKED:
            tz = ZoneInfo(zone)
            reviews = [(datetime.fromisoformat(s).replace(tzinfo=tz), r) for s, r in local]
            cases.append(_run(cfg_name, case_id, zone, reviews))
        for case_id, zone, seq in _random_cases():
            cases.append(_run(cfg_name, case_id, zone, seq))
    return {
        "about": "FSRS-6 golden vectors: generated by apps/api/tests/cards/golden.py from the API scheduler (fsrs "
        "6.3.3); replayed by the web scheduler (ts-fsrs 6.0.0). Times are UTC instants; due_local_date is the "
        "calendar day of `due` in the case's zone.",
        "tolerance": {"stability_rel": 1e-6, "difficulty_abs": 1e-6},
        "configs": CONFIGS,
        "cases": cases,
    }


def render() -> str:
    """One case per line: diffable, and a fraction of the size of an indented file."""
    data = build()
    head = {k: v for k, v in data.items() if k != "cases"}
    lines = [json.dumps(c, separators=(",", ":")) for c in data["cases"]]
    body = json.dumps(head, separators=(",", ":"))[:-1]
    return body + ',"cases":[\n' + ",\n".join(lines) + "\n]}\n"


if __name__ == "__main__":
    GOLDEN_PATH.write_text(render(), encoding="utf-8")
    sys.stdout.write(f"wrote {GOLDEN_PATH.relative_to(REPO_ROOT)}\n")
