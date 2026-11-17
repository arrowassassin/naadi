"""Coach (no database, no model): candidates from facts, the number validator, LLM parsing and every fallback."""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from naadi_api.analytics.student import CoachFacts
from naadi_api.coach.run import PassStats, word
from naadi_api.coach.suggest import Section, candidates, numbers_ok, parse_llm, rules
from naadi_api.llm import LLMUnavailable
from naadi_api.llm_budget import BudgetExhausted

FACTS = CoachFacts(
    exam="step1",
    weak_systems=(("step1.sys.respiratory_renal", "Respiratory & Renal/Urinary Systems", 22, 9, 41),),
    misses_7d=9,
    miss_systems=(("step1.sys.respiratory_renal", "Respiratory & Renal/Urinary Systems", 6),),
    changed_answers=3,
    right_to_wrong=2,
    plan_behind_items=4,
    plan_behind_days=2,
    plan_skipped_items=0,
    cards_due=62,
    card_retention=88,
)
SECTION = Section("ch05", "ch05-s12", "Acid–base, step by step")
UID = uuid.UUID(int=1)


def offered() -> list[Any]:
    return candidates(FACTS, catch_up_questions=20, section=SECTION, limit=4)


def test_candidates_carry_their_numbers_and_actions() -> None:
    c = offered()
    assert [x.kind for x in c] == ["catch_up_block", "review_misses", "cards_catchup", "reread_section"]
    assert c[0].facts == {
        "system": "Respiratory & Renal/Urinary Systems",
        "percent": 41,
        "attempts": 22,
        "correct": 9,
        "questions": 20,
    }
    assert c[0].action["add_items"][0] == {
        "kind": "questions",
        "title": "Respiratory & Renal/Urinary Systems",
        "ref_type": "weak_spot",
        "ref_id": "step1",
        "target_count": 20,
        "target_minutes": 60,
    }
    assert c[1].facts["misses"] == 9 and c[1].action["add_items"][0]["ref_type"] == "qbank_incorrect"
    assert c[2].facts == {"due": 62, "retention": 88}
    assert c[3].action["add_items"][0]["ref_id"] == "ch05"


def test_quiet_facts_give_no_candidates() -> None:
    quiet = CoachFacts("step1", (), 1, (), None, None, 0, 0, 0, 3, 95)
    assert candidates(quiet, catch_up_questions=20) == []


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("You got 9 of 22 renal questions right (41%).", True),
        ("Try a 20-question block tonight.", True),
        ("You missed 12 questions.", False),  # invented
        ("41.0% is low", True),
        ("Aim for 75% by Saturday.", False),
        ("Respiratory & Renal/Urinary Systems needs work.", True),
    ],
)
def test_numbers_must_come_from_the_facts(text: str, ok: bool) -> None:
    assert numbers_ok(text, offered()[0].facts) is ok


def _reply(items: list[dict[str, str]]) -> AIMessage:
    return AIMessage(content="```json\n" + json.dumps({"suggestions": items}) + "\n```")


def test_parse_keeps_valid_and_drops_invented_numbers_and_unknown_kinds() -> None:
    reply = _reply(
        [
            {"kind": "catch_up_block", "title": "Renal catch-up", "body": "You got 9 of 22 right. Do 20 tonight."},
            {"kind": "review_misses", "title": "Misses", "body": "You missed 14 questions this week."},
            {"kind": "unknown_kind", "title": "x", "body": "y"},
        ]
    )
    with pytest.raises(Exception):  # noqa: B017, PT011 - an unknown kind fails the whole schema
        parse_llm(reply.content, offered(), limit=3)
    reply = _reply(
        [
            {"kind": "catch_up_block", "title": "Renal catch-up", "body": "You got 9 of 22 right. Do 20 tonight."},
            {"kind": "review_misses", "title": "Misses", "body": "You missed 14 questions this week."},
            {"kind": "catch_up_block", "title": "Again", "body": "Duplicate."},
        ]
    )
    kept, dropped = parse_llm(reply.content, offered(), limit=3)
    assert [k.kind for k in kept] == ["catch_up_block"] and dropped == 2
    assert kept[0].source == "llm" and kept[0].facts["attempts"] == 22


class FakeGateway:
    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, Any]] = []

    async def ainvoke(self, role: str, messages: Any, ctx: Any, **_: Any) -> Any:
        self.calls.append((role, ctx))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


async def test_one_structured_job_call_with_the_students_id() -> None:
    gw = FakeGateway(_reply([{"kind": "cards_catchup", "title": "Cards", "body": "62 cards are due."}]))
    stats = PassStats()
    out = await word(gw, offered(), user_id=UID, run_id="r1", limit=3, stats=stats)
    assert [w.kind for w in out] == ["cards_catchup"] and out[0].source == "llm"
    assert len(gw.calls) == 1
    role, ctx = gw.calls[0]
    assert role == "structured" and ctx.purpose == "job" and ctx.user_id == UID


async def test_malformed_reply_retries_once_then_rules() -> None:
    gw = FakeGateway(AIMessage(content="not json"), AIMessage(content="{still not}"))
    stats = PassStats()
    out = await word(gw, offered(), user_id=UID, run_id=None, limit=3, stats=stats)
    assert len(gw.calls) == 2
    assert [w.source for w in out] == ["rules"] * 3
    assert stats.reasons == {"invalid_output": 2}


async def test_budget_exhausted_switches_the_whole_pass_to_rules() -> None:
    reset = dt.datetime(2026, 11, 17, tzinfo=dt.UTC)
    gw = FakeGateway(BudgetExhausted("daily_cap", reset, purpose="job"))
    stats = PassStats()
    first = await word(gw, offered(), user_id=UID, run_id=None, limit=3, stats=stats)
    assert stats.budget_spent and [w.source for w in first] == ["rules"] * 3
    second = await word(gw, offered(), user_id=uuid.UUID(int=2), run_id=None, limit=3, stats=stats)
    assert len(gw.calls) == 1  # no further model calls this pass
    assert [w.kind for w in second] == [c.kind for c in offered()[:3]]


async def test_unavailable_model_or_all_dropped_fall_back_to_rules() -> None:
    stats = PassStats()
    down = FakeGateway(LLMUnavailable("structured", []))
    out = await word(down, offered(), user_id=UID, run_id=None, limit=2, stats=stats)
    assert [w.source for w in out] == ["rules", "rules"]
    gw = FakeGateway(_reply([{"kind": "review_misses", "title": "Misses", "body": "You missed 99."}]))
    out = await word(gw, offered(), user_id=UID, run_id=None, limit=3, stats=stats)
    assert [w.source for w in out] == ["rules"] * 3
    assert stats.dropped == 1


def test_rules_wording_has_no_text_only_facts() -> None:
    out = rules(offered(), limit=3)
    assert all(w.title is None and w.body is None and w.facts for w in out)


MISSES = {"misses": 9, "days": 7, "questions": 9}


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("You missed twelve questions this week, triple last week.", False),  # the review's repro
        ("a hundred cards — double yesterday", False),
        ("Do Q99 and a20 block", False),  # digits after a letter count too
        ("Try block XL tonight.", False),  # roman numerals
        ("Half of them were renal.", False),
        ("A dozen more tomorrow.", False),
        ("Twenty-five questions.", False),
        ("Redo nine of them over seven days.", True),  # words whose value is in the facts are fine
        ("You missed 9 questions in the last 7 days.", True),
        ("Review your misses while they are fresh.", True),
        ("Read the IV fluids table.", False),  # IV = 4, not in facts: strict by design
    ],
)
def test_number_words_digits_and_roman_numerals_must_come_from_facts(text: str, ok: bool) -> None:
    assert numbers_ok(text, MISSES) is ok


async def test_a_spelled_out_invented_number_is_dropped_and_rules_used() -> None:
    gw = FakeGateway(
        _reply([{"kind": "review_misses", "title": "Misses", "body": "You missed twelve questions, triple last week."}])
    )
    stats = PassStats()
    out = await word(gw, offered(), user_id=UID, run_id=None, limit=3, stats=stats)
    assert [w.source for w in out] == ["rules"] * 3 and stats.dropped == 1


@pytest.mark.parametrize("reason", ["rate_limited", "turn_limit"])
async def test_a_rate_limit_gives_rules_to_this_student_only(reason: str) -> None:
    """Only the spent daily cap stops the pass using the model. A rate limit (after the gateway's bounded wait) or
    a turn cap words this one student's suggestions from rules; the next student still gets a model call."""
    reset = dt.datetime(2026, 11, 17, tzinfo=dt.UTC)
    gw = FakeGateway(
        BudgetExhausted(reason, reset, purpose="job"),
        _reply([{"kind": "cards_catchup", "title": "Cards", "body": "62 cards are due."}]),
    )
    stats = PassStats()
    first = await word(gw, offered(), user_id=UID, run_id="coach:2026-11-17", limit=3, stats=stats)
    assert [w.source for w in first] == ["rules"] * 3
    assert stats.budget_spent is False and stats.reasons == {f"budget_{reason}": 1}
    second = await word(gw, offered(), user_id=uuid.UUID(int=2), run_id="coach:2026-11-17", limit=3, stats=stats)
    assert len(gw.calls) == 2 and [w.source for w in second] == ["llm"]
    assert gw.calls[1][1].run_id == "coach:2026-11-17"
