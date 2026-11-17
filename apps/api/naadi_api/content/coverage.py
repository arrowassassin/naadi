"""USMLE-2026 blueprint coverage (segment 23): published items per leaf area per Step against the blueprint.

Pure (unit-tested): `blueprint_coverage(leaves, targets, items, exam)` turns leaf areas, blueprint rows and tagged
content into per-area and per-system rows. Two callers:
- the admin API (`GET /api/admin/coverage/blueprint`, naadi_api/admin/blueprint.py) with rows read from Postgres;
- tools/coverage.py with rows read from the seed bundle (no database), run in CI.

Counting: a published shared item counts once for every leaf area it is tagged with, on its own Step. Sections
and shared cards count for both Steps (study text is not Step-specific). Status is a word, never a colour:
`below_min` (fewer items than the minimum), `below_target` (at or above the minimum, under the target) or `met`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

AreaStatus = Literal["below_min", "below_target", "met"]
Measure = Literal["items", "sections", "cards"]


@dataclass(frozen=True)
class Leaf:
    id: str
    code: str
    label: str
    system: str  # outline system code, "08"
    system_label: str


@dataclass(frozen=True)
class Target:
    min_items: int
    target_items: int
    tested: bool = True


@dataclass(frozen=True)
class Tagged:
    measure: Measure
    exam: str | None  # None: counts for every Step (sections, cards)
    tags: frozenset[str]


@dataclass
class AreaRow:
    id: str
    code: str
    label: str
    system: str
    items: int
    sections: int
    cards: int
    min_items: int
    target_items: int
    status: AreaStatus


@dataclass
class SystemRow:
    code: str
    label: str
    items: int
    target_items: int
    areas: int
    areas_at_min: int
    areas_met: int


@dataclass
class Report:
    exam: str
    areas: list[AreaRow]
    systems: list[SystemRow]

    @property
    def items(self) -> int:
        return sum(a.items for a in self.areas)

    @property
    def below_min(self) -> list[AreaRow]:
        return [a for a in self.areas if a.status == "below_min"]


def status_of(items: int, t: Target) -> AreaStatus:
    if items < t.min_items:
        return "below_min"
    return "met" if items >= t.target_items else "below_target"


def blueprint_coverage(
    leaves: Iterable[Leaf], targets: dict[tuple[str, str], Target], items: Iterable[Tagged], exam: str
) -> Report:
    leaves = [lf for lf in leaves if (t := targets.get((lf.id, exam))) is not None and t.tested]
    ids = {lf.id for lf in leaves}
    counts: dict[tuple[str, Measure], int] = defaultdict(int)
    for it in items:
        if it.exam is not None and it.exam != exam:
            continue
        for tag in it.tags & ids:
            counts[(tag, it.measure)] += 1
    areas = []
    for lf in leaves:
        t = targets[(lf.id, exam)]
        n = counts[(lf.id, "items")]
        areas.append(
            AreaRow(
                id=lf.id,
                code=lf.code,
                label=lf.label,
                system=lf.system,
                items=n,
                sections=counts[(lf.id, "sections")],
                cards=counts[(lf.id, "cards")],
                min_items=t.min_items,
                target_items=t.target_items,
                status=status_of(n, t),
            )
        )
    by_sys: dict[str, list[AreaRow]] = defaultdict(list)
    labels: dict[str, str] = {}
    for lf, a in zip(leaves, areas, strict=True):
        by_sys[lf.system].append(a)
        labels[lf.system] = lf.system_label
    systems = [
        SystemRow(
            code=code,
            label=labels[code],
            items=sum(a.items for a in rows),
            target_items=sum(a.target_items for a in rows),
            areas=len(rows),
            areas_at_min=sum(1 for a in rows if a.status != "below_min"),
            areas_met=sum(1 for a in rows if a.status == "met"),
        )
        for code, rows in sorted(by_sys.items())
    ]
    return Report(exam=exam, areas=areas, systems=systems)
