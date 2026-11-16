"""Results of a finished timed/exam block: pure aggregation over its graded attempts (segment 09).

Inputs are plain rows (one per question of the block, in order) so the arithmetic is unit-tested without a
database. Insights are structured (`kind` + values); the client words them with ui_copy templates, so the
same numbers always give the same sentences and no model is involved.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from .exam_models import (
    ChangeKind,
    ChangeStats,
    FlagStats,
    Insight,
    QuestionOutcome,
    ResultQuestion,
    TimeStats,
    TopicStat,
    TrendPoint,
    WeightOverlay,
)

WEAK_PERCENT = 60.0
TopicKind = Literal["system", "discipline", "competency", "clinical_science"]
TOPIC_KINDS: tuple[TopicKind, ...] = ("system", "discipline", "competency", "clinical_science")


@dataclass(frozen=True)
class GradedQuestion:
    n: int
    section_n: int
    question_id: str
    answer: str
    chosen: str | None
    first_chosen: str | None
    seconds: int | None
    flagged: bool
    taxonomy_ids: tuple[str, ...]


@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    label: str
    sort: int
    weight_min: float | None
    weight_max: float | None


def outcome(q: GradedQuestion) -> QuestionOutcome:
    if q.chosen is None:
        return "omitted"
    return "correct" if q.chosen == q.answer else "incorrect"


def change_of(q: GradedQuestion) -> ChangeKind | None:
    if q.chosen is None or q.first_chosen is None or q.first_chosen == q.chosen:
        return None
    if q.first_chosen == q.answer:
        return "right_to_wrong"
    if q.chosen == q.answer:
        return "wrong_to_right"
    return "wrong_to_wrong"


def pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def time_stats(questions: Sequence[GradedQuestion], target_s: int | None) -> TimeStats:
    secs = [q.seconds for q in questions if q.chosen is not None and q.seconds is not None]
    return TimeStats(
        mean_s=round(statistics.fmean(secs), 1) if secs else None,
        median_s=round(float(statistics.median(secs)), 1) if secs else None,
        target_s=target_s,
        total_s=sum(q.seconds or 0 for q in questions),
    )


def change_stats(questions: Sequence[GradedQuestion]) -> ChangeStats:
    kinds = [change_of(q) for q in questions]
    return ChangeStats(
        changed=sum(1 for k in kinds if k is not None),
        right_to_wrong=kinds.count("right_to_wrong"),
        wrong_to_right=kinds.count("wrong_to_right"),
        wrong_to_wrong=kinds.count("wrong_to_wrong"),
    )


def flag_stats(questions: Sequence[GradedQuestion]) -> FlagStats:
    flagged = [q for q in questions if q.flagged]
    return FlagStats(flagged=len(flagged), flagged_correct=sum(1 for q in flagged if outcome(q) == "correct"))


def topic_stats(questions: Sequence[GradedQuestion], nodes: Mapping[str, Node], kind: TopicKind) -> list[TopicStat]:
    totals: dict[str, list[int]] = {}
    for q in questions:
        for tid in dict.fromkeys(q.taxonomy_ids):
            node = nodes.get(tid)
            if node is None or node.kind != kind:
                continue
            t = totals.setdefault(tid, [0, 0])
            t[0] += 1
            t[1] += 1 if outcome(q) == "correct" else 0
    out = [
        TopicStat(
            id=tid,
            label=nodes[tid].label,
            kind=kind,
            total=t[0],
            correct=t[1],
            percent=pct(t[1], t[0]),
            weak=pct(t[1], t[0]) < WEAK_PERCENT,
        )
        for tid, t in totals.items()
    ]
    # Weakest first; ties by size (bigger first), then the taxonomy's own order.
    out.sort(key=lambda s: (s.percent, -s.total, nodes[s.id].sort, s.label))
    return out


def weight_overlay(questions: Sequence[GradedQuestion], nodes: Mapping[str, Node]) -> list[WeightOverlay]:
    """Every system of the exam that has an official weight (or appears in the block), in the taxonomy's order."""
    total = len(questions)
    stats = {s.id: s for s in topic_stats(questions, nodes, "system")}
    systems = [n for n in nodes.values() if n.kind == "system" and (n.weight_min is not None or n.id in stats)]
    systems.sort(key=lambda n: (n.sort, n.label))
    out: list[WeightOverlay] = []
    for n in systems:
        s = stats.get(n.id)
        out.append(
            WeightOverlay(
                id=n.id,
                label=n.label,
                weight_min=n.weight_min,
                weight_max=n.weight_max,
                share=pct(s.total, total) if s else 0.0,
                total=s.total if s else 0,
                correct=s.correct if s else 0,
                percent=s.percent if s else None,
            )
        )
    return out


def insights(
    *,
    percent: float,
    time: TimeStats,
    changes: ChangeStats,
    flags: FlagStats,
    systems: Sequence[TopicStat],
    disciplines: Sequence[TopicStat],
    weights: Sequence[WeightOverlay],
    trend: Sequence[TrendPoint],
) -> list[Insight]:
    """ "What Naadi sees": at most five sentences, chosen and ordered by fixed rules."""
    out: list[Insight] = []
    topics = [t for t in (*systems, *disciplines) if t.total >= 2]
    weak = [t for t in topics if t.weak]
    if weak:
        w = min(weak, key=lambda t: (t.percent, -t.total))
        out.append(Insight(kind="weakest_topic", vars={"topic": w.label, "correct": w.correct, "total": w.total}))
    strong = [t for t in topics if not t.weak and t.percent >= 80]
    if strong:
        s = max(strong, key=lambda t: (t.percent, t.total))
        out.append(Insight(kind="strongest_topic", vars={"topic": s.label, "correct": s.correct, "total": s.total}))
    if time.mean_s is not None and time.target_s:
        if time.mean_s > time.target_s * 1.1:
            out.append(Insight(kind="pace_slow", vars={"mean": round(time.mean_s), "target": time.target_s}))
        elif time.mean_s < time.target_s * 0.5:
            out.append(Insight(kind="pace_fast", vars={"mean": round(time.mean_s), "target": time.target_s}))
    if changes.right_to_wrong > changes.wrong_to_right:
        out.append(
            Insight(kind="changes_hurt", vars={"lost": changes.right_to_wrong, "gained": changes.wrong_to_right})
        )
    elif changes.wrong_to_right > 0:
        out.append(
            Insight(kind="changes_helped", vars={"gained": changes.wrong_to_right, "lost": changes.right_to_wrong})
        )
    if flags.flagged >= 2:
        out.append(Insight(kind="flags_right", vars={"correct": flags.flagged_correct, "flagged": flags.flagged}))
    under = [
        w
        for w in weights
        if w.weight_min is not None and w.total > 0 and w.percent is not None and w.percent < WEAK_PERCENT
    ]
    if under:
        u = max(under, key=lambda w: (w.weight_max or w.weight_min or 0, -(w.percent or 0)))
        out.append(
            Insight(
                kind="underweighted",
                vars={
                    "topic": u.label,
                    "percent": round(u.percent or 0),
                    "weight_min": _num(u.weight_min),
                    "weight_max": _num(u.weight_max if u.weight_max is not None else u.weight_min),
                },
            )
        )
    if len(trend) >= 2:
        prev = trend[-2].score
        delta = round(percent - prev)
        if delta >= 5:
            out.append(Insight(kind="trend_up", vars={"delta": delta, "previous": round(prev)}))
        elif delta <= -5:
            out.append(Insight(kind="trend_down", vars={"delta": -delta, "previous": round(prev)}))
    return out[:5]


def _num(v: float | None) -> int | float:
    if v is None:
        return 0
    return int(v) if float(v).is_integer() else float(v)


def questions_out(questions: Sequence[GradedQuestion]) -> list[ResultQuestion]:
    return [
        ResultQuestion(
            n=q.n,
            section_n=q.section_n,
            question_id=q.question_id,
            outcome=outcome(q),
            flagged=q.flagged,
            change=change_of(q),
            seconds=q.seconds,
        )
        for q in questions
    ]
