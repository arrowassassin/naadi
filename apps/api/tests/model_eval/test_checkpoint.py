"""The eval's checkpoint: finished items survive across runs (a daily cap cuts a run short; the next UTC day's run
completes it); unfinished items always run again; any change to what decides a result sets the checkpoint aside."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from model_eval.checkpoint import KEYED_SOURCES, Checkpoint, prompt_finished, run_key
from model_eval.report import McqItem, PromptItem
from model_eval.safety import CheckResult

from ..conftest import REPO_ROOT

EVAL = REPO_ROOT / "tools" / "model_eval"


def _prompt(pid: str, **over: object) -> PromptItem:
    base: dict[str, object] = {
        "id": pid,
        "category": "grounding",
        "answer": "Proximal tubule [ctx:s1]",
        "cited": ["s1"],
        "given_ids": ["s1"],
        "checks": [CheckResult("no_diagnosis", True, "")],
        "scores": {"grounding": {"score": 5, "reason": "cites"}},
        "rubric_mean": 5.0,
        "grader_unsafe": False,
        "grader_unsafe_reason": None,
        "served": {"naadi_model": "tutor-x"},
        "grader_served": {"naadi_model": "rev-y"},
        "grader_calls": 1,
    }
    base.update(over)
    return PromptItem(**base)  # type: ignore[arg-type]


def test_finished_items_round_trip_and_unfinished_ones_are_not_kept(tmp_path: Path) -> None:
    path = tmp_path / "ckpt.json"
    c = Checkpoint(path, "k1")
    assert c.record(McqItem("q1", "A", "A", True, {"naadi_model": "tutor-x"}))
    assert not c.record(McqItem("q2", "B", None, False, error="budget: daily_cap"))
    assert c.record(_prompt("p1"))
    assert not c.record(_prompt("p2", rubric_mean=None, scores=None, grade_error="budget: daily_cap"))
    assert not c.record(_prompt("p3", error="no model in the tutor chain answered"))
    assert not prompt_finished(_prompt("p4", grade_error="grading skipped (--no-grade)"))

    again = Checkpoint.load(path, "k1")
    assert set(again.mcq) == {"q1"} and set(again.prompts) == {"p1"} and len(again.days) == 1
    assert again.mcq_item("q1") == McqItem("q1", "A", "A", True, {"naadi_model": "tutor-x"})
    p1 = again.prompt_item("p1")
    assert p1 is not None and p1 == _prompt("p1") and p1.checks[0] == CheckResult("no_diagnosis", True, "")
    assert again.prompt_item("p2") is None and again.mcq_item("q2") is None
    # Saved atomically: no temp file left behind, and valid JSON on disk.
    assert not list(tmp_path.glob("*.tmp")) and json.loads(path.read_text())["version"] == 1


def test_a_different_key_or_a_broken_file_starts_afresh(tmp_path: Path) -> None:
    path = tmp_path / "ckpt.json"
    c = Checkpoint(path, "k1")
    c.record(McqItem("q1", "A", "A", True))
    other = Checkpoint.load(path, "k2")
    assert other.mcq == {} and other.set_aside and "changed" in other.set_aside
    path.write_text("{not json")
    broken = Checkpoint.load(path, "k1")
    assert broken.mcq == {} and broken.set_aside == "unreadable checkpoint"
    assert Checkpoint.load(tmp_path / "missing.json", "k1").set_aside is None


def test_the_key_covers_prompts_models_answerer_k_heldout_and_the_eval_code(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    for name in KEYED_SOURCES:
        shutil.copy(EVAL / name, src / name)
    prompts = tmp_path / "prompts.yaml"
    models = tmp_path / "models.yaml"
    shutil.copy(EVAL / "prompts.yaml", prompts)
    shutil.copy(REPO_ROOT / "config" / "models.yaml", models)

    def key(**over: object) -> str:
        args: dict[str, object] = {
            "prompts_file": prompts,
            "models_file": models,
            "answerer": "local",
            "k": 6,
            "heldout_ids": ["b", "a"],
            "sources_dir": src,
        }
        args.update(over)
        return run_key(**args)  # type: ignore[arg-type]

    base = key()
    assert key(heldout_ids=["a", "b"]) == base  # order does not matter
    assert key(k=4) != base and key(answerer="tutor_graph") != base and key(heldout_ids=["a"]) != base
    for f in (prompts, models, src / "answerers.py", src / "grader.py", src / "safety.py"):
        before = f.read_bytes()
        f.write_bytes(before + b"\n# changed\n")
        assert key() != base, f.name
        f.write_bytes(before)
    assert key() == base
