"""USMLE-2026 blueprint coverage for the admin console (segment 23): published items per leaf area per Step
against `blueprint_targets`, with section and card counts. The computation is naadi_api/content/coverage.py, the
same function tools/coverage.py runs on the seed bundle in CI. Structured fields only: the client words them."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from psycopg import AsyncConnection
from pydantic import BaseModel

from ..content.coverage import AreaStatus, Leaf, Tagged, Target, blueprint_coverage
from .common import ADMIN_ERRORS, ConsoleAdmin, rows, run_tx

router = APIRouter(prefix="/api/admin", tags=["admin"])

OUTLINE_VERSION = "USMLE-2026"


class BlueprintArea(BaseModel):
    id: str
    code: str
    label: str
    system: str
    items: int
    sections: int
    cards: int
    min_items: int
    target_items: int
    status: AreaStatus


class BlueprintSystem(BaseModel):
    code: str
    label: str
    items: int
    target_items: int
    areas: int
    areas_at_min: int
    areas_met: int


class BlueprintCoverage(BaseModel):
    exam: Literal["step1", "step2ck"]
    outline_version: str
    items: int
    target_items: int
    areas: int
    areas_below_min: int
    areas_met: int
    systems: list[BlueprintSystem]
    area_rows: list[BlueprintArea]


_LEAVES = """
select n.id, n.code, n.label, s.code as system, s.label as system_label
  from public.taxonomy_nodes n
  join public.taxonomy_nodes s on s.outline_version = n.outline_version and s.code = left(n.code, 2) and s.level = 1
 where n.outline_version = %(v)s and exists (select 1 from public.blueprint_targets b where b.node_id = n.id)
   and n.level >= 2
 order by n.code
"""
_TARGETS = (
    "select node_id, exam, min_items, target_items, tested from public.blueprint_targets where outline_version = %(v)s"
)
_ITEMS = """
select 'items' as measure, q.exam, q.taxonomy_ids as tags from public.questions q
 where q.owner_user_id is null and q.status = 'published'
union all
select 'sections', null, s.taxonomy_ids from public.chapter_sections s join public.chapters c on c.id = s.chapter_id
 where c.owner_user_id is null and c.status = 'published' and s.body is not null
union all
select 'cards', null, f.taxonomy_ids from public.flashcards f where f.owner_user_id is null
"""


@router.get(
    "/coverage/blueprint",
    operation_id="getAdminBlueprintCoverage",
    summary="Admin only: published items per USMLE-2026 leaf area against the blueprint minimum and target",
    response_model=BlueprintCoverage,
    responses=ADMIN_ERRORS,
)
async def blueprint(
    request: Request, admin: ConsoleAdmin, exam: Annotated[Literal["step1", "step2ck"], Query()] = "step1"
) -> BlueprintCoverage:
    async def run(conn: AsyncConnection[Any]) -> BlueprintCoverage:
        v = {"v": OUTLINE_VERSION}
        leaves = [
            Leaf(r["id"], r["code"], r["label"], r["system"], r["system_label"]) for r in await rows(conn, _LEAVES, v)
        ]
        targets = {
            (r["node_id"], r["exam"]): Target(r["min_items"], r["target_items"], r["tested"])
            for r in await rows(conn, _TARGETS, v)
        }
        items = [Tagged(r["measure"], r["exam"], frozenset(r["tags"] or ())) for r in await rows(conn, _ITEMS)]
        rep = blueprint_coverage(leaves, targets, items, exam)
        return BlueprintCoverage(
            exam=exam,
            outline_version=OUTLINE_VERSION,
            items=rep.items,
            target_items=sum(a.target_items for a in rep.areas),
            areas=len(rep.areas),
            areas_below_min=len(rep.below_min),
            areas_met=sum(1 for a in rep.areas if a.status == "met"),
            systems=[BlueprintSystem(**vars(s)) for s in rep.systems],
            area_rows=[BlueprintArea(**vars(a)) for a in rep.areas],
        )

    return await run_tx(request, run)
