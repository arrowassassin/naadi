"""Row-level security: cross-user denial on every user-owned table, anon denial, shared-content visibility,
answer-column privileges, admin-only tables."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg import errors, sql
from psycopg.types.json import Jsonb

from .fixtures import USER_TABLES, Shared, UserTable, insert_row, seed_admin_rows, seed_user_rows
from .rls import DB_MARKS, as_anon, as_service_role, as_user, create_auth_user

pytestmark = DB_MARKS

ADMIN_TABLES = ("app_config", "app_config_history", "chapter_section_revisions", "search_cache", "llm_usage")
SHARED_TABLES = (
    "chapters",
    "chapter_sections",
    "questions",
    "media_assets",
    "section_media",
    "taxonomy_nodes",
    "sources",
    "pathway_stages",
    "pathway_tasks",
    "flashcards",
)
SECRET_QUESTION_COLUMNS = (
    "answer",
    "answer_html",
    "explanation_html",
    "why_not_html",
    "educational_objective",
    "review",
    "stats",
)


def _count(conn: psycopg.Connection, table: str, user_id: uuid.UUID) -> int:
    row = conn.execute(
        sql.SQL("select count(*) from public.{} where user_id = %s").format(sql.Identifier(table)), (user_id,)
    ).fetchone()
    assert row is not None
    return int(row[0])


def _update(conn: psycopg.Connection, t: UserTable, user_id: uuid.UUID) -> int:
    col, value = t.update
    cur = conn.execute(
        sql.SQL("update public.{} set {} = %s where user_id = %s").format(sql.Identifier(t.name), sql.Identifier(col)),
        (value, user_id),
    )
    return cur.rowcount


def _delete(conn: psycopg.Connection, t: UserTable, user_id: uuid.UUID) -> int:
    cur = conn.execute(sql.SQL("delete from public.{} where user_id = %s").format(sql.Identifier(t.name)), (user_id,))
    return cur.rowcount


@pytest.mark.parametrize("table", USER_TABLES, ids=lambda t: t.name)
def test_user_b_cannot_touch_user_a_rows(
    pg: psycopg.Connection, user_a: uuid.UUID, user_b: uuid.UUID, shared: Shared, table: UserTable
) -> None:
    seed_user_rows(pg, user_a, shared)
    assert _count(pg, table.name, user_a) == 1

    with as_user(pg, user_b) as conn:
        assert _count(conn, table.name, user_a) == 0, "B can see A's rows"
        assert _update(conn, table, user_a) == 0, "B updated A's rows"
        assert _delete(conn, table, user_a) == 0, "B deleted A's rows"
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            insert_row(conn, table.name, table.row(user_a, shared))
        # B also cannot move one of its own rows over to A.
        insert_row(conn, table.name, table.row(user_b, shared))
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute(
                sql.SQL("update public.{} set user_id = %s where user_id = %s").format(sql.Identifier(table.name)),
                (user_a, user_b),
            )

    # Nothing of A's changed.
    assert _count(pg, table.name, user_a) == 1
    col, value = table.update
    row = pg.execute(
        sql.SQL("select {} is distinct from %s from public.{} where user_id = %s").format(
            sql.Identifier(col), sql.Identifier(table.name)
        ),
        (value, user_a),
    ).fetchone()
    assert row is not None and row[0] is True


@pytest.mark.parametrize("table", USER_TABLES, ids=lambda t: t.name)
def test_user_a_can_select_update_delete_insert_own_rows(
    pg: psycopg.Connection, user_a: uuid.UUID, shared: Shared, table: UserTable
) -> None:
    seed_user_rows(pg, user_a, shared)
    with as_user(pg, user_a) as conn:
        assert _count(conn, table.name, user_a) == 1
        assert _update(conn, table, user_a) == 1
        assert _delete(conn, table, user_a) == 1
        assert _count(conn, table.name, user_a) == 0
        insert_row(conn, table.name, table.row(user_a, shared))
        assert _count(conn, table.name, user_a) == 1


@pytest.mark.parametrize("table", USER_TABLES, ids=lambda t: t.name)
def test_anon_has_no_access_to_user_tables(
    pg: psycopg.Connection, user_a: uuid.UUID, shared: Shared, table: UserTable
) -> None:
    seed_user_rows(pg, user_a, shared)
    with as_anon(pg) as conn:
        for stmt in (
            sql.SQL("select count(*) from public.{}").format(sql.Identifier(table.name)),
            sql.SQL("delete from public.{}").format(sql.Identifier(table.name)),
        ):
            with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
                conn.execute(stmt)
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            insert_row(conn, table.name, table.row(user_a, shared))


def test_restricted_columns_are_backend_only(pg: psycopg.Connection, user_a: uuid.UUID) -> None:
    with as_user(pg, user_a) as conn:
        insert_row(conn, "generated_media", {"user_id": user_a, "kind": "image", "prompt": "p", "model": "m"})
        insert_row(conn, "report_issues", {"user_id": user_a, "ref_type": "other", "body": "b"})
        for stmt in (
            "update public.generated_media set approved_for_publish = true",
            "update public.generated_media set ai_generated = false",
            "update public.report_issues set status = 'resolved'",
        ):
            with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
                conn.execute(stmt)
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            insert_row(
                conn,
                "generated_media",
                {"user_id": user_a, "kind": "image", "prompt": "p", "model": "m", "approved_for_publish": True},
            )


def test_composite_foreign_keys_keep_parents_per_user(
    pg: psycopg.Connection, user_a: uuid.UUID, user_b: uuid.UUID, shared: Shared
) -> None:
    notebook = uuid.uuid4()
    insert_row(pg, "notebooks", {"id": notebook, "user_id": user_a, "title": "A's"})
    with as_user(pg, user_b) as conn, pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        insert_row(conn, "notes", {"user_id": user_b, "notebook_id": notebook, "anchor_type": "free"})


def test_anon_reads_only_published_shared_content(pg: psycopg.Connection, shared: Shared) -> None:
    with as_anon(pg) as conn:
        ids = {r[0] for r in conn.execute("select id from public.chapters where id like 'test-ch-%'").fetchall()}
        assert shared.chapter_id in ids
        assert shared.draft_chapter_id not in ids
        qs = {r[0] for r in conn.execute("select id from public.questions where id like 'test-ch-%'").fetchall()}
        assert shared.question_id in qs and shared.draft_question_id not in qs
        media = {
            r[0]
            for r in conn.execute(
                "select id from public.media_assets where id = any(%s)",
                ([shared.media_id, shared.unapproved_media_id],),
            ).fetchall()
        }
        assert media == {shared.media_id}
        placed = conn.execute(
            "select media_id from public.section_media where section_id = %s", (shared.section_id,)
        ).fetchall()
        assert [r[0] for r in placed] == [shared.media_id]
        for table in SHARED_TABLES:
            conn.execute(sql.SQL("select 1 from public.{} limit 1").format(sql.Identifier(table)))
        # read-only for clients
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("update public.chapters set title = 'x'")
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            insert_row(conn, "sources", {"id": "evil", "name": "x", "license": "x"})


def test_shared_content_is_read_only_for_authenticated(
    pg: psycopg.Connection, user_a: uuid.UUID, shared: Shared
) -> None:
    with as_user(pg, user_a) as conn:
        for stmt in (
            "update public.chapters set title = 'x'",
            "delete from public.chapter_sections",
            "update public.taxonomy_nodes set label = 'x'",
            "delete from public.pathway_tasks",
            "update public.media_assets set review_status = 'approved'",
        ):
            with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
                conn.execute(stmt)
        # Shared (seed) flashcards are not editable by students even though they can read them.
        assert (
            conn.execute("update public.flashcards set front_html = 'x' where id = %s", (shared.flashcard_id,)).rowcount
            == 0
        )


def test_private_chapter_is_visible_only_to_its_owner(
    pg: psycopg.Connection, user_a: uuid.UUID, user_b: uuid.UUID, shared: Shared
) -> None:
    ch = f"test-ch-private-{uuid.uuid4().hex[:8]}"
    insert_row(
        pg,
        "chapters",
        {
            "id": ch,
            "exam": "step1",
            "kind": "user_kb",
            "title": "Mine",
            "owner_user_id": user_a,
            "source_id": shared.source_id,
        },
    )
    insert_row(pg, "chapter_sections", {"id": f"{ch}-s1", "chapter_id": ch, "sort": 1, "heading": "h"})
    with as_user(pg, user_a) as conn:
        assert conn.execute("select count(*) from public.chapters where id = %s", (ch,)).fetchone() == (1,)
        assert conn.execute("select count(*) from public.chapter_sections where chapter_id = %s", (ch,)).fetchone() == (
            1,
        )
    with as_user(pg, user_b) as conn:
        assert conn.execute("select count(*) from public.chapters where id = %s", (ch,)).fetchone() == (0,)
        assert conn.execute("select count(*) from public.chapter_sections where chapter_id = %s", (ch,)).fetchone() == (
            0,
        )
    with as_anon(pg) as conn:
        assert conn.execute("select count(*) from public.chapters where id = %s", (ch,)).fetchone() == (0,)


def test_private_chapter_requires_an_owner(pg: psycopg.Connection, shared: Shared) -> None:
    with pytest.raises(errors.CheckViolation), pg.transaction():
        insert_row(pg, "chapters", {"id": "test-ch-orphan", "exam": "step1", "kind": "user_upload", "title": "x"})


def test_user_flashcards_are_private_and_owner_writable(
    pg: psycopg.Connection, user_a: uuid.UUID, user_b: uuid.UUID, shared: Shared
) -> None:
    with as_user(pg, user_a) as conn:
        card = uuid.uuid4()
        insert_row(conn, "flashcards", {"id": card, "owner_user_id": user_a, "front_html": "f", "back_html": "b"})
        assert conn.execute("update public.flashcards set back_html = 'b2' where id = %s", (card,)).rowcount == 1
        assert conn.execute("delete from public.flashcards where id = %s", (card,)).rowcount == 1
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            insert_row(conn, "flashcards", {"owner_user_id": user_b, "front_html": "f", "back_html": "b"})
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            insert_row(conn, "flashcards", {"owner_user_id": None, "front_html": "f", "back_html": "b"})
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            insert_row(
                conn, "flashcards", {"owner_user_id": user_a, "front_html": "f", "back_html": "b", "origin": "seed"}
            )

    a_card = uuid.uuid4()
    insert_row(pg, "flashcards", {"id": a_card, "owner_user_id": user_a, "front_html": "f", "back_html": "b"})
    with as_user(pg, user_b) as conn:
        assert conn.execute("select count(*) from public.flashcards where id = %s", (a_card,)).fetchone() == (0,)
        assert conn.execute(
            "select count(*) from public.flashcards where id = %s", (shared.flashcard_id,)
        ).fetchone() == (1,)
        assert conn.execute("update public.flashcards set back_html = 'x' where id = %s", (a_card,)).rowcount == 0
        assert conn.execute("delete from public.flashcards where id = %s", (a_card,)).rowcount == 0
    with as_anon(pg) as conn:
        assert conn.execute("select count(*) from public.flashcards where id = %s", (a_card,)).fetchone() == (0,)


@pytest.mark.parametrize("role", ["anon", "authenticated"])
@pytest.mark.parametrize("column", SECRET_QUESTION_COLUMNS)
def test_question_answer_columns_are_not_selectable(
    pg: psycopg.Connection, user_a: uuid.UUID, shared: Shared, role: str, column: str
) -> None:
    ctx = as_anon(pg) if role == "anon" else as_user(pg, user_a)
    with ctx as conn:
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute(sql.SQL("select {} from public.questions").format(sql.Identifier(column)))
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("select * from public.questions")
        # Filtering or sorting by a hidden column would leak it too.
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute(sql.SQL("select id from public.questions where {} is not null").format(sql.Identifier(column)))
        # The client-visible columns work.
        row = conn.execute("select id, stem_html, options from public.questions where id = %s", (shared.question_id,))
        assert row.fetchone() is not None


def test_question_answers_readable_by_service_role(pg: psycopg.Connection, shared: Shared) -> None:
    with as_service_role(pg) as conn:
        row = conn.execute("select answer, explanation_html from public.questions where id = %s", (shared.question_id,))
        assert row.fetchone() == ("B", "<p>because</p>")


def test_embeddings_visibility(pg: psycopg.Connection, user_a: uuid.UUID, user_b: uuid.UUID, shared: Shared) -> None:
    insert_row(pg, "embeddings", {"owner_user_id": user_a, "ref_type": "note", "ref_id": "n1", "content": "A's note"})
    with as_user(pg, user_a) as conn:
        got = {
            r[0]
            for r in conn.execute(
                "select content from public.embeddings where ref_id in (%s, 'n1')", (shared.section_id,)
            )
        }
        assert got == {"shared chunk", "A's note"}
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            insert_row(
                conn, "embeddings", {"owner_user_id": user_a, "ref_type": "note", "ref_id": "n2", "content": "x"}
            )
    with as_user(pg, user_b) as conn:
        got = {
            r[0]
            for r in conn.execute(
                "select content from public.embeddings where ref_id in (%s, 'n1')", (shared.section_id,)
            )
        }
        assert got == {"shared chunk"}
    with as_anon(pg) as conn:
        got = {
            r[0]
            for r in conn.execute(
                "select content from public.embeddings where ref_id in (%s, 'n1')", (shared.section_id,)
            )
        }
        assert got == {"shared chunk"}


@pytest.mark.parametrize("table", ADMIN_TABLES)
def test_admin_tables_have_no_client_access(
    pg: psycopg.Connection, user_a: uuid.UUID, shared: Shared, table: str
) -> None:
    seed_admin_rows(pg, user_a, shared)
    for ctx in (as_anon(pg), as_user(pg, user_a)):
        with ctx as conn:
            with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
                conn.execute(sql.SQL("select 1 from public.{} limit 1").format(sql.Identifier(table)))
            with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
                conn.execute(sql.SQL("delete from public.{}").format(sql.Identifier(table)))
    with as_service_role(pg) as conn:
        assert conn.execute(sql.SQL("select count(*) from public.{}").format(sql.Identifier(table))).fetchone() != (0,)


def test_alembic_version_table_is_not_exposed(pg: psycopg.Connection, user_a: uuid.UUID) -> None:
    for ctx in (as_anon(pg), as_user(pg, user_a)):
        with ctx as conn, pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("select * from public.naadi_alembic_version")


def test_app_config_history_trigger(pg: psycopg.Connection, user_a: uuid.UUID) -> None:
    key = f"test_{uuid.uuid4().hex[:8]}"
    insert_row(pg, "app_config", {"key": key, "value": Jsonb({"v": 1}), "updated_by": user_a})
    pg.execute("update public.app_config set value = '{\"v\": 2}' where key = %s", (key,))
    pg.execute("update public.app_config set value = '{\"v\": 2}' where key = %s", (key,))  # no-op: no history row
    pg.execute("delete from public.app_config where key = %s", (key,))
    rows = pg.execute(
        "select value, op, updated_by from public.app_config_history where key = %s order by id", (key,)
    ).fetchall()
    assert rows == [({"v": 1}, "update", user_a), ({"v": 2}, "delete", user_a)]


def test_updated_at_is_maintained(pg: psycopg.Connection, user_a: uuid.UUID) -> None:
    insert_row(pg, "profiles", {"user_id": user_a})
    pg.execute("update public.profiles set updated_at = now() - interval '1 day' where user_id = %s", (user_a,))
    pg.execute("update public.profiles set display_name = 'Later' where user_id = %s", (user_a,))
    row = pg.execute(
        "select updated_at > now() - interval '1 minute' from public.profiles where user_id = %s", (user_a,)
    ).fetchone()
    assert row == (True,)


def test_deleting_an_auth_user_cascades(pg: psycopg.Connection, shared: Shared) -> None:
    uid = create_auth_user(pg)
    seed_user_rows(pg, uid, shared)
    pg.execute("delete from auth.users where id = %s", (uid,))
    for t in USER_TABLES:
        assert _count(pg, t.name, uid) == 0, t.name


def test_profile_first_sign_in_is_idempotent_as_the_user(pg: psycopg.Connection, user_a: uuid.UUID) -> None:
    # The S03 `/api/me` path: create-if-missing as the user, under RLS.
    with as_user(pg, user_a) as conn:
        for _ in range(2):
            conn.execute(
                "insert into public.profiles (user_id) values ((select auth.uid())) on conflict (user_id) do nothing"
            )
        row = conn.execute("select count(*), min(exam_target), min(timezone) from public.profiles").fetchone()
        assert row == (1, "step1", "Asia/Kolkata")
