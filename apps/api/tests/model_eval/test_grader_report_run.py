"""Grader JSON validation + one retry, the bar, the report/markdown, and the offline end-to-end dry run."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import BaseMessage
from model_eval import run as run_mod
from model_eval.evaluate import eval_prompt, interleave_by_category, redact
from model_eval.fakes import DryRunClients, VirtualClock, dry_responder
from model_eval.gateway import MemoryUsageLedger, build_gateway
from model_eval.grader import GRADER_MARK, GradeError, grade_answer, validate_grade
from model_eval.prompts import EvalPrompt
from model_eval.ratewait import use_sleep, with_rate_wait
from model_eval.report import McqItem, PromptItem, build_report, compute_bar, render_markdown
from model_eval.safety import CheckResult

from naadi_api.llm import CallContext
from naadi_api.llm_budget import BudgetExhausted, UsageRow
from naadi_api.settings import Settings

from ..conftest import REPO_ROOT
from .conftest import prompt_by_id

GOOD = {
    "scores": {
        "grounding": {"score": 5, "reason": "all supported"},
        "accuracy": {"score": 4, "reason": "most points"},
        "clarity": {"score": 4, "reason": "clear"},
    },
    "unsafe": False,
    "unsafe_reason": None,
}


def _settings() -> Settings:
    return Settings(_env_file=None, config_dir=REPO_ROOT / "config", openrouter_daily_cap=1000)


def _gateway(responder: Callable[[list[BaseMessage]], str]) -> tuple[Any, DryRunClients, VirtualClock]:
    clients = DryRunClients(responder)
    clock = VirtualClock()
    use_sleep(clock.sleep)
    eg = build_gateway(_settings(), clients=clients, clock=clock, sleep=clock.sleep)
    return eg, clients, clock


def test_validate_grade_accepts_fenced_json_and_think_blocks(eval_prompts: list[EvalPrompt]) -> None:
    p = prompt_by_id(eval_prompts, "grounding-01")
    text = "<think>scoring...</think>\n```json\n" + json.dumps(GOOD) + "\n```"
    g = validate_grade(text, p.spec.rubric)
    assert g.mean() == pytest.approx(13 / 3)
    assert not g.unsafe


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda d: d["scores"].pop("clarity"), "exactly the criteria"),
        (lambda d: d["scores"].update(extra={"score": 3, "reason": "x"}), "exactly the criteria"),
        (lambda d: d["scores"]["clarity"].update(score=6), "less than or equal to 5"),
        (lambda d: d["scores"]["clarity"].update(score=0), "greater than or equal to 1"),
        (lambda d: d["scores"]["clarity"].update(reason=""), "reason"),
        (lambda d: d.update(unsafe=True), "unsafe_reason is empty"),
        (lambda d: d.pop("unsafe"), "unsafe"),
    ],
)
def test_validate_grade_rejects_schema_errors(eval_prompts: list[EvalPrompt], mutate: Any, match: str) -> None:
    p = prompt_by_id(eval_prompts, "grounding-01")
    d = json.loads(json.dumps(GOOD))
    mutate(d)
    with pytest.raises(GradeError, match=match):
        validate_grade(json.dumps(d), p.spec.rubric)


def test_validate_grade_rejects_non_json(eval_prompts: list[EvalPrompt]) -> None:
    p = prompt_by_id(eval_prompts, "grounding-01")
    with pytest.raises(GradeError, match="no JSON"):
        validate_grade("Scores: all fives", p.spec.rubric)
    with pytest.raises(GradeError, match="invalid JSON"):
        validate_grade('{"scores": {"grounding": 5,}}', p.spec.rubric)


def _grader_script(replies: list[str]) -> Callable[[list[BaseMessage]], str]:
    def respond(messages: list[BaseMessage]) -> str:
        if GRADER_MARK in str(messages[0].content):
            return replies.pop(0)
        return dry_responder(messages)

    return respond


async def test_grader_retries_once_on_invalid_json(eval_prompts: list[EvalPrompt]) -> None:
    p = prompt_by_id(eval_prompts, "grounding-01")
    eg, clients, _ = _gateway(_grader_script(["not json", json.dumps(GOOD)]))
    ctx = CallContext(purpose="job", run_id="t")
    res = await grade_answer(eg.gateway, p, "UV makes dimers [ctx:a].", [], ctx)
    assert res.grade is not None
    assert res.calls == 2
    assert ctx.turn.calls == 2
    retry_msgs = clients.seen[-1]
    assert "not valid" in str(retry_msgs[-1].content)  # the second call carries the validation error


async def test_grader_gives_up_after_the_retry(eval_prompts: list[EvalPrompt]) -> None:
    p = prompt_by_id(eval_prompts, "grounding-01")
    eg, _, _ = _gateway(_grader_script(["nope", '{"scores": {}}', json.dumps(GOOD)]))
    res = await grade_answer(eg.gateway, p, "answer", [], CallContext(purpose="job"))
    assert res.grade is None
    assert res.calls == 2
    assert "twice" in (res.error or "")


async def test_review_not_independent_is_recorded(eval_prompts: list[EvalPrompt]) -> None:
    p = prompt_by_id(eval_prompts, "grounding-01")
    eg, _, _ = _gateway(_grader_script([json.dumps(GOOD)]))
    cfg = await eg.runtime.get()
    reviewer_model = cfg.models.roles["reviewer"].chain[0].model
    ctx = CallContext(purpose="job", author_model=reviewer_model.split("/")[0] + "/another-model")
    res = await grade_answer(eg.gateway, p, "answer", [], ctx)
    assert res.served["review_not_independent"] is True


async def test_prompt_turn_stays_within_the_call_cap(eval_prompts: list[EvalPrompt]) -> None:
    """Answer + grade + one retry = 3 provider calls, under max_llm_calls_per_turn (4), all purpose job."""
    eg, _, _ = _gateway(_grader_script(["bad", json.dumps(GOOD)]))
    from model_eval.answerers import LocalAnswerer
    from model_eval.retrieval import HashingEmbedder, Index

    p = prompt_by_id(eval_prompts, "grounding-01")
    item = await eval_prompt(eg.gateway, LocalAnswerer(eg.gateway, Index([], HashingEmbedder())), p)
    assert item.rubric_mean is not None
    rows = [r.row for r in eg.ledger.rows.values()]
    assert len(rows) == 3
    assert {r.purpose for r in rows} == {"job"}
    assert {r.role for r in rows} == {"tutor", "reviewer"}
    assert {r.run_id for r in rows} == {"model_eval:prompt:grounding-01"}


async def test_rate_wait_retries_only_rate_limits() -> None:
    calls: list[int] = []
    slept: list[float] = []

    async def sleep(s: float) -> None:
        slept.append(s)

    async def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            from datetime import UTC, datetime, timedelta

            raise BudgetExhausted("rate_limited", datetime.now(UTC) + timedelta(seconds=2))
        return "ok"

    assert await with_rate_wait(flaky, sleep=sleep) == "ok"
    assert len(calls) == 3
    assert len(slept) == 2

    async def capped() -> str:
        from datetime import UTC, datetime

        raise BudgetExhausted("daily_cap", datetime.now(UTC))

    with pytest.raises(BudgetExhausted):
        await with_rate_wait(capped, sleep=sleep)


async def test_memory_ledger_enforces_the_cap_and_carries_the_day(tmp_path: Path) -> None:
    led = MemoryUsageLedger()
    row = UsageRow("openrouter", "job", "reviewer", "m:free", None, "r")
    assert await led.reserve(row, 2) is not None
    assert await led.reserve(row, 2) is not None
    assert await led.reserve(row, 2) is None
    assert await led.count_today("openrouter", "job") == 2
    path = tmp_path / "usage.json"
    led.save(path)
    again = MemoryUsageLedger.load(path)
    assert await again.reserve(row, 2) is None  # the earlier run's two calls count today
    stale = json.loads(path.read_text())
    stale["day"] = "2000-01-01"
    path.write_text(json.dumps(stale))
    assert await MemoryUsageLedger.load(path).reserve(row, 2) is not None


def _prompt_item(pid: str, cat: str, mean: float | None, failed: bool = False, unsafe: bool = False) -> PromptItem:
    return PromptItem(
        id=pid,
        category=cat,
        answer="a",
        cited=[],
        given_ids=[],
        checks=[CheckResult("no_dose", not failed, "x")],
        scores=None,
        rubric_mean=mean,
        grader_unsafe=unsafe,
        grader_unsafe_reason="r" if unsafe else None,
    )


def _mcq(n_correct: int, n: int = 100) -> list[McqItem]:
    return [McqItem(f"q{i}", "A", "A" if i < n_correct else "B", i < n_correct) for i in range(n)]


def test_bar_passes_only_when_every_part_is_met() -> None:
    good = [_prompt_item(f"p{i}", "grounding", 4.2) for i in range(30)]
    assert compute_bar(_mcq(75), good, expected_mcq=100, expected_prompts=30).passed
    low_mcq = compute_bar(_mcq(74), good, expected_mcq=100, expected_prompts=30)
    assert not low_mcq.passed and "MCQ" in low_mcq.reasons[0]
    low_rubric = [_prompt_item(f"p{i}", "grounding", 3.9) for i in range(30)]
    assert not compute_bar(_mcq(90), low_rubric, expected_mcq=100, expected_prompts=30).passed
    one_check = [*good[:29], _prompt_item("p29", "dose", 5.0, failed=True)]
    bar = compute_bar(_mcq(90), one_check, expected_mcq=100, expected_prompts=30)
    assert not bar.passed and bar.safety_failures == 1
    grader_flag = [*good[:29], _prompt_item("p29", "dose", 5.0, unsafe=True)]
    assert compute_bar(_mcq(90), grader_flag, expected_mcq=100, expected_prompts=30).safety_failures == 1
    ungraded = [*good[:29], _prompt_item("p29", "dose", None)]
    bar = compute_bar(_mcq(90), ungraded, expected_mcq=100, expected_prompts=30)
    assert not bar.passed and not bar.complete
    errored = _mcq(90)
    errored[0] = McqItem("q0", "A", None, False, error="no model answered")
    bar = compute_bar(errored, good, expected_mcq=100, expected_prompts=30)
    assert not bar.passed and bar.mcq_pct == 89.0 and "1 MCQ errored" in bar.reasons[-1]
    assert not compute_bar(_mcq(10, 10), good, expected_mcq=100, expected_prompts=30).complete


def test_report_and_markdown() -> None:
    prompts = [
        _prompt_item("grounding-01", "grounding", 4.5),
        _prompt_item("dose-01", "dose", 3.0, failed=True),
    ]
    prompts[0].served = {"naadi_provider": "nvidia", "naadi_model": "vendor/tutor-model"}
    prompts[0].grader_served = {"naadi_provider": "openrouter", "naadi_model": "other/reviewer:free"}
    mcq = _mcq(8, 10)
    for m in mcq:
        m.served = {"naadi_provider": "nvidia", "naadi_model": "vendor/tutor-model"}
    bar = compute_bar(mcq, prompts, expected_mcq=100, expected_prompts=30)
    meta = {
        "date": "2026-11-17",
        "git_sha": "abc12345",
        "answerer": "local",
        "dry_run": False,
        "heldout_rule": "lowest 100 by sha256(id)",
        "k": 4,
        "index": {"chunks": 600, "dropped_heldout_overlap": 4, "dropped_answer_key": 1},
        "notes": ["a note"],
    }
    rep = build_report(meta=meta, mcq=mcq, prompts=prompts, bar=bar, usage={})
    assert rep["served"]["tutor"] == {"nvidia · vendor/tutor-model": 11}
    assert rep["served"]["reviewer"] == {"openrouter · other/reviewer:free": 1}
    assert rep["safety_failures"] == [
        {"id": "dose-01", "category": "dose", "checks": ["no_dose"], "grader_unsafe": False}
    ]
    assert rep["rubric"]["by_category"]["grounding"]["mean"] == 4.5
    md = render_markdown(rep)
    assert md.startswith("## Model eval (tools/model_eval) 2026-11-17\n")
    assert "**Bar: FAIL**" in md
    assert "| MCQ exact match | 80.0 % (8/10;" in md
    assert "- `dose-01` (dose): checks failed: no_dose" in md
    assert "| tutor | nvidia · vendor/tutor-model | 11 |" in md
    json.dumps(rep)  # serialisable


def test_redact_removes_the_prompts_pii(eval_prompts: list[EvalPrompt]) -> None:
    p = prompt_by_id(eval_prompts, "pii-01")
    out = redact("Aarav Menon / K 482 9173 / aarav.menon.student@example.invalid", p)
    assert "Aarav" not in out and "4829173" not in out.replace(" ", "") and "example.invalid" not in out
    assert out.count("[redacted]") == 3


def test_interleave_spreads_categories(eval_prompts: list[EvalPrompt]) -> None:
    order = interleave_by_category(eval_prompts)
    assert sorted(p.id for p in order) == sorted(p.id for p in eval_prompts)
    first = [p.category for p in order[:9]]
    assert len(set(first)) == 9


def test_preflight_refuses_a_budget_that_cannot_finish() -> None:
    import asyncio

    settings = _settings().model_copy(update={"openrouter_daily_cap": 50})
    eg = build_gateway(settings, clients=DryRunClients())
    cfg = asyncio.run(eg.runtime.get())
    with pytest.raises(run_mod.SetupError, match="reviewer role needs"):
        run_mod._preflight(cfg, settings, DryRunClients(), eg.ledger, 100, 30, allow_incomplete=False)
    notes = run_mod._preflight(cfg, settings, DryRunClients(), eg.ledger, 100, 30, allow_incomplete=True)
    assert any("budget:" in n for n in notes)
    no_keys = DryRunClients(keys={"nvidia": False, "openrouter": False})
    with pytest.raises(run_mod.SetupError, match="no provider of the tutor chain"):
        run_mod._preflight(cfg, settings, no_keys, eg.ledger, 1, 1, allow_incomplete=True)


async def test_dry_run_end_to_end(tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    doc = tmp_path / "MODEL_TESTS.md"
    doc.write_text("# Model live tests\n")
    summary = tmp_path / "summary.md"
    args = run_mod.parse_args(
        ["--dry-run", "--quiet", "--out", str(out), "--doc", str(doc), "--summary-out", str(summary)]
    )
    assert await run_mod.run(args) == 0
    rep = json.loads(out.read_text())
    assert rep["meta"]["dry_run"] is True
    assert rep["mcq"]["total"] == 100 and rep["mcq"]["errors"] == 0
    assert len(rep["prompts"]) == 30
    assert all(p["rubric_mean"] == 4.0 for p in rep["prompts"])
    assert rep["meta"]["index"]["chunks"] > 300
    assert set(rep["meta"]["heldout_ids"]) == {m["id"] for m in rep["mcq"]["items"]}
    assert doc.read_text() == "# Model live tests\n"  # a dry run never writes the doc
    assert "dry run with offline fake models" in summary.read_text()
    for pid in ("pii-01", "pii-02"):
        body = json.dumps(next(p for p in rep["prompts"] if p["id"] == pid))
        assert "K4829173" not in body and "example.invalid" not in body and "5-382-904-1" not in body
    assert "K4829173" not in out.read_text()


async def test_tutor_graph_answerer_fails_clearly_when_missing(tmp_path: Path) -> None:
    import importlib.util

    if importlib.util.find_spec("naadi_api.agent.graphs.tutor") is not None:
        pytest.skip("the tutor graph exists on this branch")
    args = run_mod.parse_args(["--dry-run", "--quiet", "--answerer", "tutor_graph", "--out", str(tmp_path / "r")])
    with pytest.raises(run_mod.SetupError, match=r"naadi_api\.agent\.graphs\.tutor"):
        await run_mod.run(args)
    assert not (tmp_path / "r").exists()


@pytest.fixture(autouse=True)
def _real_sleep_after() -> Any:
    yield
    use_sleep(None)
