"""The held-out MCQ set and the letter parser.

Pick (deterministic, documented in docs/MODEL_TESTS.md): every question in `data/questions.json` is ranked by
`sha256(id.encode("utf-8")).hexdigest()` ascending and the lowest `HELDOUT_SIZE` (100) are held out. The pick
depends only on the ids, so adding questions can only move a question into or out of the set by its own hash,
never reshuffle it. Held-out stems, options, answers and explanations never enter the retrieval index
(`retrieval.build_corpus` drops any chunk that shares a 12-word run with them) or any page context.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from naadi_api.embeddings.text import html_to_text

HELDOUT_SIZE = 100
LETTERS = "ABCDEFGHIJ"


@dataclass(frozen=True)
class Question:
    id: str
    chapter_id: str
    stem: str
    options: tuple[tuple[str, str], ...]  # (label, plain text)
    answer: str  # the correct label; never sent to a model
    explanation: str  # plain text of explanation + why-not; only used to keep it out of the index

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(label for label, _ in self.options)

    def hidden_texts(self) -> list[str]:
        """Everything about this question that must never reach the answerer's context."""
        return [self.stem, *(t for _, t in self.options), self.explanation]


def id_hash(qid: str) -> str:
    return hashlib.sha256(qid.encode("utf-8")).hexdigest()


def load_questions(path: Path) -> list[Question]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    items: Iterable[Any] = raw if isinstance(raw, list) else raw.get("questions", [])
    out: list[Question] = []
    seen: set[str] = set()
    for q in items:
        qid = str(q["id"])
        if qid in seen:
            raise ValueError(f"duplicate question id {qid!r} in {path}")
        seen.add(qid)
        opts = tuple((str(o["label"]).strip().upper(), html_to_text(str(o.get("html") or ""))) for o in q["options"])
        answer = str(q["answer"]).strip().upper()
        if answer not in {label for label, _ in opts}:
            raise ValueError(f"question {qid!r}: answer {answer!r} is not one of its option labels")
        explanation = " ".join(
            html_to_text(str(q.get(k) or "")) for k in ("answerHtml", "explanationHtml", "whyNotHtml")
        ).strip()
        out.append(
            Question(
                id=qid,
                chapter_id=str(q.get("chapterId") or ""),
                stem=html_to_text(str(q.get("stemHtml") or "")),
                options=opts,
                answer=answer,
                explanation=explanation,
            )
        )
    return out


def pick_heldout(questions: Sequence[Question], n: int = HELDOUT_SIZE) -> list[Question]:
    if len(questions) < n:
        raise ValueError(f"need at least {n} questions for the held-out set, found {len(questions)}")
    return sorted(questions, key=lambda q: (id_hash(q.id), q.id))[:n]


# ── letter parsing ──
# Reasoning models may think aloud first; only the text after the reasoning counts.
_THINK = re.compile(r"<think>.*?(?:</think>|$)", re.S | re.I)
_MD = re.compile(r"[*_`]+")


def _patterns(letters: str) -> tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str]]:
    L = f"[{letters}{letters.lower()}]"
    explicit = re.compile(
        # "Answer: C", "Final answer - (C)", "the answer is C", "correct option is C."
        rf"\b(?:final\s+answer|answer|correct\s+(?:answer|option|choice)|best\s+(?:answer|option))"
        rf"\s*(?:is|:|-|=|would\s+be)?\s*(?:option\s+|choice\s+)?[\(\[]?({L})[\)\]]?(?![A-Za-z0-9])",
        re.I,
    )
    # "Option C" / "choice (C)"
    option = re.compile(rf"\b(?:option|choice)\s*[\(\[]?({L})[\)\]]?(?![A-Za-z0-9])", re.I)
    # a line holding only the letter: "C", "(C)", "C."
    bare = re.compile(rf"^\s*[\(\[]?({L})[\)\]]?[.:]?\s*$", re.M)
    return explicit, option, bare


def _counts(m: re.Match[str]) -> bool:
    """Upper-case letters always count. A lower-case one ("answer is a") only when nothing follows it on the
    line, so the article "a" in "the answer is a defect of ..." is never read as option A."""
    if m.group(1).isupper():
        return True
    tail = m.string[m.end() :].split("\n", 1)[0].strip()
    return tail in ("", ".", ")")


def parse_letter(text: str, labels: Sequence[str] = ("A", "B", "C", "D", "E")) -> str | None:
    """The option letter a model chose, or None when it gave none (or several, with no final statement).

    The model is told to end with `Answer: <letter>`. Priority: explicit "answer"-style statements (the last
    one wins: models restate their final choice), then a single distinct "option X", then the last line that
    holds only a letter. Text inside `<think>` blocks is ignored, as is markdown emphasis.
    """
    letters = "".join(sorted({x.upper() for x in labels if len(x) == 1 and x.upper() in LETTERS}))
    if not letters:
        return None
    body = _MD.sub("", _THINK.sub("", text or "")).strip()
    if not body:
        return None
    explicit, option, bare = _patterns(letters)
    hits = [m.group(1).upper() for m in explicit.finditer(body) if _counts(m)]
    if hits:
        return hits[-1]
    found = {m.group(1).upper() for m in option.finditer(body) if _counts(m)}
    if len(found) == 1:
        return found.pop()
    lines = [m.group(1).upper() for m in bare.finditer(body) if _counts(m)]
    if lines:
        return lines[-1]
    return None


__all__ = [
    "HELDOUT_SIZE",
    "Question",
    "id_hash",
    "load_questions",
    "parse_letter",
    "pick_heldout",
]
