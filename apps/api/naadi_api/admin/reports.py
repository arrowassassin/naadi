"""Report-an-issue triage (segment 19, HANDOFF §12): open → triaged → resolved / dismissed, with a deep link to the
reported item. Also registered as the `report_issues` approval queue (approve = resolved, reject = dismissed).

The report body is the student's own words; it is shown to admins only and never logged.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .common import ADMIN_ERRORS, CONFLICT, NOT_FOUND, ConsoleAdmin, Row, audit, conflict, one, rows, run_tx
from .queues import ItemGone, QueueContext, QueueDecision, QueueItem, QueuePage, QueueRef, register_queue

router = APIRouter(prefix="/api/admin/reports", tags=["admin"])

ReportStatus = Literal["open", "triaged", "resolved", "dismissed"]
TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"triaged", "resolved", "dismissed"}),
    "triaged": frozenset({"open", "resolved", "dismissed"}),
    "resolved": frozenset({"open"}),
    "dismissed": frozenset({"open"}),
}


class ReportLink(BaseModel):
    chapter_id: str | None = None
    section_id: str | None = None
    question_id: str | None = None
    media_id: str | None = None


class AdminReport(BaseModel):
    id: uuid.UUID
    ref_type: str
    ref_id: str | None
    body: str
    status: ReportStatus
    created_at: dt.datetime
    link: ReportLink


class AdminReportList(BaseModel):
    items: list[AdminReport]
    counts: dict[str, int]


class ReportStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: ReportStatus
    note: str | None = Field(default=None, max_length=1000)


_SELECT = """
select r.id, r.ref_type, r.ref_id, r.body, r.status, r.created_at,
       case when r.ref_type = 'section' then s.chapter_id when r.ref_type = 'chapter' then r.ref_id
            when r.ref_type = 'question' then q.chapter_id end as chapter_id,
       case when r.ref_type = 'section' then r.ref_id end as section_id,
       case when r.ref_type = 'question' then r.ref_id end as question_id,
       case when r.ref_type = 'media' then r.ref_id end as media_id
  from public.report_issues r
  left join public.chapter_sections s on r.ref_type = 'section' and s.id = r.ref_id
  left join public.questions q on r.ref_type = 'question' and q.id = r.ref_id
"""


def _report(r: Row) -> AdminReport:
    return AdminReport(
        id=r["id"],
        ref_type=r["ref_type"],
        ref_id=r["ref_id"],
        body=r["body"],
        status=r["status"],
        created_at=r["created_at"],
        link=ReportLink(
            chapter_id=r["chapter_id"], section_id=r["section_id"], question_id=r["question_id"], media_id=r["media_id"]
        ),
    )


@router.get(
    "",
    operation_id="listAdminReports",
    summary="Admin only: reported issues, oldest open first",
    response_model=AdminReportList,
    responses=ADMIN_ERRORS,
)
async def list_reports(
    request: Request,
    admin: ConsoleAdmin,
    status: Annotated[ReportStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> AdminReportList:
    async def run(conn: AsyncConnection[Any]) -> AdminReportList:
        sql = (
            _SELECT + (" where r.status = %(status)s" if status else "") + " order by r.created_at asc limit %(limit)s"
        )
        found = await rows(conn, sql, {"status": status, "limit": limit})
        counts = {
            r["status"]: int(r["n"])
            for r in await rows(conn, "select status, count(*) as n from public.report_issues group by 1")
        }
        return AdminReportList(items=[_report(r) for r in found], counts={s: counts.get(s, 0) for s in TRANSITIONS})

    return await run_tx(request, run)


async def set_status(
    conn: AsyncConnection[Any], admin: Any, report_id: uuid.UUID, status: str, note: str | None
) -> tuple[Row, Row]:
    before = await one(conn, _SELECT + " where r.id = %s for update of r", (report_id,))
    if before is None:
        raise HTTPException(404, "No such report.")
    if status != before["status"]:
        if status not in TRANSITIONS[before["status"]]:
            raise conflict("bad_transition", "This status change is not allowed.")
        await conn.execute("update public.report_issues set status = %s where id = %s", (status, report_id))
    after = await one(conn, _SELECT + " where r.id = %s", (report_id,))
    assert after is not None
    await audit(conn, admin, f"report.status_{status}", "report", str(report_id), before=before, after=after, note=note)
    return before, after


@router.post(
    "/{report_id}/status",
    operation_id="setAdminReportStatus",
    summary="Admin only: triage, resolve, dismiss or reopen a reported issue",
    response_model=AdminReport,
    responses={**ADMIN_ERRORS, **NOT_FOUND, **CONFLICT},
)
async def change_status(
    request: Request, admin: ConsoleAdmin, report_id: Annotated[uuid.UUID, Path()], body: ReportStatusChange
) -> AdminReport:
    async def run(conn: AsyncConnection[Any]) -> AdminReport:
        _, after = await set_status(conn, admin, report_id, body.status, body.note)
        return _report(after)

    return await run_tx(request, run)


# ── queue `report_issues` ──

_REF_TYPES = {
    "section": "section",
    "question": "question",
    "chapter": "chapter",
    "media": "media",
    "pathway_task": "pathway_task",
}


async def _list(ctx: QueueContext, *, limit: int, cursor: str | None) -> QueuePage:
    params: dict[str, Any] = {"limit": limit + 1}
    sql = _SELECT + " where r.status in ('open','triaged')"
    if cursor:
        sql += " and (r.created_at, r.id) > (select created_at, id from public.report_issues where id::text = %(c)s)"
        params["c"] = cursor
    found = await rows(ctx.conn, sql + " order by r.created_at, r.id limit %(limit)s", params)
    total = await one(
        ctx.conn, "select count(*)::int as n from public.report_issues where status in ('open','triaged')"
    )
    more = len(found) > limit
    found = found[:limit]
    items = []
    for r in found:
        rt = _REF_TYPES.get(r["ref_type"])
        detail: dict[str, JsonValue] = {
            "ref_type": r["ref_type"],
            "status": r["status"],
            "body": r["body"],
            "chapter_id": r["chapter_id"],
            "section_id": r["section_id"],
            "question_id": r["question_id"],
        }
        items.append(
            QueueItem(
                id=str(r["id"]),
                kind="report_issues",
                created_at=r["created_at"],
                title=None,
                ref=QueueRef(type=rt, id=r["ref_id"]) if rt and r["ref_id"] else None,
                detail=detail,
            )
        )
    return QueuePage(
        items=items, next_cursor=str(found[-1]["id"]) if more and found else None, total=int(total["n"]) if total else 0
    )


def _uuid(item_id: str) -> uuid.UUID:
    try:
        return uuid.UUID(item_id)
    except ValueError as exc:
        raise ItemGone() from exc


async def _decide(ctx: QueueContext, item_id: str, status: str, note: str | None) -> QueueDecision:
    rid = _uuid(item_id)
    current = await one(ctx.conn, "select status from public.report_issues where id = %s", (rid,))
    if current is None or current["status"] not in ("open", "triaged"):
        raise ItemGone()
    before, after = await set_status(ctx.conn, ctx.admin, rid, status, note)
    return QueueDecision(
        id=item_id, status="approved" if status == "resolved" else "rejected", before=before, after=after
    )


async def _approve(ctx: QueueContext, item_id: str, note: str | None, params: dict[str, JsonValue]) -> QueueDecision:
    return await _decide(ctx, item_id, "resolved", note)


async def _reject(ctx: QueueContext, item_id: str, note: str | None) -> QueueDecision:
    return await _decide(ctx, item_id, "dismissed", note)


register_queue("report_issues", _list, _approve, _reject, ref_type="report")
