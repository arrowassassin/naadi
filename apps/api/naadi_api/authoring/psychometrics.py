"""Item statistics from live attempts (config/authoring_rules.md Part C, step 6): pure formulas.

First attempts only, tutor and timed pooled (exam mode is a different test condition). The score an item is
correlated with is the student's "rest" score: their share correct on every OTHER item they answered (item-rest
correlation, so an item never correlates with itself).

  N >= `min_n` (30): FLAG only (never an action): p < 0.20 or > 0.95, point-biserial < 0.05, a distractor chosen
                     more often than the key, median time > 2x the bank's median, >= 2 open reports.
  N >= `act_min_n` (100): the same flags plus the action-level ones (a distractor whose point-biserial is positive,
                     options nobody picks); only then may a human retire the item. Nothing here retires anything.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PsychometricRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_n: int = Field(30, ge=2, le=100_000)
    act_min_n: int = Field(100, ge=2, le=100_000)
    p_low: float = Field(0.20, ge=0, le=1)
    p_high: float = Field(0.95, ge=0, le=1)
    pb_min: float = Field(0.05, ge=-1, le=1)
    time_factor: float = Field(2.0, gt=1, le=20)
    report_flags: int = Field(2, ge=1, le=100)
    nonfunctioning_below: float = Field(0.05, ge=0, le=1)


@dataclass(frozen=True)
class FirstAttempt:
    user_id: Any
    chosen: str
    correct: bool
    seconds: int | None


def point_biserial(x: Sequence[float], y: Sequence[float]) -> float | None:
    """Pearson correlation of a 0/1 indicator `x` with a continuous `y`; None when either has no variance."""
    n = len(x)
    if n < 3 or n != len(y):
        return None
    mx = sum(x) / n
    my = sum(y) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True)) / (sx * sy)


def rest_scores(per_user: Mapping[Any, tuple[int, int]], own: Mapping[Any, bool]) -> dict[Any, float]:
    """`per_user[u] = (correct, answered)` over all of u's first attempts; `own[u]` = u's result on this item.
    The rest score is the share correct on the other items; a student with no other item has none."""
    out: dict[Any, float] = {}
    for u, was_right in own.items():
        correct, answered = per_user.get(u, (0, 0))
        others = answered - 1
        if others >= 1:
            out[u] = (correct - (1 if was_right else 0)) / others
    return out


def item_stats(
    attempts: Sequence[FirstAttempt],
    key: str,
    labels: Sequence[str],
    per_user: Mapping[Any, tuple[int, int]],
    report_count: int = 0,
    now: datetime | None = None,
) -> dict[str, Any]:
    """`{n, pCorrect, pointBiserial, optionCounts, distractorPb, medianSeconds, reportCount, computedAt}`."""
    n = len(attempts)
    counts = Counter(a.chosen for a in attempts)
    own = {a.user_id: a.correct for a in attempts}
    rest = rest_scores(per_user, own)
    users = [u for u in own if u in rest]

    def pb(indicator: Mapping[Any, bool]) -> float | None:
        return point_biserial([1.0 if indicator[u] else 0.0 for u in users], [rest[u] for u in users])

    chosen_by = {a.user_id: a.chosen for a in attempts}
    key_pb = pb(own)
    distractor_pb = {lab: pb({u: chosen_by[u] == lab for u in own}) for lab in labels if lab != key}
    times = [a.seconds for a in attempts if a.seconds is not None]
    return {
        "n": n,
        "pCorrect": (sum(1 for a in attempts if a.correct) / n) if n else None,
        "pointBiserial": key_pb,
        "optionCounts": {lab: counts.get(lab, 0) for lab in labels},
        "distractorPb": distractor_pb,
        "medianSeconds": statistics.median(times) if times else None,
        "reportCount": report_count,
        "computedAt": (now or datetime.now(UTC)).isoformat(),
    }


def flags_for(
    stats: Mapping[str, Any], key: str, rules: PsychometricRules, bank_median_seconds: float | None
) -> list[str]:
    """Flag codes for an item's stats. Below `min_n` only the report flag applies (a reader's report needs no
    sample). Flags never retire anything."""
    out: list[str] = []
    if (stats.get("reportCount") or 0) >= rules.report_flags:
        out.append("reports")
    n = int(stats.get("n") or 0)
    if n < rules.min_n:
        return out
    p = stats.get("pCorrect")
    if p is not None and (p < rules.p_low):
        out.append("p_too_low")
    if p is not None and (p > rules.p_high):
        out.append("p_too_high")
    pb = stats.get("pointBiserial")
    if pb is not None and pb < rules.pb_min:
        out.append("point_biserial_low")
    counts: Mapping[str, int] = stats.get("optionCounts") or {}
    key_n = counts.get(key, 0)
    if any(c > key_n for lab, c in counts.items() if lab != key):
        out.append("distractor_over_key")
    med = stats.get("medianSeconds")
    if med is not None and bank_median_seconds and med > rules.time_factor * bank_median_seconds:
        out.append("slow")
    if n >= rules.act_min_n:
        if any(v is not None and v > 0 for v in (stats.get("distractorPb") or {}).values()):
            out.append("distractor_pb_positive")
        if any(c / n < rules.nonfunctioning_below for lab, c in counts.items() if lab != key):
            out.append("nonfunctioning_distractor")
    return out


def may_act(n: int, rules: PsychometricRules) -> bool:
    """Retire/revise outcomes need N >= `act_min_n`; between `min_n` and that, flag for review only."""
    return n >= rules.act_min_n
