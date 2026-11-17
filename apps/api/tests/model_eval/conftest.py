"""tools/model_eval is a package under tools/ (run as a script from apps/api); the tests import it from there."""

from __future__ import annotations

import sys

import pytest

from ..conftest import REPO_ROOT

TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from model_eval.heldout import Question, load_questions, pick_heldout  # noqa: E402
from model_eval.prompts import EvalPrompt, load_prompts  # noqa: E402
from model_eval.retrieval import load_sections  # noqa: E402

DATA = REPO_ROOT / "data"
PROMPTS = TOOLS / "model_eval" / "prompts.yaml"


@pytest.fixture(scope="session")
def questions() -> list[Question]:
    return load_questions(DATA / "questions.json")


@pytest.fixture(scope="session")
def heldout(questions: list[Question]) -> list[Question]:
    return pick_heldout(questions)


@pytest.fixture(scope="session")
def sections() -> list[tuple[str, str, str, str]]:
    return load_sections(DATA / "chapters.json")


@pytest.fixture(scope="session")
def eval_prompts(
    questions: list[Question], heldout: list[Question], sections: list[tuple[str, str, str, str]]
) -> list[EvalPrompt]:
    return load_prompts(
        PROMPTS,
        {q.id: q for q in questions},
        {sid: (h, t) for _, sid, h, t in sections},
        [q.id for q in heldout],
    )


def prompt_by_id(prompts: list[EvalPrompt], pid: str) -> EvalPrompt:
    return next(p for p in prompts if p.id == pid)


__all__ = ["DATA", "PROMPTS", "TOOLS", "prompt_by_id"]
