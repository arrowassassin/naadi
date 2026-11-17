"""Database access of the authoring pipeline (service role: the pipeline is a backend job, and the tables it
writes are backend-only). Reads of shared content apply the same publish and licence gates RLS and the embedding
index apply to everything else."""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb

from ..db import Database
from .lint import GROUNDING_LICENSE, SourceInfo
from .prompts import Excerpt
from .selection import TaxNode

DB_TIMEOUT_MS = 15_000
PENDING = ("draft", "auto_checked", "in_review")


@asynccontextmanager
async def service_conn(db: Database) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    if db.pool is None:
        await db.open()
    assert db.pool is not None
    async with db.pool.connection(timeout=10.0) as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]
        try:
            async with conn.transaction():
                await conn.execute(f"set local statement_timeout = {DB_TIMEOUT_MS}")
                yield conn
        finally:
            conn.row_factory = tuple_row


@dataclass(frozen=True)
class Gap:
    exam: str
    topic_id: str
    label: str
    published: int
    pending: int


@dataclass
class RunRecord:
    """One `authoring_runs` row."""

    trigger: str
    cell: dict[str, str]
    objective: str
    difficulty: str
    started_by: uuid.UUID | None = None
    author_model: str | None = None
    prompt_hash: str | None = None
    excerpt_ids: list[str] = field(default_factory=list)
    lint: dict[str, Any] = field(default_factory=dict)
    solver: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    llm_calls: int = 0
    outcome: str = "failed"
    question_id: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


_TOKEN = re.compile(r"[A-Za-z][A-Za-z-]{3,}")
_STOP = frozenset({"with", "from", "that", "this", "disease", "disorders", "syndrome", "and", "the", "for"})


def tsquery_of(text: str, limit: int = 8) -> str:
    """An OR `to_tsquery` string from the distinctive words of `text` (never user text; taxonomy labels only)."""
    words = [w.lower().strip("-") for w in _TOKEN.findall(text)]
    uniq = list(dict.fromkeys(w for w in words if w not in _STOP))[:limit]
    return " | ".join(f"{w}:*" for w in uniq)


class PgAuthoringStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ── context ──
    async def taxonomy(self) -> dict[str, TaxNode]:
        async with service_conn(self.db) as c:
            cur = await c.execute("select id, kind, label, parent_id, exam, sort from public.taxonomy_nodes")
            return {
                r["id"]: TaxNode(r["id"], r["kind"], r["label"], r["parent_id"], tuple(r["exam"]), int(r["sort"] or 0))
                for r in await cur.fetchall()
            }

    async def sources(self) -> dict[str, SourceInfo]:
        async with service_conn(self.db) as c:
            cur = await c.execute("select id, license, allow_ai_ingest from public.sources")
            return {r["id"]: SourceInfo(bool(r["allow_ai_ingest"]), r["license"] or "") for r in await cur.fetchall()}

    # ── selection ──
    async def gaps(
        self, exams: Sequence[str], min_published: int, limit: int, skip_topics: Sequence[str] = ()
    ) -> list[Gap]:
        """Topics with fewer than `min_published` published questions (pending drafts count toward not authoring the
        same topic twice), fewest first."""
        async with service_conn(self.db) as c:
            cur = await c.execute(
                """
                select t.id as topic_id, t.label, ex.exam,
                       count(q.id) filter (where q.status = 'published')::int as published,
                       count(q.id) filter (where q.status = any(%(pending)s))::int as pending
                from public.taxonomy_nodes t
                join (select unnest(%(exams)s::text[]) as exam) ex on ex.exam = any(t.exam)
                left join public.questions q on q.exam = ex.exam and q.taxonomy_ids @> array[t.id]
                  and q.owner_user_id is null
                where t.kind = 'topic' and not (t.id = any(%(skip)s))
                group by t.id, t.label, ex.exam, t.sort
                having count(q.id) filter (where q.status = 'published' or q.status = any(%(pending)s)) < %(min)s
                order by count(q.id) filter (where q.status = 'published' or q.status = any(%(pending)s)), t.sort, t.id
                limit %(limit)s
                """,
                {
                    "exams": list(exams),
                    "pending": list(PENDING),
                    "skip": list(skip_topics),
                    "min": min_published,
                    "limit": limit,
                },
            )
            return [
                Gap(r["exam"], r["topic_id"], r["label"], r["published"], r["pending"]) for r in await cur.fetchall()
            ]

    async def common_second(self, exam: str, topic_id: str) -> str | None:
        kind = "discipline" if exam == "step1" else "clinical_science"
        async with service_conn(self.db) as c:
            cur = await c.execute(
                """
                select n.id from public.questions q
                cross join lateral unnest(q.taxonomy_ids) as tid
                join public.taxonomy_nodes n on n.id = tid and n.kind = %s and %s = any(n.exam)
                where q.exam = %s and q.taxonomy_ids @> array[%s] and q.status = 'published'
                  and q.owner_user_id is null
                group by n.id order by count(*) desc, n.id limit 1
                """,
                (kind, exam, exam, topic_id),
            )
            row = await cur.fetchone()
            return str(row["id"]) if row else None

    async def failed_topics(self, days: int = 7, times: int = 3) -> list[str]:
        """Topics whose runs were discarded `times` or more in the last `days`: skipped so a stubborn topic does not
        eat the night's calls."""
        async with service_conn(self.db) as c:
            cur = await c.execute(
                """
                select cell->>'topic' as topic from public.authoring_runs
                where created_at > now() - make_interval(days => %s)
                  and outcome in ('discarded_lint','discarded_solver','failed') and cell ? 'topic'
                group by 1 having count(*) >= %s
                """,
                (days, times),
            )
            return [r["topic"] for r in await cur.fetchall()]

    # ── grounding ──
    async def excerpts(self, taxonomy_ids: Sequence[str], text: str, limit: int, chars: int) -> list[Excerpt]:
        """Chunks of published, shared sections whose source allows AI ingestion under a grounding licence
        (Naadi original, public domain, CC BY). Never user uploads, never link-only sources."""
        q = tsquery_of(text)
        if not q:
            return []
        async with service_conn(self.db) as c:
            cur = await c.execute(
                """
                select e.ref_id as section_id, e.chunk_index, e.content, c.source_id,
                       (s.taxonomy_ids && %(ids)s::text[]) as tagged,
                       ts_rank(e.tsv, to_tsquery('english', %(q)s)) as rank
                from public.embeddings e
                join public.chapter_sections s on s.id = e.ref_id
                join public.chapters c on c.id = s.chapter_id
                join public.sources src on src.id = c.source_id
                where e.ref_type = 'section' and e.owner_user_id is null
                  and c.owner_user_id is null and c.status = 'published' and c.kind = 'chapter'
                  and src.allow_ai_ingest and src.license ~* %(lic)s
                  and (e.tsv @@ to_tsquery('english', %(q)s) or s.taxonomy_ids && %(ids)s::text[])
                order by tagged desc, rank desc, e.ref_id, e.chunk_index
                limit %(n)s
                """,
                {"ids": list(taxonomy_ids), "q": q, "lic": GROUNDING_LICENSE.pattern, "n": limit},
            )
            rows = await cur.fetchall()
        return [
            Excerpt(f"{r['section_id']}#{r['chunk_index']}", r["source_id"], str(r["content"])[:chars], r["section_id"])
            for r in rows
        ]

    async def max_similarity(self, vector_literal: str) -> float:
        """Highest cosine similarity between a draft's text and any question already indexed."""
        async with service_conn(self.db) as c:
            cur = await c.execute(
                "select 1 - (embedding <=> %s::halfvec(384)) as sim from public.embeddings"
                " where ref_type = 'question' and embedding is not null"
                " order by embedding <=> %s::halfvec(384) limit 1",
                (vector_literal, vector_literal),
            )
            row = await cur.fetchone()
            return float(row["sim"]) if row else 0.0

    # ── writes ──
    async def insert_item(
        self, question_id: str, row: Mapping[str, Any], status: str, review: Mapping[str, Any]
    ) -> None:
        async with service_conn(self.db) as c:
            # questions-scope: insert (authored items are shared: owner_user_id stays null)
            await c.execute(
                """
                insert into public.questions
                  (id, chapter_id, exam, stem_html, options, answer, answer_html, explanation_html, why_not_html,
                   educational_objective, difficulty, taxonomy_ids, tags, source_id, status, review, stats)
                values (%s, null, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '{}'::jsonb)
                """,
                (
                    question_id,
                    row["exam"],
                    row["stem_html"],
                    Jsonb(row["options"]),
                    row["answer"],
                    row.get("answer_html"),
                    row["explanation_html"],
                    row["why_not_html"],
                    row["educational_objective"],
                    row["difficulty"],
                    list(row["taxonomy_ids"]),
                    ["AI-authored"],
                    row["source_id"],
                    status,
                    Jsonb(dict(review)),
                ),
            )

    async def record_run(self, run: RunRecord) -> uuid.UUID:
        async with service_conn(self.db) as c:
            cur = await c.execute(
                """
                insert into public.authoring_runs
                  (id, trigger, started_by, cell, objective, difficulty, author_model, prompt_hash, excerpt_ids,
                   lint, solver, attempts, llm_calls, outcome, question_id)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id
                """,
                (
                    run.id,
                    run.trigger,
                    run.started_by,
                    Jsonb(run.cell),
                    run.objective,
                    run.difficulty,
                    run.author_model,
                    run.prompt_hash,
                    run.excerpt_ids,
                    Jsonb(run.lint),
                    Jsonb(run.solver),
                    run.attempts,
                    run.llm_calls,
                    run.outcome,
                    run.question_id,
                ),
            )
            row = await cur.fetchone()
            assert row is not None
            return uuid.UUID(str(row["id"]))

    async def calls_today(self, trigger: str) -> int:
        async with service_conn(self.db) as c:
            cur = await c.execute(
                "select coalesce(sum(llm_calls), 0)::int as n from public.authoring_runs"
                " where trigger = %s"
                " and created_at >= date_trunc('day', now() at time zone 'utc') at time zone 'utc'",
                (trigger,),
            )
            row = await cur.fetchone()
            return int(row["n"]) if row else 0

    async def pending_review_count(self) -> int:
        async with service_conn(self.db) as c:
            cur = await c.execute(
                "select count(*)::int as n from public.questions"
                " where status = any(%s) and (review->>'authorModel') is not null and owner_user_id is null",
                (list(PENDING),),
            )
            row = await cur.fetchone()
            return int(row["n"]) if row else 0
