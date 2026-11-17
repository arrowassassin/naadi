"""The review state machine (one approval publishes; two blind reviewers and adjudication when asked) and the
psychometric formulas (N thresholds, flags, never act below `act_min_n`)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from naadi_api.authoring.models import ReviewRatings
from naadi_api.authoring.psychometrics import (
    FirstAttempt,
    PsychometricRules,
    flags_for,
    item_stats,
    may_act,
    point_biserial,
    rest_scores,
)
from naadi_api.authoring.review import (
    Gates,
    ReviewRefused,
    ReviewRow,
    check_submission,
    hidden_count,
    human_reviewers,
    needed_reviews,
    publish_blockers,
    resolve,
    visible_rows,
)

A, B, C = (uuid.UUID(int=i) for i in (1, 2, 3))


def good_ratings(**kw: object) -> ReviewRatings:
    base: dict[str, object] = {
        "accuracy": "correct",
        "single_best_answer": True,
        "cognitive_level": "application",
        "flaws": [],
        "difficulty": "medium",
        "taxonomy_ok": True,
    }
    base.update(kw)
    return ReviewRatings.model_validate(base)


def row(who: uuid.UUID, decision: str, **kw: object) -> ReviewRow:
    return ReviewRow(who, decision, **kw)  # type: ignore[arg-type]


# ── state machine ──


def test_one_approval_publishes_by_default() -> None:
    assert resolve([], 1).state == "awaiting"
    r = resolve([row(A, "approve")], 1)
    assert (r.state, r.final, r.needed) == ("approved", "approve", 1)
    assert resolve([row(A, "reject")], 1).state == "rejected"
    assert resolve([row(A, "revise")], 1).state == "revise"


def test_a_reviewer_can_ask_for_a_second_review() -> None:
    first = [row(A, "approve", request_second=True)]
    assert needed_reviews(1, first) == 2
    r = resolve(first, 1)
    assert (r.state, r.submitted, r.needed) == ("awaiting", 1, 2)
    assert resolve([*first, row(B, "approve")], 1).state == "approved"


def test_two_approvals_are_required_when_configured() -> None:
    assert resolve([row(A, "approve")], 2).state == "awaiting"
    assert resolve([row(A, "approve"), row(B, "approve")], 2).state == "approved"
    assert resolve([row(A, "reject"), row(B, "reject")], 2).state == "rejected"
    assert resolve([row(A, "revise"), row(B, "revise")], 2).state == "revise"


@pytest.mark.parametrize(("a", "b"), [("approve", "reject"), ("approve", "revise"), ("revise", "reject")])
def test_disagreement_goes_to_a_third_reviewer_whose_decision_is_final(a: str, b: str) -> None:
    assert resolve([row(A, a), row(B, b)], 2).state == "adjudication"
    for d, state in (("approve", "approved"), ("revise", "revise"), ("reject", "rejected")):
        assert resolve([row(A, a), row(B, b), row(C, d, adjudication=True)], 2).state == state


def test_the_same_admin_cannot_fill_two_slots_or_adjudicate_their_own_item() -> None:
    ok = good_ratings()
    with pytest.raises(ReviewRefused) as e:
        check_submission(A, [row(A, "approve", request_second=True)], 1, ok, "approve")
    assert e.value.code == "already_reviewed"
    split = [row(A, "approve"), row(B, "reject")]
    for who in (A, B):
        with pytest.raises(ReviewRefused) as e:
            check_submission(who, split, 2, ok, "approve")
        assert e.value.code == "already_reviewed"
    assert check_submission(C, split, 2, ok, "approve") is True  # a third admin adjudicates


def test_a_closed_round_takes_no_more_reviews() -> None:
    with pytest.raises(ReviewRefused) as e:
        check_submission(B, [row(A, "approve")], 1, good_ratings(), "approve")
    assert e.value.code == "round_closed"


@pytest.mark.parametrize(
    "ratings",
    [good_ratings(accuracy="incorrect"), good_ratings(single_best_answer=False), good_ratings(taxonomy_ok=False)],
)
def test_an_approval_cannot_contradict_the_ratings(ratings: ReviewRatings) -> None:
    with pytest.raises(ReviewRefused) as e:
        check_submission(A, [], 1, ratings, "approve")
    assert e.value.code == "approve_contradicts_ratings"
    assert check_submission(A, [], 1, ratings, "revise") is False  # the same ratings justify a revise


def test_a_minor_issue_may_still_be_approved() -> None:
    assert check_submission(A, [], 1, good_ratings(accuracy="minor_issue"), "approve") is False


# ── blindness ──


def test_reviewers_are_blind_to_each_other_until_both_have_submitted() -> None:
    first = [row(A, "approve", ratings={"accuracy": "correct"}, request_second=True)]
    assert [r.reviewer_id for r in visible_rows(A, first, 1)] == [A]  # you see yourself
    assert visible_rows(B, first, 1) == []  # B cannot see A's ratings or decision
    assert hidden_count(B, first, 1) == 1
    both = [*first, row(B, "reject")]
    assert len(visible_rows(A, both, 1)) == 2 and len(visible_rows(B, both, 1)) == 2
    assert len(visible_rows(C, both, 1)) == 2  # the adjudicator sees both


def test_two_reviewer_mode_hides_the_first_review_from_the_second_reviewer() -> None:
    first = [row(A, "approve")]
    assert visible_rows(B, first, 2) == []
    assert [r.reviewer_id for r in visible_rows(A, first, 2)] == [A]


def test_a_single_reviewer_decided_round_is_visible() -> None:
    assert len(visible_rows(B, [row(A, "approve")], 1)) == 1


def test_human_reviewers_records_who_decided_what() -> None:
    at = datetime(2026, 11, 17, tzinfo=UTC)
    out = human_reviewers([ReviewRow(A, "approve", created_at=at), ReviewRow(C, "revise", True, created_at=at)])
    assert out == [
        {"reviewerId": str(A), "decision": "approve", "adjudication": False, "at": at.isoformat()},
        {"reviewerId": str(C), "decision": "revise", "adjudication": True, "at": at.isoformat()},
    ]


# ── publish gates ──


def test_publish_needs_lint_source_and_an_independent_cross_check() -> None:
    assert publish_blockers(Gates(True, True, True, True)) == []
    assert publish_blockers(Gates(False, True, True, True)) == ["lint_errors"]
    assert publish_blockers(Gates(True, False, True, True)) == ["source_check_failed"]
    assert publish_blockers(Gates(True, True, False, True)) == ["model_cross_check_failed"]
    assert publish_blockers(Gates(True, True, True, False)) == ["review_not_independent"]
    assert publish_blockers(Gates(False, False, False, False)) == [
        "lint_errors",
        "source_check_failed",
        "model_cross_check_failed",
        "review_not_independent",
    ]


# ── psychometrics ──


def test_point_biserial_matches_the_textbook_value() -> None:
    # item scores vs rest scores: perfectly separated groups give a high positive correlation
    x = [1, 1, 1, 0, 0, 0]
    y = [0.9, 0.8, 0.7, 0.3, 0.2, 0.1]
    assert point_biserial(x, y) == pytest.approx(0.96490128, abs=1e-6)
    assert point_biserial([1, 1, 1], [0.1, 0.2, 0.3]) is None  # no variance in the item
    assert point_biserial([1, 0], [0.1, 0.2]) is None  # too few
    assert point_biserial(x, [0.5] * 6) is None


def test_rest_score_excludes_the_item_itself() -> None:
    per_user = {"u1": (8, 10), "u2": (3, 10)}
    assert rest_scores(per_user, {"u1": True, "u2": False}) == {"u1": pytest.approx(7 / 9), "u2": pytest.approx(3 / 9)}
    assert rest_scores({"u3": (1, 1)}, {"u3": True}) == {}  # nothing else answered


def synthetic(
    n: int, p_correct: float, *, key: str = "A", wrong: str = "B", seconds: int = 60
) -> tuple[list[FirstAttempt], dict[int, tuple[int, int]]]:
    attempts: list[FirstAttempt] = []
    per_user: dict[int, tuple[int, int]] = {}
    for i in range(n):
        ok = i < round(n * p_correct)
        attempts.append(FirstAttempt(i, key if ok else wrong, ok, seconds))
        # stronger students (low i) do better on the rest of the bank
        per_user[i] = (int(20 * (1 - i / n)) + (1 if ok else 0), 20)
    return attempts, per_user


def test_item_stats_has_the_documented_shape() -> None:
    attempts, per_user = synthetic(40, 0.6)
    s = item_stats(attempts, "A", list("ABCDE"), per_user, report_count=1, now=datetime(2026, 11, 17, tzinfo=UTC))
    assert set(s) == {
        "n",
        "pCorrect",
        "pointBiserial",
        "optionCounts",
        "distractorPb",
        "medianSeconds",
        "reportCount",
        "computedAt",
    }
    assert s["n"] == 40 and s["pCorrect"] == pytest.approx(0.6)
    assert s["optionCounts"] == {"A": 24, "B": 16, "C": 0, "D": 0, "E": 0}
    assert s["pointBiserial"] > 0.5  # strong students got it right
    assert s["distractorPb"]["B"] < 0  # the distractor attracts the weak ones
    assert s["distractorPb"]["C"] is None
    assert s["medianSeconds"] == 60 and s["reportCount"] == 1


RULES = PsychometricRules()


def test_nothing_is_flagged_below_n_30_except_reports() -> None:
    attempts, per_user = synthetic(29, 0.0)  # everybody wrong
    s = item_stats(attempts, "A", list("ABCDE"), per_user)
    assert flags_for(s, "A", RULES, 60) == []
    s["reportCount"] = 2
    assert flags_for(s, "A", RULES, 60) == ["reports"]


def test_flags_at_n_30() -> None:
    attempts, per_user = synthetic(30, 0.1)
    s = item_stats(attempts, "A", list("ABCDE"), per_user)
    assert {"p_too_low", "distractor_over_key"} <= set(flags_for(s, "A", RULES, 60))
    easy, pu = synthetic(40, 1.0)
    f = flags_for(item_stats(easy, "A", list("ABCDE"), pu), "A", RULES, 60)
    assert "p_too_high" in f
    attempts, per_user = synthetic(40, 0.5, seconds=200)
    assert "slow" in flags_for(item_stats(attempts, "A", list("ABCDE"), per_user), "A", RULES, 60)
    assert "slow" not in flags_for(item_stats(attempts, "A", list("ABCDE"), per_user), "A", RULES, None)


def test_a_weak_discrimination_is_flagged() -> None:
    # right and wrong students have the same rest score: point-biserial ~ 0
    attempts = [FirstAttempt(i, "A" if i % 2 else "B", bool(i % 2), 50) for i in range(40)]
    per_user = {i: (10, 20) for i in range(40)}
    s = item_stats(attempts, "A", list("ABCDE"), per_user)
    assert s["pointBiserial"] is not None and s["pointBiserial"] < RULES.pb_min
    assert "point_biserial_low" in flags_for(s, "A", RULES, 50)


def test_action_level_flags_need_n_100_and_may_act_follows() -> None:
    attempts, per_user = synthetic(100, 0.5)
    s = item_stats(attempts, "A", list("ABCDE"), per_user)
    f = flags_for(s, "A", RULES, 60)
    assert "nonfunctioning_distractor" in f  # C, D and E are never chosen
    few, pu = synthetic(60, 0.5)
    assert "nonfunctioning_distractor" not in flags_for(item_stats(few, "A", list("ABCDE"), pu), "A", RULES, 60)
    assert may_act(99, RULES) is False and may_act(100, RULES) is True


def test_a_distractor_with_a_positive_point_biserial_flags_a_second_answer() -> None:
    attempts: list[FirstAttempt] = []
    per_user: dict[int, tuple[int, int]] = {}
    for i in range(120):
        strong = i < 60
        chosen = "A" if i < 40 else ("B" if strong else "C")  # B is picked by strong students: suspicious
        attempts.append(FirstAttempt(i, chosen, chosen == "A", 50))
        per_user[i] = (15 if strong else 5, 20)
    s = item_stats(attempts, "A", list("ABCDE"), per_user)
    assert "distractor_pb_positive" in flags_for(s, "A", RULES, 50)
