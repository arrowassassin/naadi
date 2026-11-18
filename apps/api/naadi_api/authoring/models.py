"""Shapes of the authoring pipeline: the model's draft, the review ratings and the API's payloads.

The draft is written by the `author` role as JSON and validated here; it is turned into the `questions` row shape
(`to_row`) that the linter reads. Nothing here carries prose for the UI: every outcome is a code the clients word
with `ui_copy`.
"""

from __future__ import annotations

import html
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Label = Literal["A", "B", "C", "D", "E"]
LABELS: tuple[Label, ...] = ("A", "B", "C", "D", "E")
Difficulty = Literal["easy", "medium", "hard"]
Decision = Literal["approve", "revise", "reject"]
QuestionStatus = Literal["draft", "auto_checked", "in_review", "published", "retired"]

# Part C ratings (config/authoring_rules.md): every field is required for an approval.
Accuracy = Literal["correct", "minor_issue", "incorrect"]
CognitiveLevel = Literal["recall", "application", "analysis"]
# The item-writing flaws a reviewer can tick: the Part A checklist.
Flaw = Literal[
    "not_homogeneous",
    "key_too_long",
    "grammatical_cue",
    "logical_cue",
    "word_repeat",
    "convergence",
    "absolute_terms",
    "negative_lead_in",
    "cannot_cover_options",
    "irrelevant_difficulty",
    "hinged",
    "stereotyped",
    "other",
]
FLAWS: tuple[str, ...] = Flaw.__args__  # type: ignore[attr-defined]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DraftOption(_Strict):
    label: Label
    html: str = Field(min_length=1, max_length=400)


class DraftCitation(_Strict):
    excerpt_id: str = Field(min_length=1, max_length=120)
    claim: str = Field(min_length=3, max_length=400)


class ItemDraft(_Strict):
    """What the author role returns (Part A "Required output fields"). `taxonomy_ids` is decided by the cell the
    run was asked for, not by the model."""

    cannot_write: str | None = Field(default=None, max_length=300)
    vignette: str = Field(default="", max_length=3000)
    lead_in: str = Field(default="", max_length=300)
    options: list[DraftOption] = Field(default_factory=list, max_length=5)
    answer: Label | None = None
    explanation: str = Field(default="", max_length=3000)
    why_not: dict[Label, str] = Field(default_factory=dict)
    educational_objective: str = Field(default="", max_length=300)
    difficulty: Difficulty = "medium"
    citations: list[DraftCitation] = Field(default_factory=list, max_length=30)
    nutrition: bool = False
    demographic_relevance: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _complete(self) -> ItemDraft:
        if self.cannot_write:
            return self
        if not (self.vignette.strip() and self.lead_in.strip() and self.answer and len(self.options) == 5):
            raise ValueError("an item needs a vignette, a lead-in, five options and a key")
        return self


def _p(text: str) -> str:
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    return "".join(f"<p>{html.escape(p, quote=False)}</p>" for p in paras)


def to_row(
    draft: ItemDraft, *, exam: str, taxonomy_ids: list[str], source_id: str, excerpt_source_ids: list[str]
) -> dict[str, Any]:
    """The `questions` row shape (snake_case) the linter and the store use."""
    assert draft.answer is not None
    opts = sorted(draft.options, key=lambda o: o.label)
    key = next((o for o in opts if o.label == draft.answer), None)
    wrong = "".join(
        f" {label} — {html.escape(draft.why_not[label].strip(), quote=False)}"
        for label in LABELS
        if label != draft.answer and label in draft.why_not
    )
    return {
        "exam": exam,
        "stem_html": _p(draft.vignette) + _p(draft.lead_in),
        "options": [{"label": o.label, "html": html.escape(o.html.strip(), quote=False)} for o in opts],
        "answer": draft.answer,
        "answer_html": f"Answer: {draft.answer}. {html.escape(key.html.strip(), quote=False)}" if key else None,
        "explanation_html": _p(draft.explanation),
        "why_not_html": f"Why the others are wrong:{wrong}",
        "educational_objective": draft.educational_objective.strip(),
        "difficulty": draft.difficulty,
        "taxonomy_ids": taxonomy_ids,
        "source_id": source_id,
        "excerpt_source_ids": excerpt_source_ids,
        "demographic_relevance": draft.demographic_relevance,
        "nutrition": draft.nutrition,
    }


# ── review ──


class ReviewRatings(_Strict):
    """Part C: accuracy, single best answer, cognitive level, flaws, difficulty, taxonomy."""

    accuracy: Accuracy
    single_best_answer: bool
    cognitive_level: CognitiveLevel
    flaws: list[Flaw] = Field(default_factory=list, max_length=len(FLAWS))
    difficulty: Difficulty
    taxonomy_ok: bool

    @field_validator("flaws")
    @classmethod
    def _unique(cls, v: list[Flaw]) -> list[Flaw]:
        return list(dict.fromkeys(v))


class QuestionReviewIn(_Strict):
    """A reviewer's submission. `ratings` is required for every decision: a revise or a reject says why in the
    same fields the approval uses."""

    decision: Decision
    ratings: ReviewRatings
    note: Annotated[str | None, Field(max_length=2000)] = None
    request_second: bool = False
