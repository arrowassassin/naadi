"""Prompts of the author graph.

The author's system prompt IS Part A of `config/authoring_rules.md`, read from that file at run time so the rules
and the prompt cannot drift. The user message carries the cell, the objective, the difficulty and the allowed
excerpts (AI-ingestible sources only). No student data, no vault content, no commercial item text ever goes in.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RULES_FILE = "authoring_rules.md"

OUTPUT_CONTRACT = """
Reply with ONE JSON object and nothing else, with these keys:
  "cannot_write": null, or one sentence saying why you cannot write a valid single-best-answer item from the excerpts;
  "vignette": the stem up to (not including) the lead-in, plain text, paragraphs separated by a blank line;
  "lead_in": the single closed question;
  "options": exactly five objects {"label": "A".."E", "html": option text}, in alphabetical or logical order;
  "answer": the key's label;
  "explanation": why the key is right, plain text, every factual sentence supported by an excerpt;
  "why_not": an object mapping each distractor label to one sentence saying why it is wrong;
  "educational_objective": one sentence;
  "difficulty": "easy" | "medium" | "hard";
  "citations": a list of {"excerpt_id": an id from the excerpts, "claim": the factual claim it supports}, one per
    factual claim in the explanation;
  "nutrition": true or false;
  "demographic_relevance": null, or why a demographic characteristic in the stem is clinically relevant.
Do not include any text outside the JSON object.
""".strip()


def part_a(config_dir: Path) -> str:
    """Part A of the authoring rules, verbatim (without its heading)."""
    text = (config_dir / RULES_FILE).read_text(encoding="utf-8")
    m = re.search(r"^## Part A[^\n]*\n(.*?)^---\s*$", text, re.S | re.M)
    if not m:
        raise ValueError(f"{RULES_FILE}: Part A not found")
    return m.group(1).strip()


def system_prompt(config_dir: Path) -> str:
    return f"{part_a(config_dir)}\n\n{OUTPUT_CONTRACT}"


@dataclass(frozen=True)
class Excerpt:
    id: str
    source_id: str
    text: str
    section_id: str | None = None


def author_message(
    *,
    exam: str,
    labels: Sequence[str],
    objective: str,
    difficulty: str,
    excerpts: Sequence[Excerpt],
    feedback: Sequence[dict[str, Any]] | None = None,
) -> str:
    lines = [
        f"Exam: {exam}",
        "Taxonomy cell: " + " > ".join(labels),
        f"Learning objective: {objective}",
        f"Difficulty target: {difficulty}",
        "",
        "Excerpts (the only factual grounding you may use):",
    ]
    for e in excerpts:
        lines.append(f"[{e.id}] {e.text}")
    if feedback:
        lines += [
            "",
            "Your previous draft was rejected. Fix every problem below and write a new item (codes are the linter's "
            "and the blind solver's rule ids with their parameters):",
            json.dumps(list(feedback), ensure_ascii=False, separators=(",", ":")),
        ]
    return "\n".join(lines)


def prompt_hash(system: str, user: str) -> str:
    return hashlib.sha256((system + "\n\n" + user).encode()).hexdigest()[:16]


SOLVER_SYSTEM = (
    "You are an expert physician answering a USMLE-style single-best-answer question. Reply with ONE JSON object "
    "and nothing else."
)


def solve_message(stem: str, options: Sequence[tuple[str, str]]) -> str:
    opts = "\n".join(f"{lab}. {text}" for lab, text in options)
    return (
        f"{stem}\n\n{opts}\n\n"
        'Reply: {"choice": "<one letter>", "confidence": <0..1>, "defensibility": {"A": <0..1>, "B": <0..1>, '
        '"C": <0..1>, "D": <0..1>, "E": <0..1>}} where defensibility is how defensible each option is as the best '
        "answer (0 = clearly wrong, 1 = fully defensible)."
    )


def cover_message(stem: str) -> str:
    return (
        f"{stem}\n\nYou are not shown any options. Answer the question in a short phrase.\n"
        'Reply: {"answer": "<short free-text answer>"}'
    )


def judge_message(lead_in: str, free_answer: str, key: str) -> str:
    return (
        f"Question: {lead_in}\nAnswer given: {free_answer}\nReference answer: {key}\n\n"
        'Does the answer given mean the same as the reference answer? Reply: {"matches": true|false}'
    )
