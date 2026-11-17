#!/usr/bin/env python3
"""Tutor-model evaluation harness (segment 15, HANDOFF §6.1).

Usage (from the repo root):
    uv --directory apps/api run python ../../tools/model_eval/run.py [--answerer local|tutor_graph]
        [--limit-mcq N] [--limit-prompts N] [--dry-run] [--concurrency N] [--allow-incomplete] [--no-doc]

What it measures:
- 100 held-out seed MCQ (`heldout.py`: lowest sha256(id) in data/questions.json), excluded from retrieval, graded by
  exact match of the chosen letter;
- 30 Naadi-original tutor prompts (`prompts.yaml`) graded by the `reviewer` role against their rubrics, plus
  machine safety checks.
Bar: MCQ ≥ 75 %, rubric mean ≥ 4/5, zero safety failures, complete run. Exit 0 = bar met, 1 = bar missed,
2 = the eval could not start (answerer missing, models not cached, no provider configured, budget too small).

Every model call goes through `naadi_api.llm.LLMGateway` (purpose `job`, free chains from config/models.yaml, rpm
buckets, daily caps, ≤ 4 calls per turn, PII scrubbing). Writes `tools/model_eval/results/<UTC date>-<sha>.json`
(not committed) and appends a summary to docs/MODEL_TESTS.md (not on --dry-run). Never prints keys, prompts'
PII or the student messages.

`--dry-run` swaps the provider clients and the embedder for offline fakes (`fakes.py`): no network, no model
files, nothing about any real model is measured.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from model_eval.answerers import Answerer, AnswererUnavailable, LocalAnswerer, TutorGraphAnswerer  # noqa: E402
from model_eval.evaluate import run_items  # noqa: E402
from model_eval.fakes import DryRunClients, VirtualClock  # noqa: E402
from model_eval.gateway import MemoryUsageLedger, build_gateway, day_caps, job_caps  # noqa: E402
from model_eval.heldout import HELDOUT_SIZE, load_questions, pick_heldout  # noqa: E402
from model_eval.prompts import EXPECTED_PROMPTS, EvalPrompt, load_prompts  # noqa: E402
from model_eval.ratewait import use_sleep  # noqa: E402
from model_eval.report import build_report, compute_bar, render_markdown  # noqa: E402
from model_eval.retrieval import Embedder, HashingEmbedder, HeldoutFilter, Index, build_corpus, load_sections  # noqa: E402
from naadi_api.embeddings.engine import Engine, ModelsNotCached, spec_from_config  # noqa: E402
from naadi_api.llm import set_gateway  # noqa: E402
from naadi_api.llm_config import RuntimeConfig, set_runtime_config  # noqa: E402
from naadi_api.logs import configure_logging, register_secrets  # noqa: E402
from naadi_api.settings import Settings  # noqa: E402

REPO_ROOT = TOOLS_DIR.parent
HERE = Path(__file__).resolve().parent
HELDOUT_RULE = f"lowest {HELDOUT_SIZE} by sha256(id)"


class SetupError(RuntimeError):
    """The eval cannot start; exit code 2."""


def git_sha(root: Path = REPO_ROOT) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"],  # noqa: S607 - git from PATH, as in every dev tool here
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "nogit"
    return out.stdout.strip() or "nogit"


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Naadi tutor-model evaluation (tools/model_eval)")
    ap.add_argument("--answerer", choices=("local", "tutor_graph"), default="local")
    ap.add_argument("--limit-mcq", type=int, default=None, help="run only the first N held-out MCQ")
    ap.add_argument("--limit-prompts", type=int, default=None, help="run only the first N tutor prompts")
    ap.add_argument("--dry-run", action="store_true", help="offline fake models and embedder (no network)")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--k", type=int, default=6, help="retrieved passages per question")
    ap.add_argument("--data", type=Path, default=REPO_ROOT / "data")
    ap.add_argument("--prompts", type=Path, default=HERE / "prompts.yaml")
    ap.add_argument("--out", type=Path, default=None, help="JSON report path (default results/<date>-<sha>.json)")
    ap.add_argument("--doc", type=Path, default=REPO_ROOT / "docs" / "MODEL_TESTS.md")
    ap.add_argument("--no-doc", action="store_true", help="do not append the summary to docs/MODEL_TESTS.md")
    ap.add_argument("--summary-out", type=Path, default=None, help="also write the markdown summary to this file")
    ap.add_argument(
        "--no-grade",
        action="store_true",
        help="answer and machine-check the prompts without reviewer calls (prompt iteration; misses the bar)",
    )
    ap.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="start even when the job budget cannot cover every call (the run then misses the bar)",
    )
    ap.add_argument(
        "--usage-file",
        type=Path,
        default=REPO_ROOT / ".tmp" / "model_eval_usage.json",
        help="today's call counts, shared by runs on one UTC day (counts only; ignored by --dry-run)",
    )
    ap.add_argument("--quiet", action="store_true")
    return ap.parse_args(argv)


def _embedder(dry_run: bool, settings: Settings) -> Embedder:
    if dry_run:
        return HashingEmbedder()
    # A batch job, not the Space's request path: use every core for the one-off index build.
    engine = Engine(spec_from_config(settings.config_dir), threads=max(1, os.cpu_count() or 1))
    try:
        engine.warm()
    except ModelsNotCached as exc:
        raise SetupError(f"{exc} (in apps/api: uv run python -m naadi_api.embeddings prefetch)") from exc
    return engine


def _preflight(
    cfg: Any,
    settings: Settings,
    clients: Any,
    ledger: MemoryUsageLedger,
    n_mcq: int,
    n_prompts: int,
    allow_incomplete: bool,
    grade: bool = True,
) -> list[str]:
    """Refuse to spend the budget on a run that cannot finish. Returns notes for the report."""
    key = (lambda p: clients.key_for(p) is not None) if clients is not None else None

    def configured(p: str) -> bool:
        if key is not None:
            return bool(key(p))
        return (settings.nvidia_api_key if p == "nvidia" else settings.openrouter_api_key) is not None

    used = ledger.counts()
    caps = {p: max(0, c - used.get((p, "job"), 0)) for p, c in job_caps(cfg, settings).items()}
    notes: list[str] = []
    problems: list[str] = []
    for role, need in (("tutor", n_mcq + n_prompts), ("reviewer", n_prompts if grade else 0)):
        if need == 0:
            continue
        providers = [e.provider for e in cfg.models.roles[role].chain]
        usable = sorted({p for p in providers if configured(p) and caps.get(p, 0) > 0})
        cap = sum(caps[p] for p in usable)
        notes.append(
            f"{role} chain providers configured: {usable or 'none'}; purpose-job calls left today {cap} "
            f"(needs at least {need} calls)"
        )
        if not usable and role == "tutor":
            raise SetupError("no provider of the tutor chain is configured with a non-zero job cap: nothing to run")
        if not usable:
            problems.append(f"no provider of the {role} chain is configured with a non-zero job cap")
        elif cap < need:
            problems.append(f"the {role} role needs ≥ {need} job calls but only {cap} are left today")
    if problems and not allow_incomplete:
        raise SetupError("; ".join(problems) + " (see the budget in config/models.yaml and OPENROUTER_DAILY_CAP)")
    notes.extend(f"budget: {p}" for p in problems)
    return notes


async def run(args: argparse.Namespace) -> int:
    out = print if not args.quiet else (lambda *_a, **_k: None)
    if args.dry_run:
        # Offline: no .env, no keys; the account-wide OPENROUTER_DAILY_CAP of this machine does not apply to fakes,
        # so the budget is the one in config/models.yaml alone.
        base = Settings(_env_file=None)
        cap = RuntimeConfig(None, base.config_dir).yaml_sections()["budget"]["daily_request_cap"]
        settings = base.model_copy(
            update={"openrouter_api_key": None, "nvidia_api_key": None, "openrouter_daily_cap": int(cap)}
        )
    else:
        settings = Settings()
    register_secrets(settings.secret_values())
    clients = DryRunClients() if args.dry_run else None

    questions = load_questions(args.data / "questions.json")
    heldout = pick_heldout(questions)
    by_id = {q.id: q for q in questions}
    sections_raw = load_sections(args.data / "chapters.json")
    sections = {sid: (heading, text) for _, sid, heading, text in sections_raw}
    prompts: list[EvalPrompt] = load_prompts(args.prompts, by_id, sections, [q.id for q in heldout])
    if len(prompts) != EXPECTED_PROMPTS:
        raise SetupError(f"prompts.yaml has {len(prompts)} prompts; the eval is defined with {EXPECTED_PROMPTS}")
    hidden = HeldoutFilter(t for q in heldout for t in q.hidden_texts())
    for p in prompts:
        texts = [p.page.text] if p.page else []
        texts += [c.text for c in p.spec.context]
        if any(hidden.overlaps(t) for t in texts):
            raise SetupError(f"{p.id}: its page or context overlaps a held-out question")
    mcq_set = heldout[: args.limit_mcq] if args.limit_mcq is not None else heldout
    prompt_set = prompts[: args.limit_prompts] if args.limit_prompts is not None else prompts

    if args.dry_run:
        vclock = VirtualClock()
        eg = build_gateway(settings, clients=clients, clock=vclock, sleep=vclock.sleep)
        use_sleep(vclock.sleep)
    else:
        eg = build_gateway(settings, ledger=MemoryUsageLedger.load(args.usage_file))
        use_sleep(None)
    cfg = await eg.runtime.get()
    notes = _preflight(
        cfg,
        settings,
        clients,
        eg.ledger,
        len(mcq_set),
        len(prompt_set),
        args.allow_incomplete,
        grade=not args.no_grade,
    )
    if args.no_grade:
        notes.append("grading skipped (--no-grade): answers and machine safety checks only; cannot meet the bar")

    chunks, dropped = build_corpus(sections_raw, hidden)
    out(
        f"index: {len(chunks)} chunks (dropped: {dropped['heldout_overlap']} overlapping a held-out question, "
        f"{dropped['answer_key']} answer-key chunks)"
    )
    answerer: Answerer
    if args.answerer == "tutor_graph":
        try:
            answerer = TutorGraphAnswerer.load()
        except AnswererUnavailable as exc:
            raise SetupError(str(exc)) from exc
        set_runtime_config(eg.runtime)
        set_gateway(eg.gateway)  # the graph reads the gateway through get_gateway()
        notes.append("tutor_graph answerer: the graph's own retrieval and call context; citations not validated")
    else:
        embedder = _embedder(args.dry_run, settings)
        if isinstance(embedder, Engine):
            cache = REPO_ROOT / ".tmp" / "model_eval_vectors.npz"
            index = Index(chunks, embedder, cache=cache, model_id=embedder.spec.model)
        else:
            index = Index(chunks, embedder)
        answerer = LocalAnswerer(eg.gateway, index, k=args.k)

    started = datetime.now(UTC)
    try:
        mcq_items, prompt_items = await run_items(
            eg.gateway,
            answerer,
            mcq_set,
            prompt_set,
            concurrency=args.concurrency,
            printer=None if args.quiet else out,
            grade=not args.no_grade,
        )
    finally:
        if not args.dry_run:
            eg.ledger.save(args.usage_file)
    finished = datetime.now(UTC)
    bar = compute_bar(mcq_items, prompt_items, expected_mcq=HELDOUT_SIZE, expected_prompts=EXPECTED_PROMPTS)
    if args.limit_mcq is not None or args.limit_prompts is not None:
        notes.append(f"limited run: {len(mcq_set)} MCQ, {len(prompt_set)} prompts (cannot meet the bar)")
    caps_today = day_caps(cfg, settings)
    meta = {
        "date": started.strftime("%Y-%m-%d"),
        "started": started.isoformat(timespec="seconds"),
        "finished": finished.isoformat(timespec="seconds"),
        "git_sha": git_sha(),
        "answerer": answerer.name,
        "dry_run": bool(args.dry_run),
        "heldout_rule": HELDOUT_RULE,
        "heldout_ids": [q.id for q in heldout],
        "k": args.k,
        "index": {
            "chunks": len(chunks),
            "dropped_heldout_overlap": dropped["heldout_overlap"],
            "dropped_answer_key": dropped["answer_key"],
        },
        "caps": {"day": caps_today, "job": job_caps(cfg, settings)},
        "config_source": dict(cfg.source),
        "notes": [
            *notes,
            "usage ledger: in memory with today's counts carried in --usage-file (llm_usage is not read); "
            "the gateway enforces the same caps",
        ],
    }
    report = build_report(meta=meta, mcq=mcq_items, prompts=prompt_items, bar=bar, usage=eg.ledger.summary())
    suffix = "-dryrun" if args.dry_run else ""
    path = args.out or HERE / "results" / f"{meta['date']}-{meta['git_sha']}{suffix}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md = render_markdown(report)
    if not args.dry_run and not args.no_doc:
        with args.doc.open("a", encoding="utf-8") as fh:
            fh.write("\n" + md)
    if args.summary_out is not None:
        args.summary_out.parent.mkdir(parents=True, exist_ok=True)
        args.summary_out.write_text(md, encoding="utf-8")
    out("")
    out(md)
    out(f"report: {path}")
    if args.dry_run:
        # A dry run checks the plumbing, not a model: it succeeds when every item ran without an error.
        errored = [m.id for m in mcq_items if m.error] + [p.id for p in prompt_items if p.error or p.grade_error]
        return 1 if errored else 0
    return 0 if bar.passed else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging("WARNING", stream=sys.stderr)
    try:
        return asyncio.run(run(args))
    except SetupError as exc:
        print(f"model_eval: cannot start: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
