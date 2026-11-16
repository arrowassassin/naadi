"""Pure analytics: blueprint quotas, weighted accuracy, by-system states, readiness thresholds, diagnostic choice."""

from __future__ import annotations

import collections
import json
import uuid

import pytest

from naadi_api.analytics.blueprint import SystemWeight, quotas, system_weights, weighted_accuracy
from naadi_api.analytics.student import Cells, by_system, readiness
from naadi_api.plan.diagnostic import Candidate, choose_diagnostic, diagnostic_seed

from ..plan.helpers import DATA

W = [
    SystemWeight("a", "A", 10, 14, 1),  # 12
    SystemWeight("b", "B", 4, 8, 2),  # 6
    SystemWeight("c", "C", 1, 3, 3),  # 2
]


def test_system_weights_skip_unweighted_and_sort() -> None:
    rows = [
        {"id": "x", "kind": "system", "label": "X", "weight_min": None, "weight_max": 3, "sort": 1},
        {"id": "b", "kind": "system", "label": "B", "weight_min": 4, "weight_max": 8, "sort": 2},
        {"id": "a", "kind": "system", "label": "A", "weight_min": 10, "weight_max": 14, "sort": 1},
    ]
    assert [w.id for w in system_weights(rows)] == ["a", "b"]
    assert system_weights(rows)[0].midpoint == 12


def test_weighted_accuracy_and_coverage() -> None:
    acc = weighted_accuracy({"a": (10, 5), "b": (4, 4)}, W)
    assert acc.score == pytest.approx((12 * 0.5 + 6 * 1.0) / 18)
    assert acc.coverage == pytest.approx(18 / 20)
    assert weighted_accuracy({}, W).score is None


def test_quotas_follow_weights_and_cap_by_availability() -> None:
    assert quotas(W, 20, {"a": 99, "b": 99, "c": 99}) == {"a": 12, "b": 6, "c": 2}
    capped = quotas(W, 20, {"a": 99, "b": 99, "c": 0})
    assert capped["c"] == 0 and sum(capped.values()) == 20
    assert quotas(W, 20, {"a": 3, "b": 2, "c": 1}) == {"a": 3, "b": 2, "c": 1}


def test_by_system_states() -> None:
    stats = by_system(W, {"a": (10, 5), "b": (4, 4), "c": (20, 19)}, min_attempts=5, weak_below=0.55)
    assert [(s.id, s.percent, s.state) for s in stats] == [
        ("a", 50, "weak"),
        ("b", None, "insufficient"),
        ("c", 95, "ok"),
    ]


def test_readiness_withheld_until_enough_answered_and_covered() -> None:
    cells = Cells(recent={}, first={"a": (30, 21), "b": (10, 5)}, answered=39)
    r = readiness(W, cells, min_answered=40, min_coverage=0.5)
    assert r.score is None and r.coverage == 90 and r.answered == 39
    r = readiness(W, Cells({}, cells.first, 40), min_answered=40, min_coverage=0.5)
    assert r.score == round(100 * (12 * 0.7 + 6 * 0.5) / 18)
    thin = readiness(W, Cells({}, {"c": (50, 50)}, 50), min_answered=40, min_coverage=0.5)
    assert thin.score is None and thin.coverage == 10


def _real_pool() -> tuple[list[Candidate], list[SystemWeight]]:
    qs = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    tax = json.loads((DATA / "question_taxonomy_map.json").read_text(encoding="utf-8"))
    nodes = json.loads((DATA / "taxonomy.json").read_text(encoding="utf-8"))
    weights = system_weights([n for n in nodes if n["kind"] == "system" and "step1" in n["exam"]])
    pool = [
        Candidate(q["id"], tuple(tax.get(q["id"], [])), q.get("difficulty"))
        for q in qs
        if q["exam"] == "step1" and q.get("block") is None
    ]
    return pool, weights


def test_diagnostic_is_40_distinct_stratified_and_deterministic() -> None:
    pool, weights = _real_pool()
    seed = diagnostic_seed(uuid.UUID(int=7))
    ids = choose_diagnostic(pool, weights, 40, seed)
    assert len(ids) == len(set(ids)) == 40
    assert ids == choose_diagnostic(pool, weights, 40, seed)
    assert ids != choose_diagnostic(pool, weights, 40, diagnostic_seed(uuid.UUID(int=8)))
    by_id = {q.id: q for q in pool}
    systems = collections.Counter(t for i in ids for t in by_id[i].taxonomy_ids if ".sys." in t)
    assert len(systems) >= 9  # mixed: most of the 11 systems
    # the heaviest systems get the most questions
    assert systems["step1.sys.reproductive_endocrine"] >= systems["step1.sys.biostat_epi"]
    diffs = collections.Counter(by_id[i].difficulty for i in ids)
    assert set(diffs) >= {"easy", "medium", "hard"}


def test_diagnostic_never_exceeds_the_pool() -> None:
    pool = [Candidate(f"q{i}", ("a",), "easy") for i in range(5)]
    assert len(choose_diagnostic(pool, W, 40, 1)) == 5
