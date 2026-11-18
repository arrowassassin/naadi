"""Coverage against HANDOFF §7.2 (segment 19): per taxonomy topic, how much published content exists, against the
goals (one reading section, one or more approved videos, 15 or more questions, 10 or more cards), and the same
counts aggregated to a system × discipline grid for the heat-map.

What counts (shared content students can see, nothing private):
- sections: sections with a live body in published shared chapters, tagged with the node
- videos: approved `video_youtube` / `video_own` assets tagged with the node, or attached (`section_media`) to such
  a section
- questions: published questions tagged with the node
- cards: shared flashcards (no owner) tagged with the node

A topic's system is its parent; an item tagged with a topic counts for that topic's system too. Grid cells are
items tagged with the system (directly or through one of its topics) and with the discipline. The status is a
word (`gap` | `thin` | `covered`), never a colour alone.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from .common import ADMIN_ERRORS, ConsoleAdmin, rows, run_tx

router = APIRouter(prefix="/api/admin", tags=["admin"])

Status = Literal["gap", "thin", "covered"]
Measure = Literal["sections", "videos", "questions", "cards"]
MEASURES: tuple[Measure, ...] = ("sections", "videos", "questions", "cards")


class Goals(BaseModel):
    sections: int = 1
    videos: int = 1
    questions: int = 15
    cards: int = 10


GOALS = Goals()


class Counts(BaseModel):
    sections: int = 0
    videos: int = 0
    questions: int = 0
    cards: int = 0


class Node(BaseModel):
    id: str
    label: str


class Cell(BaseModel):
    system_id: str
    discipline_id: str
    counts: Counts
    status: Status


class TopicCoverage(BaseModel):
    id: str
    label: str
    system_id: str | None
    counts: Counts
    status: Status
    missing: list[Measure] = Field(description="Measures below their goal")


class SystemCoverage(BaseModel):
    id: str
    label: str
    counts: Counts
    topics: int
    topics_covered: int


class CoverageReport(BaseModel):
    exam: str
    goals: Goals
    systems: list[Node]
    disciplines: list[Node]
    cells: list[Cell]
    system_totals: list[SystemCoverage]
    topics: list[TopicCoverage]


def status_of(c: Counts, goals: Goals = GOALS) -> Status:
    met = [getattr(c, m) >= getattr(goals, m) for m in MEASURES]
    if all(met):
        return "covered"
    if any(getattr(c, m) > 0 for m in MEASURES):
        return "thin"
    return "gap"


def missing_of(c: Counts, goals: Goals = GOALS) -> list[Measure]:
    return [m for m in MEASURES if getattr(c, m) < getattr(goals, m)]


@dataclass
class Item:
    measure: Measure
    tags: frozenset[str]


@dataclass
class Taxonomy:
    systems: list[Node]
    disciplines: list[Node]
    topics: list[tuple[Node, str | None]]
    topic_system: dict[str, str] = field(default_factory=dict)


def aggregate(tax: Taxonomy, items: list[Item]) -> tuple[list[Cell], list[TopicCoverage], list[SystemCoverage]]:
    """Pure aggregation (unit-tested): items with their tag sets → topic, system and grid counts."""
    system_ids = {s.id for s in tax.systems}
    discipline_ids = {d.id for d in tax.disciplines}
    per_topic: dict[str, Counts] = defaultdict(Counts)
    per_system: dict[str, Counts] = defaultdict(Counts)
    per_cell: dict[tuple[str, str], Counts] = defaultdict(Counts)
    topic_ids = {t.id for t, _ in tax.topics}
    for it in items:
        item_topics = it.tags & topic_ids
        item_systems = (it.tags & system_ids) | {tax.topic_system[t] for t in item_topics if t in tax.topic_system}
        discs = it.tags & discipline_ids
        for t in item_topics:
            c = per_topic[t]
            setattr(c, it.measure, getattr(c, it.measure) + 1)
        for s in item_systems:
            c = per_system[s]
            setattr(c, it.measure, getattr(c, it.measure) + 1)
            for d in discs:
                c = per_cell[(s, d)]
                setattr(c, it.measure, getattr(c, it.measure) + 1)
    cells = [
        Cell(
            system_id=s.id, discipline_id=d.id, counts=per_cell[(s.id, d.id)], status=status_of(per_cell[(s.id, d.id)])
        )
        for s in tax.systems
        for d in tax.disciplines
    ]
    topics = [
        TopicCoverage(
            id=t.id,
            label=t.label,
            system_id=sys,
            counts=per_topic[t.id],
            status=status_of(per_topic[t.id]),
            missing=missing_of(per_topic[t.id]),
        )
        for t, sys in tax.topics
    ]
    by_sys: dict[str, list[TopicCoverage]] = defaultdict(list)
    for tc in topics:
        if tc.system_id:
            by_sys[tc.system_id].append(tc)
    systems = [
        SystemCoverage(
            id=s.id,
            label=s.label,
            counts=per_system[s.id],
            topics=len(by_sys[s.id]),
            topics_covered=sum(1 for t in by_sys[s.id] if t.status == "covered"),
        )
        for s in tax.systems
    ]
    return cells, topics, systems


async def load_taxonomy(conn: AsyncConnection[Any], exam: str) -> Taxonomy:
    nodes = await rows(
        conn,
        "select id, kind, parent_id, label from public.taxonomy_nodes where %s = any(exam)"
        " and kind in ('system','discipline','topic') order by sort, id",
        (exam,),
    )
    systems = [Node(id=n["id"], label=n["label"]) for n in nodes if n["kind"] == "system"]
    sys_ids = {s.id for s in systems}
    topics = [
        (Node(id=n["id"], label=n["label"]), n["parent_id"] if n["parent_id"] in sys_ids else None)
        for n in nodes
        if n["kind"] == "topic"
    ]
    return Taxonomy(
        systems=systems,
        disciplines=[Node(id=n["id"], label=n["label"]) for n in nodes if n["kind"] == "discipline"],
        topics=topics,
        topic_system={t.id: s for t, s in topics if s},
    )


_ITEMS = """
select 'sections' as measure, s.taxonomy_ids as tags
  from public.chapter_sections s join public.chapters c on c.id = s.chapter_id
 where c.owner_user_id is null and c.status = 'published' and c.exam = %(exam)s and s.body is not null
union all
select 'videos', (m.taxonomy_ids || coalesce((select array_agg(distinct t) from public.section_media sm
                   join public.chapter_sections s on s.id = sm.section_id
                   join public.chapters c on c.id = s.chapter_id and c.owner_user_id is null and c.status = 'published'
                   cross join lateral unnest(s.taxonomy_ids) t
                  where sm.media_id = m.id), '{}'))
  from public.media_assets m
 where m.review_status = 'approved' and m.kind in ('video_youtube','video_own')
union all
select 'questions', q.taxonomy_ids from public.questions q
 where q.owner_user_id is null and q.status = 'published' and q.exam = %(exam)s
union all
select 'cards', f.taxonomy_ids from public.flashcards f where f.owner_user_id is null
"""


@router.get(
    "/coverage",
    operation_id="getAdminCoverage",
    summary="Admin only: published content per taxonomy topic against the goals, and the system × discipline grid",
    response_model=CoverageReport,
    responses=ADMIN_ERRORS,
)
async def coverage(
    request: Request, admin: ConsoleAdmin, exam: Annotated[Literal["step1", "step2ck", "step3"], Query()] = "step1"
) -> CoverageReport:
    async def run(conn: AsyncConnection[Any]) -> CoverageReport:
        tax = await load_taxonomy(conn, exam)
        items = [Item(r["measure"], frozenset(r["tags"] or ())) for r in await rows(conn, _ITEMS, {"exam": exam})]
        cells, topics, systems = aggregate(tax, items)
        return CoverageReport(
            exam=exam,
            goals=GOALS,
            systems=tax.systems,
            disciplines=tax.disciplines,
            cells=cells,
            system_totals=systems,
            topics=topics,
        )

    return await run_tx(request, run)
