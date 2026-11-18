"""The checks that run after the linter (config/authoring_rules.md Part B, B01-B04).

B04 (citations) is deterministic: every citation must name a supplied excerpt that lexically supports its claim,
and the share of explanation sentences no excerpt supports is bounded. B01-B03 are the blind solver: a model of a
DIFFERENT family from the author (the `reviewer` role; llm.py flags it when the chain falls back to the author's
family), at temperature 0, that never sees the key or the explanation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..llm import CallContext
from ..llm_budget import TurnCounter
from ..llm_config import model_family
from .jsonio import NoJson, first_object, text_of
from .lint import Finding, content_tokens, flat, plain
from .models import LABELS, ItemDraft
from .prompts import SOLVER_SYSTEM, Excerpt, cover_message, judge_message, solve_message

ROLE_SOLVER = "reviewer"
SUPPORT_CLAIM = 0.5  # share of a claim's content words that must appear in its excerpt
SUPPORT_SENTENCE = 0.5  # share of a sentence's content words found in the cited excerpts
DEFENSIBLE = 0.5  # B03: a distractor above this may be a second best answer
_SENT = re.compile(r"(?<=[.?!])\s+")


def _coverage(words: set[str], pool: set[str]) -> float:
    return len(words & pool) / len(words) if words else 1.0


def citation_check(draft: ItemDraft, excerpts: Sequence[Excerpt], *, max_uncited_ratio: float) -> list[Finding]:
    """B04. ERROR: no citations, a citation to an unknown excerpt, a claim its excerpt does not support, or too many
    explanation sentences supported by nothing. WARN: a few unsupported sentences (listed)."""
    by_id = {e.id: e for e in excerpts}
    out: list[Finding] = []
    if not draft.citations:
        return [Finding("B04", "error", "citations", {"why": "no_citations"})]
    unknown = sorted({c.excerpt_id for c in draft.citations if c.excerpt_id not in by_id})
    weak = [
        c.excerpt_id
        for c in draft.citations
        if c.excerpt_id in by_id
        and _coverage(content_tokens(c.claim), content_tokens(by_id[c.excerpt_id].text)) < SUPPORT_CLAIM
    ]
    if unknown or weak:
        out.append(Finding("B04", "error", "citations", {"unknown_excerpts": unknown, "unsupported_claims": weak}))
    cited_pool: set[str] = set()
    for c in draft.citations:
        if c.excerpt_id in by_id:
            cited_pool |= content_tokens(by_id[c.excerpt_id].text)
    sentences = [s for s in _SENT.split(flat(draft.explanation)) if len(content_tokens(s)) >= 4]
    uncited = [i for i, s in enumerate(sentences) if _coverage(content_tokens(s), cited_pool) < SUPPORT_SENTENCE]
    if uncited:
        ratio = len(uncited) / len(sentences)
        out.append(
            Finding(
                "B04",
                "error" if ratio > max_uncited_ratio else "warn",
                "explanation_html",
                {"uncited_sentences": uncited, "of": len(sentences)},
            )
        )
    return out


class Gateway(Protocol):
    async def ainvoke(self, role: str, messages: list[BaseMessage], ctx: CallContext, **kwargs: Any) -> Any: ...


class CallBudget:
    """Model calls spent by one run (and, via `shared`, by a night's runs). `take()` raises when the cap is hit."""

    def __init__(self, cap: int | None = None, shared: CallBudget | None = None) -> None:
        self.cap = cap
        self.used = 0
        self.shared = shared

    def left(self) -> int | None:
        own = None if self.cap is None else self.cap - self.used
        theirs = self.shared.left() if self.shared else None
        vals = [v for v in (own, theirs) if v is not None]
        return min(vals) if vals else None

    def take(self) -> None:
        left = self.left()
        if left is not None and left <= 0:
            raise CallCapReached
        self.used += 1
        if self.shared:
            self.shared.used += 1


class CallCapReached(RuntimeError):
    """The night's model-call cap is spent (`authoring.max_llm_calls_per_night`)."""


class _Solve(BaseModel):
    model_config = ConfigDict(extra="ignore")
    choice: str
    confidence: float = Field(default=0.5, ge=0, le=1)
    defensibility: dict[str, float] = Field(default_factory=dict)


class _Cover(BaseModel):
    model_config = ConfigDict(extra="ignore")
    answer: str = Field(min_length=1, max_length=400)


class _Judge(BaseModel):
    model_config = ConfigDict(extra="ignore")
    matches: bool


@dataclass
class SolverResult:
    """`solver` of the run and of `questions.review`. `agrees` is B01; `independent` is the family check."""

    agrees: bool | None = None  # None = the solver could not answer
    choice: str | None = None
    confidence: float | None = None
    defensibility: dict[str, float] = field(default_factory=dict)
    cover_matches: bool | None = None
    model: str | None = None
    independent: bool = False
    findings: list[Finding] = field(default_factory=list)
    calls: int = 0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "agrees": self.agrees,
            "choice": self.choice,
            "confidence": self.confidence,
            "defensibility": self.defensibility,
            "coverMatches": self.cover_matches,
            "model": self.model,
            "independent": self.independent,
            "findings": [f.as_dict() for f in self.findings],
            "calls": self.calls,
            "error": self.error,
        }


def _meta(msg: Any) -> Mapping[str, Any]:
    return getattr(msg, "response_metadata", None) or {}


async def _ask(
    gateway: Gateway, user: str, ctx: CallContext, budget: CallBudget, model_cls: type[BaseModel], result: SolverResult
) -> tuple[Any, Mapping[str, Any]]:
    """One solver call, validated; one retry on a malformed reply (each attempt takes a call from the budget)."""
    last: Exception | None = None
    for _ in range(2):
        ctx = replace(ctx, turn=TurnCounter())  # a pipeline step is its own turn, not a tool loop
        budget.take()
        result.calls += 1
        msg = await gateway.ainvoke(
            ROLE_SOLVER, [SystemMessage(content=SOLVER_SYSTEM), HumanMessage(content=user)], ctx
        )
        try:
            return model_cls.model_validate(first_object(text_of(msg.content))), _meta(msg)
        except (NoJson, ValidationError) as exc:
            last = exc
    raise ValueError("unparseable solver reply") from last


def _option_texts(row: Mapping[str, Any]) -> list[tuple[str, str]]:
    return [(str(o["label"]), flat(str(o["html"]))) for o in row["options"]]


async def blind_solve(
    gateway: Gateway, row: Mapping[str, Any], ctx: CallContext, budget: CallBudget, author_model: str | None
) -> SolverResult:
    """B01-B03. The solver sees the stem and options only (never the key or the explanation)."""
    res = SolverResult()
    stem = plain(row["stem_html"])
    options = _option_texts(row)
    key = str(row["answer"])
    ctx.author_model = author_model
    try:
        solved, meta = await _ask(gateway, solve_message(stem, options), ctx, budget, _Solve, res)
    except ValueError:
        res.error = "unparseable"
        return res
    res.model = str(meta.get("naadi_model") or "") or None
    res.independent = not meta.get("review_not_independent") and bool(
        res.model and author_model and model_family(res.model) != model_family(author_model)
    )
    res.choice = solved.choice.strip().upper()[:1]
    res.confidence = solved.confidence
    res.defensibility = {k.upper(): float(v) for k, v in solved.defensibility.items() if k.upper() in LABELS}
    res.agrees = res.choice == key
    if not res.agrees:
        res.findings.append(Finding("B01", "error", "answer", {"key": key, "solver_choice": res.choice}))
        return res
    second = sorted(lab for lab, v in res.defensibility.items() if lab != key and v > DEFENSIBLE)
    if second:
        res.findings.append(Finding("B03", "warn", "options", {"options": second}))
    lead_in = stem.split("\n")[-1]
    try:
        cover, _ = await _ask(gateway, cover_message(stem), ctx, budget, _Cover, res)
        key_text = next(t for lab, t in options if lab == key)
        judged, _ = await _ask(gateway, judge_message(lead_in, cover.answer, key_text), ctx, budget, _Judge, res)
        res.cover_matches = judged.matches
        if not judged.matches:
            res.findings.append(Finding("B02", "warn", "stem_html", {}))
    except ValueError:
        res.findings.append(Finding("B02", "info", "stem_html", {"skipped": "unparseable"}))
    return res
