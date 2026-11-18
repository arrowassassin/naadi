"""The onboarding diagnostic's question choice (pure): stratified by system in proportion to the blueprint.

- System quotas: `total` shared out by the official weight midpoints (largest remainder), capped by what each
  system has; a shortfall goes to the systems that still have questions.
- A question tagged with several systems counts once, for the system it is drawn for.
- Within a system, the difficulty mix follows availability (the same largest-remainder split over easy / medium /
  hard / unrated), so the block is neither all easy nor all hard.
- Deterministic for (pool, seed): the seed is derived from the student's id, so a retry yields the same block.
- Mock exam questions never reach this function (the caller's pool excludes `questions.block is not null`).
"""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from ..analytics.blueprint import SystemWeight, quotas

DIFFICULTY_ORDER = ("easy", "medium", "hard", None)


@dataclass(frozen=True, slots=True)
class Candidate:
    id: str
    taxonomy_ids: tuple[str, ...]
    difficulty: str | None


def diagnostic_seed(user_id: uuid.UUID) -> int:
    return int.from_bytes(hashlib.sha256(f"naadi-diagnostic:{user_id}".encode()).digest()[:6], "big")


def _rank(seed: int, qid: str) -> bytes:
    return hashlib.sha256(f"{seed}:{qid}".encode()).digest()


def _split(total: int, sizes: dict[str | None, int]) -> dict[str | None, int]:
    """Largest-remainder split of `total` in proportion to `sizes` (never above a size)."""
    have = sum(sizes.values())
    if have == 0 or total <= 0:
        return dict.fromkeys(sizes, 0)
    raw = {k: total * v / have for k, v in sizes.items()}
    out = {k: min(int(v), sizes[k]) for k, v in raw.items()}
    for k in sorted(sizes, key=lambda k: (-(raw[k] - int(raw[k])), DIFFICULTY_ORDER.index(k))):
        if sum(out.values()) >= min(total, have):
            break
        if out[k] < sizes[k]:
            out[k] += 1
    while sum(out.values()) < min(total, have):  # rounding left room; fill from the largest leftovers
        k = max(sizes, key=lambda k: (sizes[k] - out[k], -DIFFICULTY_ORDER.index(k)))
        out[k] += 1
    return out


def choose_diagnostic(pool: Sequence[Candidate], weights: Sequence[SystemWeight], total: int, seed: int) -> list[str]:
    """Question ids for the diagnostic, in a deterministic shuffle (systems interleaved)."""
    by_system: dict[str, list[Candidate]] = defaultdict(list)
    system_ids = {w.id for w in weights}
    for q in pool:
        for t in dict.fromkeys(q.taxonomy_ids):
            if t in system_ids:
                by_system[t].append(q)
    for qs in by_system.values():
        qs.sort(key=lambda q: _rank(seed, q.id))
    # Systems with fewer distinct questions claim theirs first, so a shared question is not counted twice.
    available = {w.id: len(by_system.get(w.id, ())) for w in weights}
    want = quotas(weights, total, available)
    taken: set[str] = set()
    picked: list[str] = []
    for w in sorted(weights, key=lambda w: (available[w.id], w.sort, w.id)):
        free = [q for q in by_system.get(w.id, ()) if q.id not in taken]
        sizes: dict[str | None, int] = {d: 0 for d in DIFFICULTY_ORDER}
        for q in free:
            sizes[q.difficulty if q.difficulty in DIFFICULTY_ORDER else None] += 1
        split = _split(min(want[w.id], len(free)), sizes)
        for d in DIFFICULTY_ORDER:
            chosen = [q for q in free if (q.difficulty if q.difficulty in DIFFICULTY_ORDER else None) == d][: split[d]]
            for q in chosen:
                taken.add(q.id)
                picked.append(q.id)
    # Questions shared between systems can leave the block short; top up from the rest of the pool by weight.
    if len(picked) < total:
        for w in sorted(weights, key=lambda w: (-w.midpoint, w.sort)):
            for q in by_system.get(w.id, ()):
                if len(picked) >= total:
                    break
                if q.id not in taken:
                    taken.add(q.id)
                    picked.append(q.id)
    return sorted(picked[:total], key=lambda i: _rank(seed, i))
