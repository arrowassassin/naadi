"""Review queue, reviews and publication (service role: `question_reviews` and the hidden `questions` columns are
backend-only). Every function takes the connection of one transaction so a review, its outcome and the status
change commit together or not at all."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .lint import LintContext, LintReport, SourceInfo, flat, lint_question
from .models import Decision, Ratings
from .review import Gates, Resolution, ReviewRow, human_reviewers, resolve

FILTERS = ("ai_drafts", "seed_spot_check", "psychometric_flags", "adjudication", "needs_check", "all")
ITEM_COLUMNS = (
    "id, exam, stem_html, options, answer, answer_html, explanation_html, why_not_html, educational_objective,"
    " difficulty, taxonomy_ids, source_id, status, review, stats, created_at"
)


@dataclass(frozen=True)
class QueueRow:
    id: str
    exam: str
    status: str
    origin: str  # ai | seed
    difficulty: str | None
    stem_preview: str
    flags: dict[str, int]
    state: str
    submitted: int
    needed: int
    mine_submitted: bool
    psych_flags: list[str]


def _row(r: Mapping[str, Any]) -> ReviewRow:
    return ReviewRow(
        r["reviewer_user_id"],
        r["decision"],
        bool(r["adjudication"]),
        bool(r["request_second"]),
        dict(r["ratings"] or {}),
        r["created_at"],
    )


async def round_of(conn: psycopg.AsyncConnection[Any], question_id: str) -> int:
    cur = await conn.execute(
        "select coalesce((review->>'round')::int, 1) as r from public.questions where id = %s", (question_id,)
    )
    row = await cur.fetchone()
    return int(row["r"]) if row else 1


async def reviews_of(conn: psycopg.AsyncConnection[Any], question_id: str, round_: int) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "select id, reviewer_user_id, round, ratings, decision, adjudication, request_second, note, created_at"
        " from public.question_reviews where question_id = %s and round = %s order by created_at, id",
        (question_id, round_),
    )
    return list(await cur.fetchall())


async def history_of(conn: psycopg.AsyncConnection[Any], question_id: str) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "select id, reviewer_user_id, round, ratings, decision, adjudication, request_second, note, created_at"
        " from public.question_reviews where question_id = %s order by round, created_at, id",
        (question_id,),
    )
    return list(await cur.fetchall())


async def get_item(conn: psycopg.AsyncConnection[Any], question_id: str) -> dict[str, Any] | None:
    cur = await conn.execute(f"select {ITEM_COLUMNS} from public.questions where id = %s", (question_id,))  # noqa: S608
    return await cur.fetchone()


def origin_of(item: Mapping[str, Any]) -> str:
    return "ai" if (item["review"] or {}).get("authorModel") else "seed"


def lint_item(
    item: Mapping[str, Any], taxonomy: Mapping[str, str], sources: Mapping[str, SourceInfo], labs: Any
) -> LintReport:
    """The linter on a stored item, as the review UI and the publish gate run it. Legacy seed items carry no
    educational objective: for them that is a WARN."""
    review = item["review"] or {}
    seed = origin_of(item) == "seed"
    sim = (review.get("lint") or {}).get("similarity")
    return lint_question(
        {**item, "excerpt_source_ids": (review.get("authorModel") or {}).get("excerptSourceIds", [])},
        LintContext(
            taxonomy=taxonomy,
            sources=sources,
            labs=labs or None,
            max_similarity=sim,
            objective_optional=seed,
        ),
    )


def gates_of(item: Mapping[str, Any], report: LintReport) -> Gates:
    solver = (item["review"] or {}).get("solver") or {}
    return Gates(
        lint_ok=report.ok,
        source_ok="S05" not in {f.rule for f in report.errors},
        cross_check_ok=solver.get("agrees") is True,
        independent=solver.get("independent") is True,
    )


def flag_counts(report: LintReport, review: Mapping[str, Any]) -> dict[str, int]:
    solver_findings = (review.get("solver") or {}).get("findings") or []
    warns = len(report.warnings) + sum(1 for f in solver_findings if f.get("severity") == "warn")
    errors = len(report.errors) + sum(1 for f in solver_findings if f.get("severity") == "error")
    return {"errors": errors, "warnings": warns}


def psych_flags_of(item: Mapping[str, Any]) -> list[str]:
    stats = item["stats"] or {}
    acked = (item["review"] or {}).get("psychAckAt")
    if acked and acked == stats.get("computedAt"):
        return []
    return list(stats.get("flags") or [])


async def list_queue(
    conn: psycopg.AsyncConnection[Any],
    flt: str,
    me: uuid.UUID,
    required: int,
    taxonomy: Mapping[str, str],
    sources: Mapping[str, SourceInfo],
    labs: Any,
    limit: int = 100,
    offset: int = 0,
) -> list[QueueRow]:
    if flt == "seed_spot_check":
        where = (
            "status = 'published' and (review->>'humanExpertReviewed') = 'false' and (review->'authorModel') is null"
        )
    elif flt == "psychometric_flags":
        where = "status = 'published' and jsonb_array_length(coalesce(stats->'flags', '[]'::jsonb)) > 0"
    elif flt == "needs_check":
        where = "status = 'auto_checked'"
    elif flt in ("ai_drafts", "adjudication"):
        where = "status in ('in_review', 'auto_checked', 'draft') and (review->'authorModel') is not null"
    else:
        where = (
            "status in ('in_review', 'auto_checked', 'draft')"
            " or (status = 'published' and (review->>'humanExpertReviewed') = 'false')"
            " or (status = 'published' and jsonb_array_length(coalesce(stats->'flags', '[]'::jsonb)) > 0)"
        )
    cur = await conn.execute(
        f"select {ITEM_COLUMNS} from public.questions where {where} order by created_at desc, id limit %s offset %s",  # noqa: S608
        (limit, offset),
    )
    items = list(await cur.fetchall())
    if not items:
        return []
    ids = [i["id"] for i in items]
    cur = await conn.execute(
        "select r.question_id, r.reviewer_user_id, r.round, r.decision, r.adjudication, r.request_second, r.ratings,"
        " r.created_at from public.question_reviews r"
        " join public.questions q on q.id = r.question_id and r.round = coalesce((q.review->>'round')::int, 1)"
        " where r.question_id = any(%s) order by r.created_at, r.id",
        (ids,),
    )
    by_q: dict[str, list[dict[str, Any]]] = {}
    for r in await cur.fetchall():
        by_q.setdefault(r["question_id"], []).append(r)
    out: list[QueueRow] = []
    for it in items:
        rows = by_q.get(it["id"], [])
        res = resolve([_row(r) for r in rows], required)
        if flt == "adjudication" and res.state != "adjudication":
            continue
        report = lint_item(it, taxonomy, sources, labs)
        out.append(
            QueueRow(
                it["id"],
                it["exam"],
                it["status"],
                origin_of(it),
                it["difficulty"],
                flat(it["stem_html"])[:160],
                flag_counts(report, it["review"] or {}),
                res.state,
                res.submitted,
                res.needed,
                any(r["reviewer_user_id"] == me for r in rows),
                psych_flags_of(it),
            )
        )
    return out


async def counts(conn: psycopg.AsyncConnection[Any], required: int) -> dict[str, int]:
    cur = await conn.execute(
        """
        select
          count(*) filter (where status in ('in_review','auto_checked','draft')
                             and (review->'authorModel') is not null)::int as ai_drafts,
          count(*) filter (where status = 'published' and (review->>'humanExpertReviewed') = 'false'
                             and (review->'authorModel') is null)::int as seed_spot_check,
          count(*) filter (where status = 'published'
                             and jsonb_array_length(coalesce(stats->'flags','[]'::jsonb)) > 0)::int
            as psychometric_flags,
          count(*) filter (where status = 'auto_checked')::int as needs_check
        from public.questions
        """
    )
    row = await cur.fetchone()
    result = dict(row) if row else {"ai_drafts": 0, "seed_spot_check": 0, "psychometric_flags": 0, "needs_check": 0}
    # adjudication: in-review items whose first stage is complete and split
    cur = await conn.execute(
        "select r.question_id, r.reviewer_user_id, r.decision, r.adjudication, r.request_second, r.created_at"
        " from public.question_reviews r join public.questions q on q.id = r.question_id"
        " and r.round = coalesce((q.review->>'round')::int, 1) and q.status in ('in_review','auto_checked')"
        " order by r.question_id, r.created_at"
    )
    grouped: dict[str, list[ReviewRow]] = {}
    for r in await cur.fetchall():
        grouped.setdefault(r["question_id"], []).append(
            ReviewRow(r["reviewer_user_id"], r["decision"], bool(r["adjudication"]), bool(r["request_second"]))
        )
    result["adjudication"] = sum(1 for rows in grouped.values() if resolve(rows, required).state == "adjudication")
    return result


async def insert_review(
    conn: psycopg.AsyncConnection[Any],
    question_id: str,
    reviewer: uuid.UUID,
    round_: int,
    decision: Decision,
    ratings: Ratings,
    note: str | None,
    request_second: bool,
    adjudication: bool,
) -> None:
    await conn.execute(
        "insert into public.question_reviews"
        " (question_id, reviewer_user_id, round, ratings, decision, adjudication, request_second, note)"
        " values (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            question_id,
            reviewer,
            round_,
            Jsonb(ratings.model_dump(mode="json")),
            decision,
            adjudication,
            request_second,
            note,
        ),
    )


async def lock_item(conn: psycopg.AsyncConnection[Any], question_id: str) -> dict[str, Any] | None:
    """Serialise reviews of one item (two admins submitting at once must not both close the round)."""
    cur = await conn.execute(f"select {ITEM_COLUMNS} from public.questions where id = %s for update", (question_id,))  # noqa: S608
    return await cur.fetchone()


async def snapshot_revision(
    conn: psycopg.AsyncConnection[Any], item: Mapping[str, Any], origin: str, note: str, user_id: uuid.UUID | None
) -> bool:
    """One `question_revisions` row (segment 19's table: full row snapshot for audit and rollback). A deployment
    without that table yet skips the snapshot; the review row and `review.humanReviewers[]` are the audit trail."""
    cur = await conn.execute("select to_regclass('public.question_revisions') as t")
    reg = await cur.fetchone()
    if not reg or reg["t"] is None:
        return False
    snapshot = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in item.items() if k != "stats"}
    await conn.execute(
        "insert into public.question_revisions (question_id, snapshot, author_user_id, origin, note)"
        " values (%s, %s, %s, %s, %s)",
        (item["id"], Jsonb(snapshot), user_id, origin, note),
    )
    return True


async def apply_outcome(
    conn: psycopg.AsyncConnection[Any],
    item: Mapping[str, Any],
    resolution: Resolution,
    rows: Sequence[ReviewRow],
    reviewer: uuid.UUID,
    now: datetime | None = None,
) -> str:
    """Move the item for a decided round; returns the new status. approved -> published (a seed stays published and
    becomes human-reviewed), revise -> draft, rejected -> retired."""
    now = now or datetime.now(UTC)
    review = dict(item["review"] or {})
    review["humanReviewers"] = [*review.get("humanReviewers", []), *human_reviewers(rows)]
    status = {"approved": "published", "revise": "draft", "rejected": "retired"}[resolution.state]
    if resolution.state == "approved":
        review["humanExpertReviewed"] = True
        review["status"] = "ai-authored, human-reviewed" if origin_of(item) == "ai" else "seed, human-reviewed"
        review["publishedAt"] = now.isoformat()
        await snapshot_revision(
            conn, {**item, "status": "published", "review": review}, "review", "published", reviewer
        )
    await conn.execute(
        "update public.questions set status = %s, review = %s where id = %s", (status, Jsonb(review), item["id"])
    )
    return status


async def resubmit(conn: psycopg.AsyncConnection[Any], item: Mapping[str, Any], to_status: str) -> int:
    """A revised draft goes back for review: a new round. Returns the round."""
    review = dict(item["review"] or {})
    new_round = int(review.get("round", 1)) + 1
    review["round"] = new_round
    await conn.execute(
        "update public.questions set status = %s, review = %s, stats = '{}'::jsonb where id = %s",
        (to_status, Jsonb(review), item["id"]),
    )
    return new_round


async def store_checks(
    conn: psycopg.AsyncConnection[Any], question_id: str, lint: Mapping[str, Any], solver: Mapping[str, Any] | None
) -> None:
    patch: dict[str, Any] = {"lint": dict(lint)}
    if solver is not None:
        patch["solver"] = dict(solver)
    await conn.execute("update public.questions set review = review || %s where id = %s", (Jsonb(patch), question_id))
