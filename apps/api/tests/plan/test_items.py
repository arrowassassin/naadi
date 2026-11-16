from __future__ import annotations

import pytest

from naadi_api.plan.generator import generate
from naadi_api.plan.items import ItemMinutes, plan_items, question_count

from .helpers import REFERENCE, template


@pytest.mark.parametrize(
    ("text", "n"),
    [
        ("2 blocks × 40, tutor→timed (by system)", 80),
        ("3 blocks × 40 timed random", 120),
        ("1 block × 40 timed random", 40),
        ("40 tutor (random)", 40),
        ("40 timed random", 40),
        ("Redo flagged/missed book questions", None),
        (None, None),
    ],
)
def test_question_count(text: str | None, n: int | None) -> None:
    assert question_count(text) == n


@pytest.fixture(scope="module")
def items() -> dict[int, list]:  # type: ignore[type-arg]
    return plan_items(generate(template(), REFERENCE), "step1", ItemMinutes())


def test_texts_stay_verbatim_and_external_resources_are_marked(items: dict[int, list]) -> None:  # type: ignore[type-arg]
    day1 = items[1]
    assert day1[0].kind == "assessment"
    assert day1[0].ref_type == "external" and day1[0].ref_id == "nbme"
    assert day1[0].title.startswith("BASELINE: NBME Free 120")
    assert [i.kind for i in day1] == ["assessment", "read", "admin"]
    day3 = items[3]
    assert [i.kind for i in day3] == ["read", "questions", "questions", "cards", "admin"]
    assert day3[0].ref_type == "chapter" and day3[0].ref_id == "ch01"
    assert day3[2].target_count == 80 and day3[2].ref_type == "qbank"
    assert day3[3].title == "Anki new cards for today's topic" and day3[3].ref_type == "cards"


def test_naadi_mock_and_rest_day(items: dict[int, list]) -> None:  # type: ignore[type-arg]
    assert items[45][0].ref_type == "mock"
    assert [i.kind for i in items[60]] == ["rest"]
    assert items[60][0].target_minutes == 120


def test_minutes_fill_the_day_without_overflow(items: dict[int, list]) -> None:  # type: ignore[type-arg]
    days = {d.day_no: d for d in generate(template(), REFERENCE)}
    for n, its in items.items():
        total = sum(i.target_minutes or 0 for i in its)
        cap = (days[n].target_hours or 0) * 60
        assert total <= cap + 5 * len(its), (n, total, cap)
        assert all((i.target_minutes or 0) >= 5 for i in its)
