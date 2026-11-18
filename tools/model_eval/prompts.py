"""Schema and loader for `prompts.yaml`: the 30 Naadi-original tutor prompts, their rubrics and safety checks.

A prompt's context comes from the retrieval index (`retrieval: index`), from passages written into the prompt
(`retrieval: given`) or both (`both`: e.g. an injected passage next to real retrieved ones). A page context names
a seed question (shown as unattempted: stem and options only) or a chapter section; it is resolved from `data/`
at load time, so no course text is copied into the YAML. The correct letter of a page question is kept for the
leak check and never sent to any model.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .heldout import Question

CATEGORIES = (
    "grounding",
    "citation",
    "unknown",
    "dose",
    "invented_drug",
    "own_symptoms",
    "answer_leak",
    "pii",
    "injection",
)
Category = Literal[
    "grounding", "citation", "unknown", "dose", "invented_drug", "own_symptoms", "answer_leak", "pii", "injection"
]
EXPECTED_PROMPTS = 30
_SLUG = r"^[a-z][a-z0-9_]*$"
CTX_ID = r"^[A-Za-z0-9][A-Za-z0-9_.#:\-]{0,79}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Criterion(_Strict):
    name: str = Field(pattern=_SLUG, max_length=40)
    descriptors: dict[int, str]
    expect: tuple[str, ...] = ()  # points the grader checks for (accuracy criteria)

    @field_validator("descriptors")
    @classmethod
    def _one_to_five(cls, v: dict[int, str]) -> dict[int, str]:
        if sorted(v) != [1, 2, 3, 4, 5] or any(not s.strip() for s in v.values()):
            raise ValueError("descriptors need a non-empty text for each score 1, 2, 3, 4 and 5")
        return v


class Passage(_Strict):
    id: str = Field(pattern=CTX_ID)
    text: str = Field(min_length=20, max_length=4000)
    title: str | None = None


class PageRef(_Strict):
    kind: Literal["question", "section"]
    ref: str = Field(min_length=3, max_length=40)


class Privacy(_Strict):
    """What the student's profile would hold; passed as the call's PrivacyContext (scrubbed by llm.py)."""

    names: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()


class MustNotContain(_Strict):
    type: Literal["must_not_contain"]
    label: str = Field(min_length=2, max_length=60)
    pattern: str

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, v: str) -> str:
        re.compile(v)
        return v


class MustContainAny(_Strict):
    type: Literal["must_contain_any"]
    label: str = Field(min_length=2, max_length=60)
    patterns: tuple[str, ...] = Field(min_length=1)

    @field_validator("patterns")
    @classmethod
    def _compiles(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for p in v:
            re.compile(p)
        return v


class MustNotEcho(_Strict):
    """Literal values (an ID, an email) that must not appear, also with spaces/dashes/dots removed."""

    type: Literal["must_not_echo"]
    label: str = Field(min_length=2, max_length=60)
    values: tuple[str, ...] = Field(min_length=1)


class Builtin(_Strict):
    """Checks whose patterns live in `safety.py` (shared by many prompts)."""

    type: Literal["no_dose", "says_unknown", "refers_to_clinician", "no_answer_leak", "no_system_prompt_leak"]


SafetyCheck = Annotated[MustNotContain | MustContainAny | MustNotEcho | Builtin, Field(discriminator="type")]


class PromptSpec(_Strict):
    id: str = Field(pattern=r"^[a-z]+-\d{2}$")
    category: Category
    message: str = Field(min_length=5, max_length=2000)
    retrieval: Literal["index", "given", "both"] = "index"
    context: tuple[Passage, ...] = ()
    page_context: PageRef | None = None
    privacy: Privacy = Privacy()
    rubric: tuple[Criterion, ...] = Field(min_length=1, max_length=6)
    safety_checks: tuple[SafetyCheck, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> PromptSpec:
        if self.retrieval in ("given", "both") and not self.context:
            raise ValueError(f"{self.id}: retrieval {self.retrieval!r} needs `context` passages")
        if self.retrieval == "index" and self.context:
            raise ValueError(f"{self.id}: `context` is only used with retrieval given|both")
        names = [c.name for c in self.rubric]
        if len(set(names)) != len(names):
            raise ValueError(f"{self.id}: duplicate rubric criterion names")
        leak = any(isinstance(c, Builtin) and c.type == "no_answer_leak" for c in self.safety_checks)
        if leak and (self.page_context is None or self.page_context.kind != "question"):
            raise ValueError(f"{self.id}: no_answer_leak needs a question page_context")
        return self


class PromptFile(_Strict):
    version: Literal[1]
    criteria: dict[str, Any] = Field(default_factory=dict)  # YAML anchors only
    prompts: tuple[PromptSpec, ...]


@dataclass(frozen=True)
class PageContext:
    kind: Literal["question", "section"]
    ref: str
    text: str  # what the answerer sees
    hidden_answer: str | None = None  # question pages: the correct letter, for the leak check only
    hidden_option_text: str | None = None
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalPrompt:
    spec: PromptSpec
    page: PageContext | None

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def category(self) -> str:
        return self.spec.category


def render_question_page(q: Question) -> str:
    opts = "\n".join(f"{label}. {text}" for label, text in q.options)
    return f"Page: Qbank question {q.id}. The student has NOT answered it yet.\nStem: {q.stem}\nOptions:\n{opts}"


def render_section_page(section_id: str, heading: str, text: str, limit: int = 1500) -> str:
    body = text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " ..."
    return f"Page: chapter section {section_id} ({heading}).\nVisible text: {body}"


def load_prompts(
    path: Path,
    questions: Mapping[str, Question],
    sections: Mapping[str, tuple[str, str]],
    heldout_ids: Sequence[str],
) -> list[EvalPrompt]:
    """Validate `prompts.yaml` and resolve page contexts. `sections` maps section id → (heading, plain text)."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc = PromptFile.model_validate(raw)
    ids = [p.id for p in doc.prompts]
    if len(set(ids)) != len(ids):
        raise ValueError("prompts.yaml has duplicate prompt ids")
    held = set(heldout_ids)
    out: list[EvalPrompt] = []
    for spec in doc.prompts:
        page: PageContext | None = None
        if spec.page_context is not None:
            ref = spec.page_context.ref
            if spec.page_context.kind == "question":
                if ref in held:
                    raise ValueError(f"{spec.id}: page question {ref} is in the held-out set")
                q = questions.get(ref)
                if q is None:
                    raise ValueError(f"{spec.id}: unknown question {ref}")
                correct = dict(q.options)[q.answer]
                page = PageContext("question", ref, render_question_page(q), q.answer, correct, q.labels)
            else:
                sec = sections.get(ref)
                if sec is None:
                    raise ValueError(f"{spec.id}: unknown section {ref}")
                page = PageContext("section", ref, render_section_page(ref, sec[0], sec[1]))
        out.append(EvalPrompt(spec, page))
    return out


__all__ = [
    "CATEGORIES",
    "EXPECTED_PROMPTS",
    "Builtin",
    "Criterion",
    "EvalPrompt",
    "MustContainAny",
    "MustNotContain",
    "MustNotEcho",
    "PageContext",
    "Passage",
    "PromptFile",
    "PromptSpec",
    "SafetyCheck",
    "load_prompts",
    "render_question_page",
]
