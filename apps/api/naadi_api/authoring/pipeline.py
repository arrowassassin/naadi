"""The `author` pipeline: select -> ground -> draft -> lint -> citations -> blind solve -> human-review queue.

Nothing in this module publishes. An item that survives lands in `questions` as `in_review` (or `auto_checked`
when the cross-check could not be made by an independent model family) and waits for a human.

Budget: every model call goes through the gateway (`naadi_api/llm.py`: free chains, rpm, daily cap, job reserve)
and through a `CallBudget` (`authoring.max_llm_calls_per_night` for the cron). One draft attempt costs one author
call plus two to three solver calls; a rejected draft is regenerated with the reasons, up to `lint_retries`
(linter / citation errors) and `solver_retries` (blind-solver disagreement), then dropped. The run is always
recorded in `authoring_runs` (no prompt, no excerpt text: ids and a hash).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from ..config_api.schemas import AuthoringRules
from ..llm import CallContext, LLMUnavailable
from ..llm_budget import BudgetExhausted, TurnCounter
from ..logs import get_logger
from .checks import CallBudget, CallCapReached, Gateway, SolverResult, blind_solve, citation_check
from .jsonio import NoJson, first_object, text_of
from .lint import Finding, LabRef, LintContext, SourceInfo, lint_question
from .models import ItemDraft, to_row
from .prompts import Excerpt, author_message, prompt_hash, system_prompt
from .selection import Cell, CellError, TaxNode, build_cell, labels
from .store import Gap, RunRecord

log = get_logger("naadi.authoring")
ROLE_AUTHOR = "author"
# Authored items are Naadi originals: the excerpts they were grounded on were AI-ingestible by construction.
NAADI_SOURCE = "naadi-original-v1"
DIFFICULTY_CYCLE = ("medium", "hard", "medium")


class Store(Protocol):
    async def taxonomy(self) -> dict[str, TaxNode]: ...
    async def sources(self) -> dict[str, SourceInfo]: ...
    async def gaps(
        self, exams: Sequence[str], min_published: int, limit: int, skip_topics: Sequence[str] = ()
    ) -> list[Gap]: ...
    async def common_second(self, exam: str, topic_id: str) -> str | None: ...
    async def failed_topics(self, days: int = 7, times: int = 3) -> list[str]: ...
    async def excerpts(self, taxonomy_ids: Sequence[str], text: str, limit: int, chars: int) -> list[Excerpt]: ...
    async def max_similarity(self, vector_literal: str) -> float: ...
    async def insert_item(
        self, question_id: str, row: Mapping[str, Any], status: str, review: Mapping[str, Any]
    ) -> None: ...
    async def record_run(self, run: RunRecord) -> uuid.UUID: ...
    async def calls_today(self, trigger: str) -> int: ...
    async def pending_review_count(self) -> int: ...


@dataclass
class Deps:
    store: Store
    gateway: Gateway
    rules: AuthoringRules
    config_dir: Path
    labs: Sequence[LabRef] = ()
    # text -> pgvector literal; None disables the originality check (recorded in the run, never silent)
    embed: Callable[[str], Awaitable[str]] | None = None
    new_id: Callable[[], str] = lambda: f"ai-{uuid.uuid4().hex[:10]}"
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


@dataclass
class ItemOutcome:
    outcome: str
    run_id: uuid.UUID | None = None
    question_id: str | None = None
    calls: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class NightStats:
    skipped: str | None = None
    attempted: int = 0
    in_review: int = 0
    needs_independent_check: int = 0
    discarded: int = 0
    llm_calls: int = 0
    budget_stopped: bool = False
    outcomes: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "skipped": self.skipped,
            "attempted": self.attempted,
            "in_review": self.in_review,
            "needs_independent_check": self.needs_independent_check,
            "discarded": self.discarded,
            "llm_calls": self.llm_calls,
            "budget_stopped": self.budget_stopped,
            "outcomes": self.outcomes,
        }


def _feedback(findings: Sequence[Finding]) -> list[dict[str, Any]]:
    return [{"rule": f.rule, "target": f.target, **f.params} for f in findings if f.severity == "error"]


async def _similarity(deps: Deps, row: Mapping[str, Any]) -> float | None:
    if deps.embed is None:
        return None
    from ..embeddings.sources import question_text

    try:
        return await deps.store.max_similarity(await deps.embed(question_text(row["stem_html"], row["options"])))
    except Exception as exc:  # noqa: BLE001 - the originality check is skipped (and says so), not the item
        log.warning("authoring.similarity_skipped", error=type(exc).__name__)
        return None


async def author_item(
    deps: Deps,
    cell: Cell,
    objective: str,
    difficulty: str,
    *,
    trigger: str,
    started_by: uuid.UUID | None = None,
    budget: CallBudget | None = None,
) -> ItemOutcome:
    """One item for one cell. Returns the outcome; the run row is written whatever happens."""
    budget = budget or CallBudget()
    taxonomy = await deps.store.taxonomy()
    sources = await deps.store.sources()
    rules = deps.rules
    run = RunRecord(trigger, cell.as_dict(), objective, difficulty, started_by)
    out = ItemOutcome("failed", run.id)
    try:
        excerpts = await deps.store.excerpts(
            cell.taxonomy_ids,
            f"{labels(taxonomy, cell)[-1]} {objective}",
            rules.excerpts_per_item,
            rules.excerpt_chars,
        )
        run.excerpt_ids = [e.id for e in excerpts]
        if not excerpts:
            run.outcome = out.outcome = "no_excerpts"
            return out
        excerpt_sources = sorted({e.source_id for e in excerpts})
        system = system_prompt(deps.config_dir)
        feedback: list[dict[str, Any]] = []
        lint_fails = solver_fails = 0
        solver_res: SolverResult | None = None
        row: dict[str, Any] | None = None
        author_model: str | None = None
        while True:
            run.attempts += 1
            user = author_message(
                exam=cell.exam,
                labels=labels(taxonomy, cell),
                objective=objective,
                difficulty=difficulty,
                excerpts=excerpts,
                feedback=feedback or None,
            )
            budget.take()
            ctx = CallContext(purpose="job", user_id=None, run_id=f"{run.id}:{run.attempts}", turn=TurnCounter())
            msg = await deps.gateway.ainvoke(
                ROLE_AUTHOR, [SystemMessage(content=system), HumanMessage(content=user)], ctx
            )
            author_model = str((getattr(msg, "response_metadata", None) or {}).get("naadi_model") or "") or None
            run.author_model = author_model
            run.prompt_hash = prompt_hash(system, user)
            try:
                draft = ItemDraft.model_validate(first_object(text_of(msg.content)))
                if draft.cannot_write:
                    run.lint = {"cannotWrite": True}
                    run.outcome = out.outcome = "discarded_lint"
                    return out
                row = to_row(
                    draft,
                    exam=cell.exam,
                    taxonomy_ids=cell.taxonomy_ids,
                    source_id=NAADI_SOURCE,
                    excerpt_source_ids=excerpt_sources,
                )
            except (NoJson, ValidationError, ValueError):
                lint_fails += 1
                feedback = [{"rule": "format", "why": "reply was not a valid item object"}]
                run.lint = {"ok": False, "findings": [{"rule": "format", "severity": "error"}]}
                if lint_fails > rules.lint_retries:
                    run.outcome = out.outcome = "discarded_lint"
                    return out
                continue
            sim = await _similarity(deps, row)
            report = lint_question(
                row,
                LintContext(
                    taxonomy={k: v.kind for k, v in taxonomy.items()},
                    sources=sources,
                    labs=list(deps.labs) or None,
                    max_similarity=sim,
                    similarity_error=rules.similarity_error,
                    similarity_warn=rules.similarity_warn,
                    requested_difficulty=difficulty,
                ),
            )
            findings = list(report.findings) + citation_check(
                draft, excerpts, max_uncited_ratio=rules.uncited_max_ratio
            )
            run.lint = {
                "ok": not any(f.severity == "error" for f in findings),
                "findings": [f.as_dict() for f in findings],
                "similarity": sim,
                "skipped": ["L16"] if sim is None else [],
            }
            if any(f.severity == "error" for f in findings):
                lint_fails += 1
                feedback = _feedback(findings)
                if lint_fails > rules.lint_retries:
                    run.outcome = out.outcome = "discarded_lint"
                    return out
                continue
            solver_res = await blind_solve(
                deps.gateway, row, CallContext(purpose="job", run_id=str(run.id)), budget, author_model
            )
            run.solver = solver_res.as_dict()
            if solver_res.agrees is False:
                solver_fails += 1
                feedback = [f.as_dict() | {"target": f.target} for f in solver_res.findings if f.severity == "error"]
                if solver_fails > rules.solver_retries:
                    run.outcome = out.outcome = "discarded_solver"
                    return out
                continue
            break
        qid = deps.new_id()
        independent_ok = bool(solver_res and solver_res.agrees and solver_res.independent)
        status = "in_review" if independent_ok else "auto_checked"
        flags = [f.as_dict() for f in findings if f.severity != "error"]
        flags += [f.as_dict() for f in (solver_res.findings if solver_res else []) if f.severity != "error"]
        review = {
            "status": "ai-authored, ai-checked",
            "humanExpertReviewed": False,
            "authorModel": {
                "model": author_model,
                "promptHash": run.prompt_hash,
                "excerptIds": run.excerpt_ids,
                "runId": str(run.id),
                "at": deps.now().isoformat(),
            },
            "flags": flags,
            "lint": run.lint,
            "solver": run.solver,
            "citations": [c.model_dump() for c in draft.citations],
            "round": 1,
            "humanReviewers": [],
        }
        assert row is not None
        await deps.store.insert_item(qid, row, status, review)
        run.question_id = qid
        run.outcome = out.outcome = "in_review" if independent_ok else "needs_independent_check"
        out.question_id = qid
        return out
    except (BudgetExhausted, CallCapReached):
        run.outcome = out.outcome = "budget"
        raise
    except LLMUnavailable:
        run.outcome = out.outcome = "failed"
        return out
    finally:
        run.llm_calls = budget.used
        out.calls = run.llm_calls
        await deps.store.record_run(run)


async def run_cell(
    deps: Deps,
    cell: Cell,
    objective: str,
    difficulty: str,
    *,
    count: int = 1,
    trigger: str = "admin",
    started_by: uuid.UUID | None = None,
    budget: CallBudget | None = None,
) -> list[ItemOutcome]:
    out: list[ItemOutcome] = []
    for _ in range(count):
        try:
            res = await author_item(
                deps,
                cell,
                objective,
                difficulty,
                trigger=trigger,
                started_by=started_by,
                budget=CallBudget(shared=budget),
            )
        except (BudgetExhausted, CallCapReached):
            out.append(ItemOutcome("budget"))
            break
        out.append(res)
    return out


async def nightly(deps: Deps) -> NightStats:
    """The 22:41 UTC cron: fill coverage gaps within the night's caps."""
    rules = deps.rules
    stats = NightStats()
    if not rules.nightly_enabled:
        stats.skipped = "disabled"
        return stats
    if await deps.store.pending_review_count() >= rules.max_pending_review:
        stats.skipped = "queue_full"
        return stats
    used = await deps.store.calls_today("cron")
    night = CallBudget(cap=max(0, rules.max_llm_calls_per_night - used))
    if night.left() == 0:
        stats.skipped = "call_cap"
        return stats
    taxonomy = await deps.store.taxonomy()
    gaps = await deps.store.gaps(
        rules.target_exams,
        rules.min_published_per_topic,
        rules.max_items_per_night,
        await deps.store.failed_topics(),
    )
    for i, gap in enumerate(gaps):
        try:
            cell = build_cell(
                taxonomy, gap.exam, gap.topic_id, common_second=await deps.store.common_second(gap.exam, gap.topic_id)
            )
        except CellError:
            continue
        objective = f"Apply the key concept of {gap.label} to a clinical or experimental scenario."
        stats.attempted += 1
        try:
            res = await author_item(
                deps,
                cell,
                objective,
                DIFFICULTY_CYCLE[i % len(DIFFICULTY_CYCLE)],
                trigger="cron",
                budget=CallBudget(shared=night),
            )
        except (BudgetExhausted, CallCapReached):
            stats.budget_stopped = True
            break
        stats.outcomes[res.outcome] = stats.outcomes.get(res.outcome, 0) + 1
        if res.outcome == "in_review":
            stats.in_review += 1
        elif res.outcome == "needs_independent_check":
            stats.needs_independent_check += 1
        else:
            stats.discarded += 1
    stats.llm_calls = night.used
    return stats
