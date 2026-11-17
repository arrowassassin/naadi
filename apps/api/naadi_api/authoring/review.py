"""The human-review state machine (config/authoring_rules.md Part C, with the lead's amendment).

Pure functions over the reviews of one round; the store applies the outcome.

- Lead decision: the owner is the only user, so ONE admin approval publishes (`required_approvals` = 1) once the
  linter, the source check and the model cross-check have all passed (`publish_blockers`). A second reviewer is
  supported but optional: `required_approvals` = 2 asks for two on every item, and any reviewer may tick "ask for a
  second review" on one item.
- With two reviewers they work blind: neither sees the other's ratings until both have submitted. Both approve ->
  approved; both the same other decision -> that decision; any difference -> adjudication by a third, different
  admin whose decision is final.
- One admin never fills two slots of a round, and never adjudicates an item they reviewed.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .models import Decision, Ratings

State = Literal["awaiting", "adjudication", "approved", "revise", "rejected"]
DECIDED: frozenset[str] = frozenset({"approved", "revise", "rejected"})
_OF_DECISION: dict[str, State] = {"approve": "approved", "revise": "revise", "reject": "rejected"}


@dataclass(frozen=True)
class ReviewRow:
    reviewer_id: uuid.UUID | None
    decision: Decision
    adjudication: bool = False
    request_second: bool = False
    ratings: dict[str, Any] | None = None
    created_at: Any = None


@dataclass(frozen=True)
class Resolution:
    state: State
    needed: int  # reviews of the first stage (1 or 2)
    submitted: int  # reviews of the first stage so far
    final: Decision | None = None

    @property
    def decided(self) -> bool:
        return self.state in DECIDED


def needed_reviews(required_approvals: int, rows: Sequence[ReviewRow]) -> int:
    first = [r for r in rows if not r.adjudication]
    return 2 if required_approvals >= 2 or any(r.request_second for r in first) else 1


def resolve(rows: Sequence[ReviewRow], required_approvals: int = 1) -> Resolution:
    """Where the round stands. `rows` are the round's reviews in submission order."""
    first = [r for r in rows if not r.adjudication]
    adjudicators = [r for r in rows if r.adjudication]
    needed = needed_reviews(required_approvals, rows)
    if adjudicators:
        d = adjudicators[0].decision
        return Resolution(_OF_DECISION[d], needed, len(first), d)
    if len(first) < needed:
        return Resolution("awaiting", needed, len(first))
    stage = first[:needed]
    decisions = {r.decision for r in stage}
    if len(decisions) == 1:
        d = stage[0].decision
        return Resolution(_OF_DECISION[d], needed, len(first), d)
    return Resolution("adjudication", needed, len(first))


class ReviewRefused(ValueError):
    """A submission the state machine does not accept. `code` is a stable machine code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def check_submission(
    reviewer_id: uuid.UUID, rows: Sequence[ReviewRow], required_approvals: int, ratings: Ratings, decision: Decision
) -> bool:
    """Raise `ReviewRefused` for a submission that may not be made; return whether it is an adjudication."""
    res = resolve(rows, required_approvals)
    if res.decided:
        raise ReviewRefused("round_closed")
    if any(r.reviewer_id == reviewer_id for r in rows):
        raise ReviewRefused("already_reviewed")  # one admin never fills two slots, nor adjudicates their own item
    if decision == "approve" and (ratings.accuracy == "incorrect" or not ratings.single_best_answer):
        raise ReviewRefused("approve_contradicts_ratings")
    if decision == "approve" and not ratings.taxonomy_ok:
        raise ReviewRefused("approve_contradicts_ratings")
    return res.state == "adjudication"


def visible_rows(viewer_id: uuid.UUID, rows: Sequence[ReviewRow], required_approvals: int) -> list[ReviewRow]:
    """The reviews `viewer_id` may see: their own always; the others' only once the first stage is complete (both
    reviewers of a two-reviewer item have submitted) or the round is decided. A single reviewer has nobody to be
    blind to."""
    res = resolve(rows, required_approvals)
    if res.decided or res.state == "adjudication":
        return list(rows)  # both reviewers have submitted (or the round is closed): nothing is hidden
    return [r for r in rows if r.reviewer_id == viewer_id]


def hidden_count(viewer_id: uuid.UUID, rows: Sequence[ReviewRow], required_approvals: int) -> int:
    return len(rows) - len(visible_rows(viewer_id, rows, required_approvals))


@dataclass(frozen=True)
class Gates:
    """The three automated checks that must pass before a human approval can publish."""

    lint_ok: bool
    source_ok: bool
    cross_check_ok: bool
    independent: bool


def publish_blockers(g: Gates) -> list[str]:
    """Machine codes of every gate that is not passed; empty = publishable."""
    out: list[str] = []
    if not g.lint_ok:
        out.append("lint_errors")
    if not g.source_ok:
        out.append("source_check_failed")
    if not g.cross_check_ok:
        out.append("model_cross_check_failed")
    if not g.independent:
        out.append("review_not_independent")
    return out


def human_reviewers(rows: Sequence[ReviewRow]) -> list[dict[str, Any]]:
    """`review.humanReviewers[]`: reviewer id, decision and date. Ratings stay in `question_reviews`."""
    return [
        {
            "reviewerId": str(r.reviewer_id) if r.reviewer_id else None,
            "decision": r.decision,
            "adjudication": r.adjudication,
            "at": r.created_at.isoformat() if hasattr(r.created_at, "isoformat") else r.created_at,
        }
        for r in rows
    ]
