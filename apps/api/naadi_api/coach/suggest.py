"""The coach's suggestions (pure): candidates from computed facts, rules wording, LLM output validation.

The server decides *what* can be suggested (kinds, the numbers behind each, and the action accepting applies);
the model only words up to three of those candidates. Every number in the model's text must appear in that
candidate's facts, or the suggestion is dropped. With no model (budget spent, validation failed) the same
candidates are worded by `ui_copy` templates `coach.rules.<kind>.title|body` with the facts as variables.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..analytics.student import CoachFacts

Kind = Literal["catch_up_block", "review_misses", "cards_catchup", "reread_section"]
KINDS: tuple[Kind, ...] = ("catch_up_block", "review_misses", "cards_catchup", "reread_section")
MIN_MISSES = 5
MIN_CARDS_DUE = 20
LOW_RETENTION = 80
# Every way a number can be written in the model's text. Digits count anywhere ("Q99", "a20" too); number words
# and multiplicatives ("twelve", "triple", "a dozen") and multi-letter roman numerals ("XL", "III") count as their
# value. A suggestion whose text holds any number that is not in its facts is dropped.
_DIGITS = re.compile(r"\d+(?:[.,]\d+)?")
_WORD = re.compile(r"[A-Za-z]+")
_ROMAN = re.compile(r"\b[IVXL]{2,}\b")
_UNITS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
    "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
)  # fmt: skip
_TENS = ("thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
NUMBER_WORDS: dict[str, float] = {
    **{w: float(i) for i, w in enumerate(_UNITS)},
    **{w: float(10 * i) for i, w in enumerate(_TENS, start=3)},
    "hundred": 100, "hundreds": 100, "thousand": 1000, "thousands": 1000, "million": 1e6, "millions": 1e6,
    "half": 0.5, "halves": 0.5, "quarter": 0.25, "quarters": 0.25,
    "double": 2, "doubled": 2, "twice": 2, "triple": 3, "tripled": 3, "thrice": 3, "treble": 3,
    "quadruple": 4, "dozen": 12, "dozens": 12, "score": 20,
    "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15, "twentieth": 20,
}  # fmt: skip
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50}


@dataclass(frozen=True, slots=True)
class Section:
    chapter_id: str
    section_id: str
    heading: str


@dataclass(frozen=True, slots=True)
class Candidate:
    kind: Kind
    facts: dict[str, Any]
    action: dict[str, Any] = field(default_factory=dict)


def candidates(
    f: CoachFacts, *, catch_up_questions: int, section: Section | None = None, limit: int = 3
) -> list[Candidate]:
    """What tonight's facts justify, most useful first."""
    out: list[Candidate] = []
    if f.weak_systems:
        _sid, label, attempts, correct, percent = f.weak_systems[0]
        out.append(
            Candidate(
                "catch_up_block",
                {
                    "system": label,
                    "percent": percent,
                    "attempts": attempts,
                    "correct": correct,
                    "questions": catch_up_questions,
                },
                {
                    "add_items": [
                        {
                            "kind": "questions",
                            "title": label,
                            "ref_type": "weak_spot",
                            "ref_id": f.exam,
                            "target_count": catch_up_questions,
                            "target_minutes": catch_up_questions * 3,
                        }
                    ]
                },
            )
        )
    if f.misses_7d >= MIN_MISSES:
        n = min(f.misses_7d, 40)
        top = f.miss_systems[0][1] if f.miss_systems else None
        out.append(
            Candidate(
                "review_misses",
                {"misses": f.misses_7d, "days": 7, "questions": n, **({"system": top} if top else {})},
                {
                    "add_items": [
                        {
                            "kind": "questions",
                            "title": top or "",
                            "ref_type": "qbank_incorrect",
                            "ref_id": f.exam,
                            "target_count": n,
                            "target_minutes": n * 3,
                        }
                    ]
                },
            )
        )
    if f.cards_due >= MIN_CARDS_DUE or (f.card_retention is not None and f.card_retention < LOW_RETENTION):
        facts: dict[str, Any] = {"due": f.cards_due}
        if f.card_retention is not None:
            facts["retention"] = f.card_retention
        out.append(
            Candidate(
                "cards_catchup",
                facts,
                {
                    "add_items": [
                        {
                            "kind": "cards",
                            "title": "",
                            "ref_type": "cards",
                            "ref_id": None,
                            "target_count": f.cards_due,
                            "target_minutes": max(10, min(90, f.cards_due // 2)),
                        }
                    ]
                },
            )
        )
    if section is not None and f.weak_systems:
        _, label, _, _, percent = f.weak_systems[0]
        out.append(
            Candidate(
                "reread_section",
                {"system": label, "percent": percent, "section": section.heading},
                {
                    "add_items": [
                        {
                            "kind": "read",
                            "title": section.heading,
                            "ref_type": "chapter",
                            "ref_id": section.chapter_id,
                            "target_minutes": 30,
                        }
                    ]
                },
            )
        )
    return out[:limit]


def _numbers_in(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, bool):
        return found
    if isinstance(value, int | float):
        found.add(_norm(str(value)))
    elif isinstance(value, str):
        found |= set(numbers_in_text(value))
    elif isinstance(value, Mapping):
        for v in value.values():
            found |= _numbers_in(v)
    elif isinstance(value, Iterable):
        for v in value:
            found |= _numbers_in(v)
    return found


def _norm(n: str) -> str:
    n = n.replace(",", ".")
    if "." in n:
        n = n.rstrip("0").rstrip(".")
    return n


def _roman(token: str) -> int | None:
    total, prev = 0, 0
    for ch in reversed(token):
        v = _ROMAN_VALUES[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    # Only canonical spellings (XL, XIV, III), so an ordinary capitalised word is never read as a number.
    return total if _to_roman(total) == token else None


def _to_roman(n: int) -> str:
    out = ""
    for v, sym in ((50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out += sym
            n -= v
    return out


def numbers_in_text(text: str) -> list[str]:
    """Every number written in `text`, normalised: digits anywhere, number words, multi-letter roman numerals."""
    found = [_norm(m) for m in _DIGITS.findall(text)]
    found += [_norm(str(NUMBER_WORDS[w.lower()])) for w in _WORD.findall(text) if w.lower() in NUMBER_WORDS]
    for tok in _ROMAN.findall(text):
        value = _roman(tok)
        if value is not None:
            found.append(str(value))
    return found


def numbers_ok(text: str, facts: Mapping[str, Any]) -> bool:
    """Every number written in `text` (digits, words or roman numerals) appears in `facts` (values or numbers
    inside fact strings)."""
    allowed = _numbers_in(facts)
    return all(n in allowed for n in numbers_in_text(text))


class _LlmSuggestion(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: Kind
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=600)


class _LlmOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    suggestions: list[_LlmSuggestion] = Field(max_length=3)


@dataclass(frozen=True, slots=True)
class Written:
    kind: Kind
    source: Literal["llm", "rules"]
    title: str | None
    body: str | None
    facts: dict[str, Any]
    action: dict[str, Any]


class InvalidOutput(ValueError):
    pass


def _json_text(content: Any) -> str:
    if isinstance(content, list):
        content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    text = str(content).strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise InvalidOutput("no JSON object in the reply")
    return text[start : end + 1]


def parse_llm(content: Any, offered: Sequence[Candidate], *, limit: int) -> tuple[list[Written], int]:
    """(valid suggestions, dropped count). Raises InvalidOutput when the reply is not the schema at all."""
    try:
        out = _LlmOutput.model_validate(json.loads(_json_text(content)))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise InvalidOutput(type(exc).__name__) from exc
    by_kind = {c.kind: c for c in offered}
    kept: list[Written] = []
    dropped = 0
    for s in out.suggestions:
        c = by_kind.get(s.kind)
        title, body = " ".join(s.title.split()), " ".join(s.body.split())
        if c is None or any(w.kind == s.kind for w in kept) or not numbers_ok(f"{title} {body}", c.facts):
            dropped += 1
            continue
        kept.append(Written(c.kind, "llm", title, body, c.facts, c.action))
    return kept[:limit], dropped


def rules(offered: Sequence[Candidate], *, limit: int) -> list[Written]:
    """The same candidates, worded by the client from ui_copy (`coach.rules.<kind>.*`) with the facts."""
    return [Written(c.kind, "rules", None, None, c.facts, c.action) for c in offered[:limit]]


SYSTEM_PROMPT = (
    "You are Naadi's study coach for a medical student preparing for a USMLE exam. You receive candidate "
    "suggestions, each with a kind and the facts behind it. Choose at most {limit} of them, most useful first, and "
    "write each one a short title (at most 8 words) and a body of one or two plain sentences addressed to the "
    "student. Use only the numbers that appear in that candidate's facts; never invent, round or combine numbers. "
    "No greetings, no medical advice, no promises about passing. Reply with JSON only: "
    '{{"suggestions": [{{"kind": "<kind>", "title": "...", "body": "..."}}]}}'
)


def prompt_payload(offered: Sequence[Candidate]) -> str:
    return json.dumps({"candidates": [{"kind": c.kind, "facts": c.facts} for c in offered]}, ensure_ascii=False)
