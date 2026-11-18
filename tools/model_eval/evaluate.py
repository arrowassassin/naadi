"""Runs the eval: held-out MCQ and tutor prompts through an answerer, safety checks, reviewer grading.

Each MCQ and each prompt is one turn (its own `TurnCounter`, purpose `job`): an MCQ makes one tutor call, a prompt
makes one tutor call + one reviewer call (+ one retry on invalid JSON), all under `max_llm_calls_per_turn`.
When every provider's rpm bucket is empty the gateway raises `BudgetExhausted("rate_limited")`; the item waits for
the bucket and tries again (no provider request was made, so nothing is double-counted). A daily cap, the turn
cap or a chain with no answering model ends that item with an error, which counts against the bar.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from naadi_api.llm import CallContext, LLMGateway, LLMUnavailable
from naadi_api.llm_budget import BudgetExhausted, TurnLimitReached
from naadi_api.llm_privacy import PrivacyContext

from .answerers import Answerer
from .grader import grade_answer
from .heldout import Question, parse_letter
from .prompts import EvalPrompt, MustNotEcho
from .ratewait import with_rate_wait
from .report import McqItem, PromptItem
from .safety import cited_ids, run_checks

REDACTED = "[redacted]"


@dataclass
class Progress:
    total: int
    done: int = 0
    printer: Callable[[str], None] | None = None

    def step(self, label: str) -> None:
        self.done += 1
        if self.printer is not None:
            self.printer(f"[{self.done}/{self.total}] {label}")


def describe_error(exc: BaseException) -> str:
    if isinstance(exc, TurnLimitReached):
        return f"turn cap reached ({exc.limit} calls)"
    if isinstance(exc, BudgetExhausted):
        return f"budget: {exc.reason} (resets {exc.reset_at.isoformat()})"
    if isinstance(exc, LLMUnavailable):
        tried = ", ".join(
            f"{a.get('provider')}/{a.get('model')}: {a.get('error')}" for a in exc.attempts if a.get("provider")
        )
        return f"no model in the {exc.role} chain answered ({tried or exc.attempts[0].get('error')})"
    return type(exc).__name__


def redact(text: str, prompt: EvalPrompt) -> str:
    """Remove every value the prompt marks as the student's PII before an answer is stored or printed."""
    values: list[str] = [*prompt.spec.privacy.names, *prompt.spec.privacy.emails, *prompt.spec.privacy.phones]
    for c in prompt.spec.safety_checks:
        if isinstance(c, MustNotEcho):
            values.extend(c.values)
    for v in sorted({v for v in values if v}, key=len, reverse=True):
        flexible = r"[\s\-./]*".join(re.escape(ch) for ch in re.sub(r"[\s\-./]", "", v))
        text = re.sub(flexible, REDACTED, text, flags=re.I)
    return text


def privacy_for(prompt: EvalPrompt) -> PrivacyContext:
    p = prompt.spec.privacy
    return PrivacyContext(names=p.names, emails=p.emails, phones=p.phones)


async def eval_mcq(gateway: LLMGateway, answerer: Answerer, q: Question) -> McqItem:
    ctx = CallContext(purpose="job", run_id=f"model_eval:mcq:{q.id}")
    try:
        ans = await with_rate_wait(lambda: answerer.answer_mcq(q, ctx))
    except (BudgetExhausted, LLMUnavailable) as exc:
        return McqItem(q.id, q.answer, None, False, error=describe_error(exc))
    except Exception as exc:  # noqa: BLE001 - one broken item is reported, the run goes on
        return McqItem(q.id, q.answer, None, False, error=describe_error(exc))
    chosen = parse_letter(ans.text, q.labels)
    return McqItem(q.id, q.answer, chosen, chosen == q.answer, ans.served, None, ans.text.strip()[-300:])


async def eval_prompt(gateway: LLMGateway, answerer: Answerer, prompt: EvalPrompt, *, grade: bool = True) -> PromptItem:
    ctx = CallContext(purpose="job", run_id=f"model_eval:prompt:{prompt.id}", privacy=privacy_for(prompt))
    try:
        ans = await with_rate_wait(lambda: answerer.answer_prompt(prompt, ctx))
    except Exception as exc:  # noqa: BLE001 - reported as an errored prompt (fails the bar)
        return PromptItem(
            prompt.id, prompt.category, "", [], None, [], None, None, False, None, error=describe_error(exc)
        )
    checks = run_checks(prompt, ans.text, ans.given_ids)
    item = PromptItem(
        id=prompt.id,
        category=prompt.category,
        answer=redact(ans.text, prompt),
        cited=cited_ids(ans.text),
        given_ids=ans.given_ids,
        checks=checks,
        scores=None,
        rubric_mean=None,
        grader_unsafe=False,
        grader_unsafe_reason=None,
        served=ans.served,
    )
    if not grade:
        item.grade_error = "grading skipped (--no-grade)"
        return item
    ctx.author_model = ans.served.get("naadi_model")  # llm.py marks review_not_independent on a family match
    try:
        res = await grade_answer(gateway, prompt, ans.text, ans.passages, ctx)
    except Exception as exc:  # noqa: BLE001 - ungraded: the run is incomplete and misses the bar
        item.grade_error = describe_error(exc)
        return item
    item.grader_calls = res.calls
    item.grader_served = res.served
    if res.grade is None:
        item.grade_error = res.error
        return item
    item.scores = {k: v.model_dump() for k, v in res.grade.scores.items()}
    item.rubric_mean = round(res.grade.mean(), 3)
    item.grader_unsafe = res.grade.unsafe
    item.grader_unsafe_reason = redact(res.grade.unsafe_reason or "", prompt) or None
    return item


async def run_items(
    gateway: LLMGateway,
    answerer: Answerer,
    questions: Sequence[Question],
    prompts: Sequence[EvalPrompt],
    *,
    concurrency: int = 4,
    printer: Callable[[str], None] | None = None,
    grade: bool = True,
    on_item: Callable[[McqItem | PromptItem], object] | None = None,
) -> tuple[list[McqItem], list[PromptItem]]:
    sem = asyncio.Semaphore(max(1, concurrency))
    progress = Progress(len(questions) + len(prompts), printer=printer)

    async def one_mcq(q: Question) -> McqItem:
        async with sem:
            item = await eval_mcq(gateway, answerer, q)
        if on_item is not None:
            on_item(item)
        progress.step(f"mcq {q.id}: {'ok' if item.correct else ('error' if item.error else 'wrong')}")
        return item

    async def one_prompt(p: EvalPrompt) -> PromptItem:
        async with sem:
            item = await eval_prompt(gateway, answerer, p, grade=grade)
        if on_item is not None:
            on_item(item)
        state = "error" if item.error else ("unsafe" if item.safety_failed else "safe")
        progress.step(f"prompt {p.id}: {state}, rubric {item.rubric_mean if item.rubric_mean is not None else '-'}")
        return item

    # Prompts start round-robin across categories, so if a daily cap cuts grading short the graded share is
    # spread over every category; results come back in file order.
    order = interleave_by_category(prompts)
    done = dict(zip([p.id for p in order], await asyncio.gather(*(one_prompt(p) for p in order)), strict=True))
    prompt_items = [done[p.id] for p in prompts]
    mcq_items = await asyncio.gather(*(one_mcq(q) for q in questions))
    return list(mcq_items), prompt_items


def interleave_by_category(prompts: Sequence[EvalPrompt]) -> list[EvalPrompt]:
    queues: dict[str, list[EvalPrompt]] = {}
    for p in prompts:
        queues.setdefault(p.category, []).append(p)
    out: list[EvalPrompt] = []
    while any(queues.values()):
        for q in queues.values():
            if q:
                out.append(q.pop(0))
    return out


__all__ = [
    "describe_error",
    "eval_mcq",
    "eval_prompt",
    "privacy_for",
    "redact",
    "run_items",
]
