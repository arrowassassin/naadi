"""Queues owned by other segments, registered with the one S19 registry through their public functions.

- `youtube_candidates` (segment 20): `naadi_api.youtube.queue` list / approve / reject. S19 runs the licence gates
  for an embed on a page before approving (YouTube source, embeddable, metadata at most 30 days old).
  S20's functions commit their own transaction; the registry's audit row follows in the queue transaction.
- `question_review` (segment 21): decided on its own page (`/admin/review`, two reviewers, ratings). The hub shows
  the count per filter and the first items; approve / reject are not offered here.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import JsonValue

from ..authoring import review_store
from ..config_api.schemas import AuthoringRules
from ..pathway import store as pathway_store
from ..youtube import queue as yt
from .common import one, rows
from .licence import GateRefused, check_media_row
from .queues import ItemGone, QueueContext, QueueDecision, QueueItem, QueuePage, QueueRef, register_queue

# ── youtube_candidates ──


def _offset(cursor: str | None) -> int:
    return int(cursor) if cursor and cursor.isdigit() else 0


async def _yt_list(ctx: QueueContext, *, limit: int, cursor: str | None) -> QueuePage:
    offset = _offset(cursor)
    found = await yt.list_candidates(ctx.app.state.db, status="pending", limit=limit + 1, offset=offset)
    page = found.items[:limit]
    return QueuePage(
        items=[
            QueueItem(
                id=c.media_id,
                kind="youtube_candidates",
                created_at=c.created_at,
                title=c.title,
                ref=QueueRef(type="section", id=c.section_id) if c.section_id else None,
                detail={
                    "youtube_id": c.youtube_id,
                    "channel_id": c.channel_id,
                    "duration_s": c.duration_s,
                    "score": c.score,
                    "matched_terms": list(c.matched_terms),
                    "section_id": c.section_id,
                    "section_heading": c.section_heading,
                    "chapter_id": c.chapter_id,
                    "taxonomy_ids": list(c.taxonomy_ids),
                },
            )
            for c in page
        ],
        next_cursor=str(offset + limit) if len(found.items) > limit else None,
        total=await yt.pending_count(ctx.app.state.db),
    )


def _int_param(params: dict[str, JsonValue], key: str) -> int | None:
    v = params.get(key)
    if v is None:
        return None
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise GateRefused(f"bad_{key}")
    return v


async def _yt_approve(ctx: QueueContext, item_id: str, note: str | None, params: dict[str, JsonValue]) -> QueueDecision:
    before = await one(ctx.conn, "select id, review_status from public.media_assets where id = %s", (item_id,))
    if before is None:
        raise ItemGone()
    await check_media_row(ctx.conn, item_id, use="embed", require_approved=False)
    await check_media_row(ctx.conn, item_id, use="page", require_approved=False)
    section = params.get("section_id")
    try:
        out = await yt.approve(
            ctx.app.state.db,
            item_id,
            reviewer=ctx.admin.user_id,
            section_id=section if isinstance(section, str) and section else None,
            start_s=_int_param(params, "start_s"),
            end_s=_int_param(params, "end_s"),
        )
    except yt.QueueError as exc:
        if exc.status == 404:
            raise ItemGone() from exc
        raise GateRefused(exc.code, item_id) from exc
    from ..embeddings.jobs import schedule_reindex

    schedule_reindex(ctx.app, ("sections",), ref_ids=[out.section_id])
    return QueueDecision(id=item_id, status="approved", before=before, after=out.model_dump(mode="json"))


async def _yt_reject(ctx: QueueContext, item_id: str, note: str | None) -> QueueDecision:
    before = await one(ctx.conn, "select id, review_status from public.media_assets where id = %s", (item_id,))
    try:
        await yt.reject(ctx.app.state.db, item_id, reviewer=ctx.admin.user_id)
    except yt.QueueError as exc:
        raise ItemGone() from exc
    return QueueDecision(id=item_id, status="rejected", before=before, after={"review_status": "rejected"})


async def _yt_count(ctx: QueueContext) -> int:
    return await yt.pending_count(ctx.app.state.db)


register_queue("youtube_candidates", _yt_list, _yt_approve, _yt_reject, ref_type="media", count_fn=_yt_count)


# ── question_review ──

REVIEW_FILTERS = ("ai_drafts", "seed_spot_check", "psychometric_flags", "needs_check", "adjudication")


async def _required(ctx: QueueContext) -> int:
    rules = AuthoringRules.model_validate(await ctx.app.state.app_config.get("authoring"))
    return int(rules.required_approvals)


async def _review_count(ctx: QueueContext) -> int:
    c = await review_store.counts(ctx.conn, await _required(ctx))
    # The filters overlap (an AI draft can also need adjudication): the hub shows distinct items waiting.
    r = await one(
        ctx.conn,
        "select count(*)::int as n from public.questions where status in ('in_review','auto_checked')"
        " or (status = 'published' and (review->>'humanExpertReviewed') = 'false')"
        " or (status = 'published' and jsonb_array_length(coalesce(stats->'flags','[]'::jsonb)) > 0)",
    )
    return int(r["n"]) if r else sum(c.values())


async def _review_list(ctx: QueueContext, *, limit: int, cursor: str | None) -> QueuePage:
    offset = _offset(cursor)
    found = await rows(
        ctx.conn,
        "select id, exam, status, difficulty, created_at,"
        " left(btrim(regexp_replace(regexp_replace(stem_html, '<[^>]*>', ' ', 'g'), '\\s+', ' ', 'g')), 160) as stem"
        " from public.questions where status in ('in_review','auto_checked')"
        " order by created_at, id limit %s offset %s",
        (limit + 1, offset),
    )
    counts = await review_store.counts(ctx.conn, await _required(ctx))
    page = found[:limit]
    return QueuePage(
        items=[
            QueueItem(
                id=r["id"],
                kind="question_review",
                created_at=r["created_at"],
                title=None,
                ref=QueueRef(type="question", id=r["id"]),
                detail={
                    "exam": r["exam"],
                    "status": r["status"],
                    "difficulty": r["difficulty"],
                    "stem_text": r["stem"],
                    "filter_counts": {k: int(v) for k, v in counts.items()},
                },
            )
            for r in page
        ],
        next_cursor=str(offset + limit) if len(found) > limit else None,
        total=await _review_count(ctx),
    )


register_queue(
    "question_review",
    _review_list,
    None,
    None,
    ref_type="question",
    count_fn=_review_count,
    path="/admin/review",
    filters=REVIEW_FILTERS,
)

__all__ = ["REVIEW_FILTERS"]


# ── pathway_checks (segment 17) ──
# The weekly official-page check flags a task whose page changed (open = changed and unresolved). Approve = the
# admin confirms the task is still right (S17's verify_task: last_verified today, flag cleared, open checks
# resolved); reject = this check is noise (resolved without verifying the task).


async def _pc_list(ctx: QueueContext, *, limit: int, cursor: str | None) -> QueuePage:
    offset = _offset(cursor)
    found, total = await pathway_store.list_checks(ctx.conn, only_open=True, limit=offset + limit + 1)
    page = found[offset : offset + limit]
    items = []
    for r in page:
        finding = r["finding"] if isinstance(r["finding"], dict) else {}
        items.append(
            QueueItem(
                id=str(r["id"]),
                kind="pathway_checks",
                created_at=r["checked_at"],
                title=r["task_title"],
                ref=QueueRef(type="pathway_task", id=r["task_id"]),
                detail={
                    "task_id": r["task_id"],
                    "stage_key": r["stage_key"],
                    "url": r["url"],
                    "http_status": r["http_status"],
                    "status": finding.get("status"),
                    "summary_key": finding.get("summary_key"),
                    "quotes": [q for q in (finding.get("quotes") or []) if isinstance(q, dict)][:5],
                    "last_verified": r["last_verified"].isoformat() if r["last_verified"] else None,
                },
            )
        )
    return QueuePage(items=items, next_cursor=str(offset + limit) if len(found) > offset + limit else None, total=total)


async def _open_check(ctx: QueueContext, item_id: str) -> dict[str, Any]:
    if not item_id.isdigit():
        raise ItemGone()
    r = await one(
        ctx.conn,
        "select id, task_id, resolved_at from public.pathway_task_checks"
        " where id = %s and resolved_at is null and changed for update",
        (int(item_id),),
    )
    if r is None:
        raise ItemGone()
    return r


async def _pc_approve(ctx: QueueContext, item_id: str, note: str | None, params: dict[str, JsonValue]) -> QueueDecision:
    check = await _open_check(ctx, item_id)
    task = await pathway_store.verify_task(
        ctx.conn, check["task_id"], ctx.admin.user_id, dt.datetime.now(dt.UTC).date()
    )
    return QueueDecision(
        id=item_id,
        status="approved",
        before={"check": item_id},
        after={"task": check["task_id"], "verified": str(task["last_verified"])},
    )


async def _pc_reject(ctx: QueueContext, item_id: str, note: str | None) -> QueueDecision:
    check = await _open_check(ctx, item_id)
    await ctx.conn.execute(
        "update public.pathway_task_checks set resolved_by = %s, resolved_at = now() where id = %s",
        (ctx.admin.user_id, check["id"]),
    )
    return QueueDecision(id=item_id, status="rejected", before={"check": item_id}, after={"resolved": True})


async def _pc_count(ctx: QueueContext) -> int:
    r = await one(
        ctx.conn, "select count(*)::int as n from public.pathway_task_checks where resolved_at is null and changed"
    )
    return int(r["n"]) if r else 0


register_queue("pathway_checks", _pc_list, _pc_approve, _pc_reject, ref_type="pathway_task", count_fn=_pc_count)
