"""The author pipeline with a fake store and a fake gateway (no database, no model, no network): ground -> draft ->
lint -> citations -> blind solve -> review queue, the retries, the discards, independence and the night's caps."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from naadi_api.authoring import pipeline
from naadi_api.authoring.checks import CallBudget, CallCapReached, blind_solve, citation_check
from naadi_api.authoring.models import ItemDraft, to_row
from naadi_api.authoring.prompts import Excerpt, part_a, system_prompt
from naadi_api.authoring.selection import Cell, CellError, TaxNode, build_cell
from naadi_api.authoring.store import Gap, RunRecord
from naadi_api.config_api.schemas import AuthoringRules
from naadi_api.llm import CallContext, LLMUnavailable
from naadi_api.llm_budget import BudgetExhausted

from .fixtures import ROOT, TAXONOMY_IDS, good_item, labs, taxonomy_kinds

CONFIG = ROOT / "config"
RULES = AuthoringRules()
AUTHOR = "nvidia/nemotron-test"
SOLVER = "particle-ai/particle-test"
EXCERPT = Excerpt(
    "sec-1#0",
    "naadi-original-v1",
    "Escherichia coli is the commonest cause of uncomplicated cystitis in young women. It ferments lactose and "
    "produces indole. Urinalysis shows leukocyte esterase and nitrite.",
    "sec-1",
)


def taxonomy() -> dict[str, TaxNode]:
    nodes: dict[str, TaxNode] = {}
    for tid, kind in taxonomy_kinds().items():
        exam = (
            ("step1",)
            if tid.startswith("step1")
            else ("step1", "step2ck", "step3")
            if tid.startswith("topic")
            else ("x",)
        )
        nodes[tid] = TaxNode(tid, kind, tid, "step1.sys.respiratory_renal" if kind == "topic" else None, exam)
    return nodes


def draft_json(**over: Any) -> str:
    g = good_item()
    d: dict[str, Any] = {
        "cannot_write": None,
        "vignette": "A 34-year-old woman comes to the clinic because of burning with urination for 2 days. She has no "
        "fever or flank pain. Her temperature is 37.0°C (98.6°F), pulse is 72/min, and blood pressure is 118/74 mm Hg. "
        "Examination of the abdomen shows mild suprapubic tenderness. Urinalysis shows positive leukocyte esterase and "
        "nitrite. Urine culture grows a gram-negative rod that ferments lactose and produces indole.",
        "lead_in": "Which of the following is the most likely causal organism?",
        "options": [{"label": o["label"], "html": o["html"]} for o in g["options"]],
        "answer": "A",
        "explanation": "Escherichia coli is the commonest cause of uncomplicated cystitis in young women. "
        "It ferments lactose and produces indole.",
        "why_not": {
            "B": "Klebsiella is indole negative.",
            "C": "Proteus does not ferment lactose.",
            "D": "Pseudomonas does not ferment lactose.",
            "E": "Staphylococcus is a gram-positive coccus.",
        },
        "educational_objective": "Identify the usual cause of uncomplicated cystitis from culture findings.",
        "difficulty": "medium",
        "citations": [
            {"excerpt_id": "sec-1#0", "claim": "Escherichia coli is the commonest cause of uncomplicated cystitis"}
        ],
        "nutrition": False,
        "demographic_relevance": None,
    }
    d.update(over)
    return json.dumps(d)


class FakeStore:
    def __init__(
        self,
        *,
        excerpts: Sequence[Excerpt] = (EXCERPT,),
        gaps: Sequence[Gap] = (),
        pending: int = 0,
        calls_today: int = 0,
    ):
        self.excerpts_ = list(excerpts)
        self.gaps_ = list(gaps)
        self.pending = pending
        self.calls = calls_today
        self.items: dict[str, tuple[dict[str, Any], str, dict[str, Any]]] = {}
        self.runs: list[RunRecord] = []
        self.sim = 0.2

    async def taxonomy(self) -> dict[str, TaxNode]:
        return taxonomy()

    async def sources(self) -> dict[str, Any]:
        from naadi_api.authoring.lint import SourceInfo

        return {"naadi-original-v1": SourceInfo(True, "proprietary-naadi")}

    async def gaps(
        self, exams: Sequence[str], min_published: int, limit: int, skip_topics: Sequence[str] = ()
    ) -> list[Gap]:
        return [g for g in self.gaps_ if g.topic_id not in skip_topics][:limit]

    async def common_second(self, exam: str, topic_id: str) -> str | None:
        return "step1.disc.microbiology"

    async def failed_topics(self, days: int = 7, times: int = 3) -> list[str]:
        return []

    async def excerpts(self, taxonomy_ids: Sequence[str], text: str, limit: int, chars: int) -> list[Excerpt]:
        return self.excerpts_[:limit]

    async def max_similarity(self, vector_literal: str) -> float:
        return self.sim

    async def insert_item(
        self, question_id: str, row: Mapping[str, Any], status: str, review: Mapping[str, Any]
    ) -> None:
        self.items[question_id] = (dict(row), status, dict(review))

    async def record_run(self, run: RunRecord) -> uuid.UUID:
        self.runs.append(run)
        return run.id

    async def calls_today(self, trigger: str) -> int:
        return self.calls

    async def pending_review_count(self) -> int:
        return self.pending


class Script:
    """A fake gateway: replies by role from a queue; each reply is (text, model, review_not_independent)."""

    def __init__(self, author: list[Any], solver: list[Any]) -> None:
        self.queues = {"author": list(author), "reviewer": list(solver)}
        self.calls: list[str] = []
        self.ctxs: list[CallContext] = []

    async def ainvoke(self, role: str, messages: list[BaseMessage], ctx: CallContext, **kw: Any) -> AIMessage:
        self.calls.append(role)
        self.ctxs.append(ctx)
        item = self.queues[role].pop(0)
        if isinstance(item, Exception):
            raise item
        text, model, flagged = (
            item if isinstance(item, tuple) else (item, AUTHOR if role == "author" else SOLVER, False)
        )
        return AIMessage(content=text, response_metadata={"naadi_model": model, "review_not_independent": flagged})


def solver_ok(choice: str = "A") -> list[str]:
    return [
        json.dumps(
            {"choice": choice, "confidence": 0.8, "defensibility": {"A": 0.9, "B": 0.1, "C": 0.1, "D": 0.05, "E": 0.05}}
        ),
        json.dumps({"answer": "Escherichia coli"}),
        json.dumps({"matches": True}),
    ]


def deps(store: FakeStore, gw: Script, **rules: Any) -> pipeline.Deps:
    n = iter(range(1000))
    return pipeline.Deps(
        store=store,
        gateway=gw,
        rules=AuthoringRules(**rules),
        config_dir=CONFIG,
        labs=labs(),
        new_id=lambda: f"ai-test{next(n)}",
    )


CELL = Cell(
    "step1",
    "step1.sys.respiratory_renal",
    "step1.disc.microbiology",
    "step1.comp.medical_knowledge",
    "topic.renal.stones_urinary_tract",
)


async def author(store: FakeStore, gw: Script, **rules: Any) -> pipeline.ItemOutcome:
    return await pipeline.author_item(
        deps(store, gw, **rules), CELL, "Identify the cause of cystitis.", "medium", trigger="admin"
    )


# ── the happy path ──


async def test_a_clean_draft_lands_in_the_review_queue_with_its_provenance() -> None:
    store, gw = FakeStore(), Script([draft_json()], solver_ok())
    out = await author(store, gw)
    assert out.outcome == "in_review" and out.question_id == "ai-test0"
    row, status, review = store.items["ai-test0"]
    assert status == "in_review"
    assert row["taxonomy_ids"] == TAXONOMY_IDS  # the cell, not whatever the model invented
    assert review["humanExpertReviewed"] is False and review["humanReviewers"] == []
    assert review["authorModel"]["model"] == AUTHOR and review["authorModel"]["excerptIds"] == ["sec-1#0"]
    assert review["solver"]["agrees"] is True and review["solver"]["independent"] is True
    assert review["lint"]["ok"] is True
    run = store.runs[0]
    assert (run.outcome, run.attempts, run.llm_calls, run.question_id) == ("in_review", 1, 4, "ai-test0")
    assert run.excerpt_ids == ["sec-1#0"] and run.author_model == AUTHOR and run.prompt_hash
    assert gw.calls == ["author", "reviewer", "reviewer", "reviewer"]


async def test_the_solver_never_sees_the_key_or_the_explanation() -> None:
    seen: list[str] = []

    class Spy(Script):
        async def ainvoke(self, role: str, messages: list[BaseMessage], ctx: CallContext, **kw: Any) -> AIMessage:
            if role == "reviewer":
                seen.append("\n".join(str(m.content) for m in messages[1:2]))
            return await super().ainvoke(role, messages, ctx, **kw)

    gw = Spy([draft_json()], solver_ok())
    await author(FakeStore(), gw)
    solve_prompt, cover_prompt, judge_prompt = seen
    assert "Escherichia coli is the commonest cause" not in solve_prompt  # no explanation
    assert "Why the others" not in solve_prompt and "Answer:" not in solve_prompt
    assert "A. Escherichia coli" in solve_prompt and "A. Escherichia" not in cover_prompt  # cover: no options at all
    assert "Reference answer: Escherichia coli" in judge_prompt  # only the judge sees the key


async def test_every_call_is_a_job_call_with_its_own_turn() -> None:
    gw = Script([draft_json()], solver_ok())
    await author(FakeStore(), gw)
    assert {c.purpose for c in gw.ctxs} == {"job"}
    assert all(c.user_id is None for c in gw.ctxs)  # no student identity anywhere
    assert len({id(c.turn) for c in gw.ctxs}) == len(gw.ctxs)  # the gateway's 4-calls-per-turn stop never trips here


# ── ground ──


async def test_no_excerpts_means_no_draft_and_no_model_call() -> None:
    store, gw = FakeStore(excerpts=[]), Script([], [])
    out = await author(store, gw)
    assert out.outcome == "no_excerpts" and gw.calls == [] and store.items == {}
    assert store.runs[0].outcome == "no_excerpts"


def test_the_system_prompt_is_part_a_of_the_rules_file() -> None:
    a = part_a(CONFIG)
    assert a.startswith("You write original USMLE-style single-best-answer questions for Naadi.")
    assert "Cover-the-options rule" in a and "Part B" not in a
    assert system_prompt(CONFIG).startswith(a) and "ONE JSON object" in system_prompt(CONFIG)


# ── lint ──


async def test_a_lint_error_regenerates_with_the_reason_then_passes() -> None:
    bad = draft_json(lead_in="Which of the following is NOT a cause?")
    store, gw = FakeStore(), Script([bad, draft_json()], solver_ok())
    prompts: list[str] = []
    orig = gw.ainvoke

    async def spy(role: str, messages: list[BaseMessage], ctx: CallContext, **kw: Any) -> AIMessage:
        if role == "author":
            prompts.append(str(messages[1].content))
        return await orig(role, messages, ctx, **kw)

    gw.ainvoke = spy  # type: ignore[method-assign]
    out = await author(store, gw)
    assert out.outcome == "in_review" and store.runs[0].attempts == 2
    assert '"rule":"L02"' in prompts[1] and "previous draft was rejected" in prompts[1]  # the reason is fed back
    assert "previous draft" not in prompts[0]


async def test_lint_retries_are_bounded_then_the_item_is_dropped() -> None:
    bad = draft_json(lead_in="Which of the following is NOT a cause?")
    store, gw = FakeStore(), Script([bad, bad, bad, bad], [])
    out = await author(store, gw)  # lint_retries = 2: three drafts in all
    assert out.outcome == "discarded_lint" and store.items == {}
    assert gw.calls == ["author"] * 3 and store.runs[0].attempts == 3
    assert any(f["rule"] == "L02" for f in store.runs[0].lint["findings"])


async def test_a_malformed_reply_counts_as_a_failed_attempt() -> None:
    store, gw = FakeStore(), Script(["not json at all", draft_json()], solver_ok())
    out = await author(store, gw)
    assert out.outcome == "in_review" and store.runs[0].attempts == 2


async def test_cannot_write_is_honoured_not_forced() -> None:
    store, gw = FakeStore(), Script([draft_json(cannot_write="The excerpts do not support one best answer.")], [])
    assert (await author(store, gw)).outcome == "discarded_lint" and store.items == {}


async def test_originality_error_blocks_the_item() -> None:
    store = FakeStore()
    store.sim = 0.95
    d = deps(store, Script([draft_json()] * 3, []))

    async def embed(text: str) -> str:
        return "[0.1]"

    d.embed = embed
    out = await pipeline.author_item(d, CELL, "obj", "medium", trigger="admin")
    assert out.outcome == "discarded_lint"
    assert any(f["rule"] == "L16" for f in store.runs[0].lint["findings"])


async def test_a_skipped_originality_check_is_recorded_not_silent() -> None:
    store = FakeStore()
    await author(store, Script([draft_json()], solver_ok()))
    assert store.runs[0].lint["skipped"] == ["L16"]


# ── B04 citations ──


def test_b04_citations_must_name_supplied_excerpts_that_support_the_claim() -> None:
    d = ItemDraft.model_validate_json(draft_json())
    assert citation_check(d, [EXCERPT], max_uncited_ratio=0.34) == []
    none = ItemDraft.model_validate_json(draft_json(citations=[]))
    assert [f.severity for f in citation_check(none, [EXCERPT], max_uncited_ratio=0.34)] == ["error"]
    ghost = ItemDraft.model_validate_json(
        draft_json(citations=[{"excerpt_id": "nope#9", "claim": "Escherichia coli causes cystitis"}])
    )
    f = citation_check(ghost, [EXCERPT], max_uncited_ratio=0.34)
    assert f[0].severity == "error" and f[0].params["unknown_excerpts"] == ["nope#9"]
    unsupported = ItemDraft.model_validate_json(
        draft_json(
            citations=[{"excerpt_id": "sec-1#0", "claim": "Aortic stenosis produces a systolic crescendo murmur"}]
        )
    )
    assert citation_check(unsupported, [EXCERPT], max_uncited_ratio=0.34)[0].params["unsupported_claims"] == ["sec-1#0"]


def test_b04_uncited_explanation_sentences_are_listed_and_bounded() -> None:
    extra = "Aortic stenosis produces a crescendo decrescendo systolic murmur radiating to the carotid arteries."
    some = ItemDraft.model_validate_json(draft_json(explanation=f"{good_item()['explanation_html'][3:-4]} {extra}"))
    f = citation_check(some, [EXCERPT], max_uncited_ratio=0.9)
    assert [(x.severity, x.params["uncited_sentences"]) for x in f] == [("warn", [1])]
    assert citation_check(some, [EXCERPT], max_uncited_ratio=0.2)[0].severity == "error"


async def test_an_uncited_item_is_regenerated() -> None:
    uncited = draft_json(citations=[])
    store, gw = FakeStore(), Script([uncited, draft_json()], solver_ok())
    out = await author(store, gw)
    assert out.outcome == "in_review" and store.runs[0].attempts == 2


# ── blind solve ──


async def test_a_solver_disagreement_goes_back_to_draft_then_is_dropped() -> None:
    store, gw = (
        FakeStore(),
        Script([draft_json(), draft_json()], [json.dumps({"choice": "B", "confidence": 0.6, "defensibility": {}})] * 2),
    )
    out = await author(store, gw)  # solver_retries = 1: two drafts in all
    assert out.outcome == "discarded_solver" and store.items == {}
    assert gw.calls == ["author", "reviewer", "author", "reviewer"]  # no wasted cover-the-options calls
    assert store.runs[0].solver["agrees"] is False
    assert store.runs[0].solver["findings"][0]["rule"] == "B01"


async def test_a_disagreement_is_fed_back_to_the_author() -> None:
    store = FakeStore()
    gw = Script(
        [draft_json(), draft_json()],
        [json.dumps({"choice": "C", "confidence": 0.6, "defensibility": {}}), *solver_ok()],
    )
    prompts: list[str] = []
    orig = gw.ainvoke

    async def spy(role: str, messages: list[BaseMessage], ctx: CallContext, **kw: Any) -> AIMessage:
        if role == "author":
            prompts.append(str(messages[1].content))
        return await orig(role, messages, ctx, **kw)

    gw.ainvoke = spy  # type: ignore[method-assign]
    assert (await author(store, gw)).outcome == "in_review"
    assert '"rule":"B01"' in prompts[1] and '"solver_choice":"C"' in prompts[1]


async def test_b02_and_b03_are_warnings_attached_for_the_reviewers() -> None:
    solver = [
        json.dumps({"choice": "A", "confidence": 0.7, "defensibility": {"A": 0.9, "C": 0.8}}),
        json.dumps({"answer": "Proteus"}),
        json.dumps({"matches": False}),
    ]
    store = FakeStore()
    assert (await author(store, Script([draft_json()], solver))).outcome == "in_review"
    _, status, review = store.items["ai-test0"]
    assert status == "in_review"
    flags = {(f["rule"], f["severity"]) for f in review["flags"]}
    assert {("B02", "warn"), ("B03", "warn")} <= flags
    assert review["solver"]["defensibility"]["C"] == 0.8 and review["solver"]["coverMatches"] is False


async def test_a_solver_of_the_authors_family_blocks_auto_advance() -> None:
    same = [(t, "nvidia/other-model", False) for t in solver_ok()]
    store = FakeStore()
    out = await author(store, Script([draft_json()], same))
    assert out.outcome == "needs_independent_check"
    _, status, review = store.items["ai-test0"]
    assert status == "auto_checked" and review["solver"]["independent"] is False


async def test_review_not_independent_from_the_gateway_blocks_auto_advance() -> None:
    flagged = [(t, SOLVER, True) for t in solver_ok()]
    out = await author(FakeStore(), Script([draft_json()], flagged))
    assert out.outcome == "needs_independent_check"


async def test_an_unparseable_solver_leaves_the_item_unchecked_not_published() -> None:
    store = FakeStore()
    out = await author(store, Script([draft_json()], ["???", "???"]))
    assert out.outcome == "needs_independent_check"
    assert (
        store.items["ai-test0"][1] == "auto_checked" and store.items["ai-test0"][2]["solver"]["error"] == "unparseable"
    )


# ── budget ──


async def test_the_night_stops_at_the_call_cap() -> None:
    gaps = [Gap("step1", "topic.renal.stones_urinary_tract", "UTI", 0, 0)] * 5
    store = FakeStore(gaps=gaps)
    gw = Script([draft_json()] * 5, solver_ok() * 5)
    stats = await pipeline.nightly(deps(store, gw, max_llm_calls_per_night=9))
    assert stats.in_review == 2 and stats.budget_stopped and stats.llm_calls == 9
    # the third draft got its author call but not its solver calls: it is kept for an independent check later
    assert [r.outcome for r in store.runs] == ["in_review", "in_review", "needs_independent_check"]
    assert [s for _, s, _ in store.items.values()] == ["in_review", "in_review", "auto_checked"]


async def test_an_unavailable_solver_keeps_the_linted_draft_for_a_later_check() -> None:
    store = FakeStore()
    out = await author(store, Script([draft_json()], [LLMUnavailable("reviewer", [{"model": "m"}])]))
    assert out.outcome == "needs_independent_check"
    _, status, review = store.items[out.question_id or ""]
    assert (
        status == "auto_checked"
        and review["solver"]["error"] == "unavailable"
        and review["solver"]["independent"] is False
    )


async def test_a_spent_daily_cap_in_the_solver_saves_the_draft_and_stops_the_night() -> None:
    from datetime import UTC, datetime

    gaps = [Gap("step1", "topic.renal.stones_urinary_tract", "UTI", 0, 0)] * 3
    store = FakeStore(gaps=gaps)
    gw = Script([draft_json()], [BudgetExhausted("daily_cap", datetime.now(UTC), purpose="job")])
    stats = await pipeline.nightly(deps(store, gw))
    assert stats.budget_stopped and stats.needs_independent_check == 0  # the run raised before counting: stopped
    assert [r.outcome for r in store.runs] == ["needs_independent_check"] and len(store.items) == 1


async def test_calls_already_spent_today_count_against_the_cap() -> None:
    gaps = [Gap("step1", "topic.renal.stones_urinary_tract", "UTI", 0, 0)]
    stats = await pipeline.nightly(
        deps(FakeStore(gaps=gaps, calls_today=40), Script([], []), max_llm_calls_per_night=40)
    )
    assert stats.skipped == "call_cap"


async def test_nightly_respects_the_switch_and_a_full_queue() -> None:
    gaps = [Gap("step1", "topic.renal.stones_urinary_tract", "UTI", 0, 0)]
    off = await pipeline.nightly(deps(FakeStore(gaps=gaps), Script([], []), nightly_enabled=False))
    assert off.skipped == "disabled"
    full = await pipeline.nightly(deps(FakeStore(gaps=gaps, pending=30), Script([], []), max_pending_review=30))
    assert full.skipped == "queue_full"


async def test_nightly_drafts_at_most_max_items_per_night() -> None:
    gaps = [Gap("step1", "topic.renal.stones_urinary_tract", "UTI", 0, 0)] * 6
    store = FakeStore(gaps=gaps)
    stats = await pipeline.nightly(deps(store, Script([draft_json()] * 3, solver_ok() * 3), max_items_per_night=3))
    assert stats.attempted == 3 and stats.in_review == 3 and len(store.items) == 3


async def test_the_gateways_own_budget_stop_ends_the_night_gracefully() -> None:
    from datetime import UTC, datetime

    gaps = [Gap("step1", "topic.renal.stones_urinary_tract", "UTI", 0, 0)] * 3
    store = FakeStore(gaps=gaps)
    gw = Script([BudgetExhausted("daily_cap", datetime.now(UTC), purpose="job")], [])
    stats = await pipeline.nightly(deps(store, gw))
    assert stats.budget_stopped and store.runs[0].outcome == "budget" and store.items == {}


# ── blind_solve and CallBudget directly ──


async def test_call_budget_is_shared_and_raises_at_the_cap() -> None:
    night = CallBudget(cap=2)
    item = CallBudget(shared=night)
    item.take()
    item.take()
    assert night.used == 2 and night.left() == 0
    with pytest.raises(CallCapReached):
        item.take()


async def test_blind_solve_reports_the_solver_model_and_independence() -> None:
    row = to_row(
        ItemDraft.model_validate_json(draft_json()),
        exam="step1",
        taxonomy_ids=TAXONOMY_IDS,
        source_id="naadi-original-v1",
        excerpt_source_ids=["naadi-original-v1"],
    )
    res = await blind_solve(Script([], solver_ok()), row, CallContext(purpose="job"), CallBudget(), AUTHOR)
    assert (res.agrees, res.model, res.independent, res.calls) == (True, SOLVER, True, 3)
    again = await blind_solve(
        Script([], [(t, "nvidia/x", False) for t in solver_ok()]), row, CallContext(purpose="job"), CallBudget(), AUTHOR
    )
    assert again.independent is False


# ── selection ──


def test_build_cell_validates_against_the_taxonomy() -> None:
    tax = taxonomy()
    cell = build_cell(tax, "step1", "topic.renal.stones_urinary_tract", common_second="step1.disc.microbiology")
    assert cell.taxonomy_ids == TAXONOMY_IDS
    assert cell.as_dict()["discipline"] == "step1.disc.microbiology"
    for kwargs, code in (
        ({"exam": "step3", "topic_id": "topic.renal.stones_urinary_tract"}, "exam_unsupported"),
        ({"exam": "step1", "topic_id": "nope"}, "topic_unknown"),
        (
            {"exam": "step1", "topic_id": "topic.renal.stones_urinary_tract", "second_id": "step1.sys.cardiovascular"},
            "second_unknown",
        ),
        (
            {"exam": "step1", "topic_id": "topic.renal.stones_urinary_tract", "competency_id": "nope"},
            "competency_unknown",
        ),
    ):
        with pytest.raises(CellError) as e:
            build_cell(tax, **kwargs)
        assert e.value.code == code


async def test_the_scripted_fake_gateway_drives_the_whole_pipeline_to_the_review_queue() -> None:
    """`AUTHORING_FAKE` (non-production e2e): a well-formed grounded item and a correct, independent solver."""
    from naadi_api.authoring.fake import FakeAuthoringGateway

    store = FakeStore(excerpts=[EXCERPT])
    out = await pipeline.author_item(
        deps(store, FakeAuthoringGateway()),  # type: ignore[arg-type]
        CELL,
        "Recall the key concept.",
        "medium",
        trigger="admin",
    )
    assert out.outcome == "in_review", store.runs[0].lint
    row, status, review = store.items[out.question_id or ""]
    assert status == "in_review" and review["solver"]["independent"] is True
    assert (
        review["authorModel"]["model"] == "nvidia/fake-author"
        and review["solver"]["model"] == "particle-ai/fake-reviewer"
    )
    assert row["answer"] == "A" and "Escherichia coli is the commonest cause" in row["explanation_html"]
