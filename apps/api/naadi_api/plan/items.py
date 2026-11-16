"""Plan days → `plan_items` (pure). Template text stays verbatim; this only classifies and sizes each piece.

One day becomes, in this order: the assessment (if any), the day's focus (reading for chapter days), chapter
questions, Qbank blocks, cards, extra resource, admin task. The rest day becomes one `rest` item. Durations are
the day's target hours shared out with the fixed minutes from `app_config.plan.item_minutes`; there are no clock
times.

`ref_type` says where an item leads:
- `chapter` (ref_id = chapter id): Naadi's own reader.
- `qbank` (ref_id = exam): Naadi's Qbank builder for that exam.
- `cards`: Naadi's card review.
- `mock` (ref_id = exam): Naadi's own mock exam (no link until the exam flow ships).
- `external` (ref_id = provider key): a resource outside Naadi (UWorld, NBME, AnKing, …): a chip, never a link.
- null: text only.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from ..config_api.schemas import PlanItemMinutes
from .generator import PlanDay

ItemMinutes = PlanItemMinutes

PlanKind = str  # one of plan_items.kind: read, questions, assessment, cards, video, admin, rest


# Outside resources, matched by name in the template text. Order matters: the first match names the item.
EXTERNAL = (
    ("uworld", re.compile(r"\bUWorld\b", re.IGNORECASE)),
    ("nbme", re.compile(r"\bNBME\b|\bFree[ -]?120\b", re.IGNORECASE)),
    ("anking", re.compile(r"\bAnKing\b", re.IGNORECASE)),
    ("mehlman", re.compile(r"\bMehlman\b", re.IGNORECASE)),
)
ANKI = re.compile(r"\bAnki\b", re.IGNORECASE)
NAADI_MOCK = re.compile(r"\bBook Mock Exam\b|\bNaadi Mock\b", re.IGNORECASE)
_BLOCKS = re.compile(r"(\d+)\s*blocks?\s*[×x]\s*(\d+)", re.IGNORECASE)
_LEADING = re.compile(r"^\s*(\d+)\b")


@dataclass(frozen=True, slots=True)
class PlanItem:
    sort: int
    kind: PlanKind
    title: str
    ref_type: str | None
    ref_id: str | None
    target_minutes: int | None
    target_count: int | None


def external_of(text: str) -> str | None:
    for key, pat in EXTERNAL:
        if pat.search(text):
            return key
    return None


def question_count(text: str | None) -> int | None:
    """ "2 blocks × 40, tutor→timed" → 80; "40 tutor (random)" → 40; "1 block × 40" → 40; otherwise None."""
    if not text:
        return None
    m = _BLOCKS.search(text)
    if m:
        return int(m.group(1)) * int(m.group(2))
    m = _LEADING.match(text)
    return int(m.group(1)) if m else None


def _round5(x: float) -> int:
    return max(5, int(5 * round(x / 5)))


@dataclass(frozen=True, slots=True)
class _Draft:
    kind: PlanKind
    title: str
    ref_type: str | None
    ref_id: str | None
    count: int | None
    fixed: float | None  # minutes; None = shares the remainder


def _drafts(day: PlanDay, exam: str, sizes: ItemMinutes) -> list[_Draft]:
    per_q = sizes.seconds_per_question / 60 * (1 + sizes.review_factor)
    out: list[_Draft] = []
    if day.self_assessment:
        ext = external_of(day.self_assessment)
        if ext:
            out.append(_Draft("assessment", day.self_assessment, "external", ext, None, None))
        elif NAADI_MOCK.search(day.self_assessment):
            out.append(_Draft("assessment", day.self_assessment, "mock", exam, None, None))
        else:
            out.append(_Draft("assessment", day.self_assessment, None, None, None, None))
    if day.topic and not day.self_assessment:
        if day.chapter_ids:
            for cid in day.chapter_ids:
                out.append(_Draft("read", day.topic, "chapter", cid, None, None))
        elif ANKI.search(day.topic):
            out.append(_Draft("cards", day.topic, "cards", None, None, float(sizes.cards)))
        else:
            out.append(_Draft("read", day.topic, None, None, None, None))
    if day.chapter_questions:
        n = question_count(day.chapter_questions)
        out.append(
            _Draft(
                "questions",
                day.chapter_questions,
                "qbank",
                exam,
                n,
                n * per_q if n else float(sizes.questions_untimed),
            )
        )
    if day.qbank:
        n = question_count(day.qbank)
        ext = external_of(day.qbank)
        out.append(
            _Draft(
                "questions",
                day.qbank,
                "external" if ext else "qbank",
                ext or exam,
                n,
                n * per_q if n else float(sizes.questions_untimed),
            )
        )
    if day.extra_resource:
        ext = external_of(day.extra_resource)
        if ext:
            out.append(_Draft("read", day.extra_resource, "external", ext, None, float(sizes.extra)))
        elif ANKI.search(day.extra_resource):
            out.append(_Draft("cards", day.extra_resource, "cards", None, None, float(sizes.cards)))
        else:
            out.append(_Draft("read", day.extra_resource, None, None, None, float(sizes.extra)))
    if day.admin_task:
        out.append(_Draft("admin", day.admin_task, None, None, None, float(sizes.admin)))
    return out


def items_for_day(day: PlanDay, exam: str, sizes: ItemMinutes, *, rest_day: bool) -> list[PlanItem]:
    total = (day.target_hours or 0) * 60
    if rest_day:
        title = day.topic or day.chapter_ref or ""
        return [PlanItem(0, "rest", title, None, None, int(total) if total else None, None)]
    drafts = _drafts(day, exam, sizes)
    fixed = sum(d.fixed or 0 for d in drafts)
    flexible = [d for d in drafts if d.fixed is None]
    if flexible:
        share = max(float(sizes.min_read), (total - fixed) / len(flexible))
        drafts = [replace(d, fixed=share) if d.fixed is None else d for d in drafts]
    planned = sum(d.fixed or 0 for d in drafts)
    scale = total / planned if planned > total > 0 else 1.0
    return [
        PlanItem(
            i,
            d.kind,
            d.title,
            d.ref_type,
            d.ref_id,
            _round5((d.fixed or 0) * scale) if total else None,
            d.count,
        )
        for i, d in enumerate(drafts)
    ]


def plan_items(days: Sequence[PlanDay], exam: str, sizes: ItemMinutes) -> dict[int, list[PlanItem]]:
    """Items per day number. The last day is the rest day."""
    last = days[-1].day_no if days else 0
    return {d.day_no: items_for_day(d, exam, sizes, rest_day=d.day_no == last) for d in days}
