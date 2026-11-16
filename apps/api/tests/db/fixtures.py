"""Minimal rows for every Naadi table, shared by the migration test and the RLS suite.

`USER_TABLES` describes each user-owned table: how to insert one row for a user (given the shared content
ids), which column the owner can update, and how to identify the row. Adding a user-owned table to a
migration without adding it here fails `test_rls_coverage.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class Shared:
    source_id: str
    chapter_id: str
    draft_chapter_id: str
    section_id: str
    question_id: str
    draft_question_id: str
    flashcard_id: uuid.UUID  # shared, not linked to a question
    linked_flashcard_id: uuid.UUID  # shared, linked to question_id
    stage_key: str
    task_id: str
    media_id: str
    unapproved_media_id: str


@dataclass(frozen=True)
class UserTable:
    name: str
    row: Callable[[uuid.UUID, Shared], dict[str, Any]]
    key: tuple[str, ...]  # columns identifying the row (with user_id)
    update: tuple[str, Any]  # (column, new value) the owner may change


def _id() -> uuid.UUID:
    return uuid.uuid4()


def ink_layer_id(user_id: uuid.UUID) -> uuid.UUID:
    """The fixture's ink layer id per user, so the ink_items row can reference its own user's layer."""
    return uuid.uuid5(user_id, "ink-layer")


INK_ITEM_ID = "019a8c4e-0000-7000-8000-000000000001"
INK_ITEM: dict[str, Any] = {
    "id": INK_ITEM_ID,
    "tool": "pen",
    "color": "ink",
    "size": 0.002,
    "points": [[0.1, 0.1, 0.5, 0]],
    "createdAt": 1_763_280_000_000,
    "updatedAt": 1_763_280_000_000,
}


_BLOCKS = uuid.UUID("5e09a1b2-0000-4000-8000-000000000009")


def block_id_of(user_id: uuid.UUID) -> uuid.UUID:
    """The fixture block of a user (stable, so `block_sections` can reference its own user's block)."""
    return uuid.uuid5(_BLOCKS, str(user_id))


USER_TABLES: tuple[UserTable, ...] = (
    UserTable(
        "profiles", lambda u, s: {"user_id": u, "display_name": "Test"}, ("user_id",), ("display_name", "Renamed")
    ),
    UserTable(
        "attempts",
        lambda u, s: {"user_id": u, "question_id": s.question_id, "chosen": "A", "mode": "tutor"},
        ("user_id", "question_id"),
        ("flagged", True),
    ),
    UserTable(
        "blocks",
        lambda u, s: {
            "id": block_id_of(u),
            "user_id": u,
            "config": Jsonb({"count": 1}),
            "question_ids": [s.question_id],
        },
        ("user_id",),
        ("started_at", "2026-12-01T08:00:00Z"),
    ),
    # Timing of a timed/exam block's sections (S09): select-only for the student, written by the API.
    UserTable(
        "block_sections",
        lambda u, s: {
            "block_id": block_id_of(u),
            "user_id": u,
            "n": 1,
            "first_index": 0,
            "count": 1,
            "time_limit_s": 90,
        },
        ("user_id", "block_id", "n"),
        ("deadline_at", "2026-12-01T09:00:00Z"),
    ),
    UserTable(
        "card_reviews",
        lambda u, s: {"user_id": u, "card_id": s.flashcard_id, "due": "2026-12-01T00:00:00Z", "reps": 0},
        ("user_id", "card_id"),
        ("reps", 1),
    ),
    UserTable(
        "notes",
        lambda u, s: {"id": _id(), "user_id": u, "anchor_type": "section", "anchor_id": s.section_id},
        ("user_id",),
        ("ocr_text", "edited"),
    ),
    UserTable(
        "notebooks", lambda u, s: {"id": _id(), "user_id": u, "title": "Cardio"}, ("user_id",), ("title", "Renal")
    ),
    UserTable(
        "ink_layers",
        lambda u, s: {"id": ink_layer_id(u), "user_id": u, "surface_type": "section", "surface_id": s.section_id},
        ("user_id",),
        ("recognized_text", "ink"),
    ),
    UserTable(
        "uploads",
        lambda u, s: {"id": _id(), "user_id": u, "kind": "pdf", "original_name": "a.pdf", "bytes": 10},
        ("user_id",),
        ("original_name", "renamed.pdf"),
    ),
    UserTable(
        "plan_items",
        lambda u, s: {"id": _id(), "user_id": u, "day": "2026-12-01", "kind": "read", "title": "Read ch05"},
        ("user_id",),
        ("status", "done"),
    ),
    UserTable(
        "user_pathway_tasks",
        lambda u, s: {"user_id": u, "task_id": s.task_id},
        ("user_id", "task_id"),
        ("status", "doing"),
    ),
    UserTable(
        "vault_documents",
        lambda u, s: {"id": _id(), "user_id": u, "doc_type": "passport", "title": "Passport"},
        ("user_id",),
        ("title", "Passport (renewed)"),
    ),
    UserTable(
        "costs",
        lambda u, s: {"id": _id(), "user_id": u, "task_id": s.task_id, "label": "Step 1 fee", "amount": 1000},
        ("user_id",),
        ("amount", 1010),
    ),
    UserTable(
        "assistant_threads", lambda u, s: {"id": _id(), "user_id": u, "title": "Murmurs"}, ("user_id",), ("title", "x")
    ),
    UserTable(
        "kb_sources",
        lambda u, s: {"id": _id(), "user_id": u, "kind": "url", "url": "https://example.org/article"},
        ("user_id",),
        ("title", "Article"),
    ),
    UserTable(
        "ingest_items",
        lambda u, s: {"id": _id(), "user_id": u, "detected_kind": "pdf"},
        ("user_id",),
        ("confirmed_placement", Jsonb({"chapter": "new"})),
    ),
    UserTable(
        "generated_media",
        lambda u, s: {"id": _id(), "user_id": u, "kind": "image", "prompt": "a heart", "model": "test-model"},
        ("user_id",),
        ("title", "Heart"),
    ),
    UserTable(
        "report_issues",
        lambda u, s: {"id": _id(), "user_id": u, "ref_type": "section", "ref_id": s.section_id, "body": "Typo"},
        ("user_id",),
        ("body", "Typo in line 2"),
    ),
    UserTable(
        "reading_positions",
        lambda u, s: {
            "user_id": u,
            "chapter_id": s.chapter_id,
            "section_id": s.section_id,
            "scroll_position": 1.5,
            "client_updated_at": "2026-11-16T08:00:00Z",
        },
        ("user_id", "section_id"),
        ("scroll_position", 2.25),
    ),
    UserTable(
        "ink_items",
        lambda u, s: {
            "user_id": u,
            "layer_id": ink_layer_id(u),
            "item_id": INK_ITEM_ID,
            "updated_at_ms": 1_763_280_000_000,
            "item": Jsonb(INK_ITEM),
        },
        ("user_id",),
        ("content_rev", "r2"),
    ),
    UserTable(
        "card_review_logs",
        lambda u, s: {
            "user_id": u,
            "card_id": s.flashcard_id,
            "client_review_id": _id(),
            "rating": 3,
            "state": 0,
            "due": "2026-12-01T00:00:00Z",
            "elapsed_days": 0,
            "last_elapsed_days": 0,
            "scheduled_days": 0,
            "review": "2026-12-01T00:00:00Z",
            "next_state": 1,
            "next_step": 1,
            "next_due": "2026-12-01T00:10:00Z",
            "next_stability": 2.3065,
            "next_difficulty": 2.1181,
            "reps": 1,
            "lapses": 0,
        },
        ("user_id", "card_id"),
        ("rating", 4),
    ),
)

USER_TABLE_NAMES = tuple(t.name for t in USER_TABLES)


def insert_row(conn: psycopg.Connection, table: str, row: dict[str, Any]) -> None:
    cols = list(row)
    conn.execute(
        sql.SQL("insert into public.{} ({}) values ({})").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, cols)),
            sql.SQL(", ").join(sql.Placeholder() * len(cols)),
        ),
        [row[c] for c in cols],
    )


def seed_shared(conn: psycopg.Connection, owner_for_private: uuid.UUID | None = None) -> Shared:
    """Insert shared content (as the connection's privileged role). Ids are unique per call."""
    tag = uuid.uuid4().hex[:8]
    s = Shared(
        source_id=f"test-src-{tag}",
        chapter_id=f"test-ch-{tag}",
        draft_chapter_id=f"test-ch-draft-{tag}",
        section_id=f"test-ch-{tag}-s01",
        question_id=f"test-ch-{tag}-q01",
        draft_question_id=f"test-ch-{tag}-q02",
        flashcard_id=uuid.uuid4(),
        linked_flashcard_id=uuid.uuid4(),
        stage_key=f"test-stage-{tag}",
        task_id=f"test-task-{tag}",
        media_id=f"test-fig-{tag}",
        unapproved_media_id=f"test-fig-pending-{tag}",
    )
    insert_row(conn, "sources", {"id": s.source_id, "name": "Test", "license": "proprietary-naadi"})
    insert_row(
        conn,
        "chapters",
        {"id": s.chapter_id, "exam": "step1", "kind": "chapter", "title": "T", "source_id": s.source_id},
    )
    insert_row(
        conn,
        "chapters",
        {
            "id": s.draft_chapter_id,
            "exam": "step1",
            "kind": "chapter",
            "title": "D",
            "status": "draft",
            "source_id": s.source_id,
        },
    )
    insert_row(conn, "chapter_sections", {"id": s.section_id, "chapter_id": s.chapter_id, "sort": 1, "heading": "H"})
    question = {
        "chapter_id": s.chapter_id,
        "exam": "step1",
        "stem_html": "<p>stem</p>",
        "options": Jsonb([{"label": "A", "html": "a"}, {"label": "B", "html": "b"}]),
        "answer": "B",
        "explanation_html": "<p>because</p>",
        "source_id": s.source_id,
    }
    insert_row(conn, "questions", {"id": s.question_id, **question})
    insert_row(conn, "questions", {"id": s.draft_question_id, **question, "status": "draft"})
    insert_row(
        conn,
        "flashcards",
        {"id": s.flashcard_id, "front_html": "f", "back_html": "b", "origin": "seed"},
    )
    insert_row(
        conn,
        "flashcards",
        {
            "id": s.linked_flashcard_id,
            "front_html": "Q?",
            "back_html": "The answer is B",
            "origin": "seed",
            "question_id": s.question_id,
        },
    )
    insert_row(conn, "pathway_stages", {"key": s.stage_key, "sort": 1, "title": "Stage"})
    insert_row(conn, "pathway_tasks", {"id": s.task_id, "stage_key": s.stage_key, "sort": 1, "title": "Task"})
    insert_row(
        conn, "media_assets", {"id": s.media_id, "kind": "svg", "source_id": s.source_id, "review_status": "approved"}
    )
    insert_row(conn, "media_assets", {"id": s.unapproved_media_id, "kind": "svg", "source_id": s.source_id})
    insert_row(conn, "section_media", {"section_id": s.section_id, "media_id": s.media_id, "sort": 1})
    insert_row(conn, "section_media", {"section_id": s.section_id, "media_id": s.unapproved_media_id, "sort": 2})
    insert_row(
        conn, "taxonomy_nodes", {"id": f"test.sys.{tag}", "kind": "system", "label": "Cardio", "exam": ["step1"]}
    )
    insert_row(conn, "embeddings", {"ref_type": "section", "ref_id": s.section_id, "content": "shared chunk"})
    return s


def seed_user_rows(conn: psycopg.Connection, user_id: uuid.UUID, shared: Shared) -> None:
    for t in USER_TABLES:
        insert_row(conn, t.name, t.row(user_id, shared))


def seed_admin_rows(conn: psycopg.Connection, user_id: uuid.UUID, shared: Shared) -> None:
    tag = uuid.uuid4().hex[:8]
    insert_row(conn, "app_config", {"key": f"test_{tag}", "value": Jsonb({"v": 1})})
    conn.execute("update public.app_config set value = '{\"v\": 2}' where key = %s", (f"test_{tag}",))
    insert_row(
        conn, "chapter_section_revisions", {"section_id": shared.section_id, "origin": "seed", "body_html": "<p>x</p>"}
    )
    insert_row(conn, "search_cache", {"query_norm": f"q {tag}", "results": Jsonb([])})
    insert_row(conn, "llm_usage", {"user_id": user_id, "provider": "nvidia", "role": "tutor", "ok": True})
