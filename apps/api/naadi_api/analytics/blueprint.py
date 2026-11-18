"""The exam blueprint (pure, no I/O): official system weights and blueprint-weighted accuracy.

A system's weight is the midpoint of its official `weight_min`/`weight_max` (taxonomy_nodes, from usmle.org).
Shared by Today's readiness (S11), the diagnostic's stratification (S11) and the Results weight overlay (S09).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SystemWeight:
    id: str
    label: str
    weight_min: float
    weight_max: float
    sort: int = 0

    @property
    def midpoint(self) -> float:
        return (self.weight_min + self.weight_max) / 2


def system_weights(rows: Iterable[Mapping[str, Any]]) -> list[SystemWeight]:
    """`taxonomy_nodes` rows of kind 'system' with both weights set, in (sort, label) order."""
    out = [
        SystemWeight(str(r["id"]), str(r["label"]), float(r["weight_min"]), float(r["weight_max"]), int(r["sort"] or 0))
        for r in rows
        if r.get("kind", "system") == "system" and r.get("weight_min") is not None and r.get("weight_max") is not None
    ]
    out.sort(key=lambda w: (w.sort, w.label))
    return out


@dataclass(frozen=True, slots=True)
class WeightedAccuracy:
    score: float | None  # Σ w·(correct/graded) / Σ w over covered systems, in [0, 1]
    coverage: float  # Σ w(covered) / Σ w(all), in [0, 1]
    covered: tuple[str, ...]


def weighted_accuracy(
    cells: Mapping[str, tuple[int, int]], weights: Sequence[SystemWeight], *, min_graded: int = 1
) -> WeightedAccuracy:
    """`cells`: system id → (graded, correct). A system is covered with at least `min_graded` graded attempts."""
    total = sum(w.midpoint for w in weights)
    covered = [w for w in weights if cells.get(w.id, (0, 0))[0] >= min_graded]
    if not covered or total <= 0:
        return WeightedAccuracy(None, 0.0, ())
    num = sum(w.midpoint * cells[w.id][1] / cells[w.id][0] for w in covered)
    den = sum(w.midpoint for w in covered)
    return WeightedAccuracy(num / den, den / total, tuple(w.id for w in covered))


def quotas(weights: Sequence[SystemWeight], total: int, available: Mapping[str, int]) -> dict[str, int]:
    """`total` items shared out in proportion to the weight midpoints (largest remainder), never more than a
    system has available; a shortfall is shared out again among the systems that still have items."""
    out = dict.fromkeys((w.id for w in weights), 0)
    left = min(total, sum(available.get(w.id, 0) for w in weights))
    while left > 0:
        open_ = [w for w in weights if available.get(w.id, 0) > out[w.id]]
        if not open_:
            break
        mass = sum(w.midpoint for w in open_)
        raw = {w.id: left * w.midpoint / mass for w in open_}
        add = {k: min(int(v), available.get(k, 0) - out[k]) for k, v in raw.items()}
        rest = left - sum(add.values())
        order = sorted(open_, key=lambda w: (-(raw[w.id] - int(raw[w.id])), -w.midpoint, w.sort, w.id))
        for w in order:
            if rest <= 0:
                break
            if available.get(w.id, 0) - out[w.id] - add[w.id] > 0:
                add[w.id] += 1
                rest -= 1
        if sum(add.values()) == 0:
            break
        for k, v in add.items():
            out[k] += v
        left -= sum(add.values())
    return out
