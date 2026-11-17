"""Every tutor tool against the real local Postgres (committed rows, cleaned up): each write tool through the real
confirm interrupt (accept / edit / cancel), tools acting only as the run's user, chapter edits (admin → revision,
everyone else → overlay note) and the question reveal gate.

    apps/api/tests/db/stack.sh up && DATABASE_URL=... uv run pytest tests/assistant -m db
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any, TypedDict

import psycopg
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command
from psycopg.types.json import Jsonb

from naadi_api.agent.tools import all_tools, get_tool
from naadi_api.agent.tools.executor import run_tool_call
from naadi_api.agent.tools.registry import PageRef, ToolContext
from naadi_api.assistant.questions import HIDDEN_FIELDS
from naadi_api.assistant.store_index import hashed_vector
from naadi_api.db import Database
from naadi_api.llm import CallContext

pytestmark = [pytest.mark.integration, pytest.mark.db, pytest.mark.asyncio]

ANSWER_SECRET = "SECRET-EXPLANATION-"


@dataclass
class World:
    tag: str
    a: uuid.UUID
    b: uuid.UUID
    chapter: str
    section: str
    q_revealed: str
    q_hidden: str
    b_note: uuid.UUID
    a_note: uuid.UUID


@pytest.fixture(scope="module")
def url() -> str:
    from tests.db.rls import database_url, ensure_migrated, require_local

    u = database_url()
    require_local(u)
    ensure_migrated(u)
    return u


@pytest.fixture
def pg(url: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(url, autocommit=True) as conn:
        yield conn


def _para(t: str) -> dict[str, Any]:
    return {"type": "paragraph", "content": [{"type": "text", "text": t}]}


@pytest.fixture
def world(pg: psycopg.Connection[Any]) -> Iterator[World]:
    from tests.db.fixtures import insert_row
    from tests.db.rls import create_auth_user, delete_auth_user

    tag = uuid.uuid4().hex[:8]
    a, b = create_auth_user(pg), create_auth_user(pg)
    w = World(tag, a, b, f"t{tag}-ach", f"t{tag}-ach-s1", f"t{tag}-aq1", f"t{tag}-aq2", uuid.uuid4(), uuid.uuid4())
    src = f"t{tag}-asrc"
    insert_row(
        pg, "sources", {"id": src, "name": "Naadi original", "license": "proprietary-naadi", "allow_ai_ingest": True}
    )
    insert_row(
        pg, "chapters", {"id": w.chapter, "exam": "step1", "kind": "chapter", "title": "Renal", "source_id": src}
    )
    insert_row(
        pg,
        "chapter_sections",
        {
            "id": w.section,
            "chapter_id": w.chapter,
            "sort": 1,
            "heading": "Acid-base",
            "body": Jsonb({"type": "doc", "content": [_para("one"), _para("two"), _para("three")]}),
        },
    )
    for qid in (w.q_revealed, w.q_hidden):
        insert_row(
            pg,
            "questions",
            {
                "id": qid,
                "exam": "step1",
                "chapter_id": w.chapter,
                "source_id": src,
                "stem_html": "<p>A 24-year-old with a low bicarbonate…</p>",
                "options": Jsonb([{"label": "A", "html": "RTA 1"}, {"label": "B", "html": "RTA 2"}]),
                "answer": "B",
                "answer_html": f"<p>{ANSWER_SECRET}answer</p>",
                "explanation_html": f"<p>{ANSWER_SECRET}{qid}</p>",
                "why_not_html": f"<p>{ANSWER_SECRET}why-not</p>",
                "educational_objective": f"{ANSWER_SECRET}objective",
                "status": "published",
            },
        )
    pg.execute(
        "insert into public.attempts (user_id, question_id, chosen, correct, mode)"
        " values (%s, %s, 'A', false, 'tutor')",
        (a, w.q_revealed),
    )
    pg.execute(
        "insert into public.notes (id, user_id, anchor_type, body) values (%s, %s, 'free', %s), (%s, %s, 'free', %s)",
        (
            w.b_note,
            b,
            Jsonb({"type": "doc", "content": [_para("B private")]}),
            w.a_note,
            a,
            Jsonb({"type": "doc", "content": [_para("A note")]}),
        ),
    )
    yield w
    pg.execute("delete from public.blocks where user_id = any(%s)", ([a, b],))
    pg.execute("delete from public.chapter_section_revisions where section_id = %s", (w.section,))
    pg.execute("delete from public.attempts where user_id = any(%s)", ([a, b],))
    pg.execute("delete from public.questions where id = any(%s)", ([w.q_revealed, w.q_hidden],))
    pg.execute("delete from public.chapters where id = %s", (w.chapter,))
    pg.execute("delete from public.sources where id = %s", (src,))
    for u in (a, b):
        delete_auth_user(pg, u)


@pytest.fixture
async def db(url: str) -> AsyncIterator[Database]:
    from pydantic import SecretStr

    d = Database(SecretStr(url), max_size=2)
    await d.open()
    yield d
    await d.close()


def ctx(
    db: Database, user: uuid.UUID, *, admin: bool = False, page: PageRef | None = None, store: Any = None
) -> ToolContext:
    return ToolContext(
        user_id=user,
        is_admin=admin,
        run_id="test",
        thread_id=None,
        db=db,
        exam_active=False,
        page=page or PageRef(),
        gateway=None,  # type: ignore[arg-type]  # no tool here calls a model
        call=CallContext(purpose="chat"),
        flags={},
        store=store,
    )


class S(TypedDict, total=False):
    out: dict[str, Any]


def confirm_graph(tc: ToolContext, name: str, args: dict[str, Any]) -> Any:
    async def node(state: S) -> S:
        r = await run_tool_call(tc, name, args, {t.name for t in all_tools()})
        return {"out": {"status": r.status, "content": r.content, "blocks": r.blocks}}

    g: StateGraph[S] = StateGraph(S)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    return g.compile(checkpointer=InMemorySaver())


async def flow(
    tc: ToolContext, name: str, args: dict[str, Any], decision: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    g = confirm_graph(tc, name, args)
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex}}
    first = await g.ainvoke({}, cfg)
    assert "__interrupt__" in first, f"{name} did not ask for a confirm"
    preview = first["__interrupt__"][0].value["preview"]
    for key in HIDDEN_FIELDS:
        assert key not in str(preview)
    done = await g.ainvoke(Command(resume=decision), cfg)
    return preview, done["out"]


def count(pg: psycopg.Connection[Any], sql: str, *params: Any) -> int:
    row = pg.execute(sql, params).fetchone()
    return int(row[0]) if row else 0


DOC = {"type": "doc", "content": [_para("edited by the student")]}


async def test_save_note_accept_edit_cancel(pg: psycopg.Connection[Any], world: World, db: Database) -> None:
    page = PageRef(type="section", id=world.section, title="Acid-base")
    tc = ctx(db, world.a, page=page)
    notes = "select count(*) from public.notes where user_id = %s and anchor_type = 'section' and anchor_id = %s"
    preview, out = await flow(tc, "save_note", {"markdown": "Winters: 1.5 x HCO3 + 8"}, {"decision": "cancel"})
    assert preview["kind"] == "note" and preview["anchor"]["id"] == world.section
    assert out["status"] == "declined" and count(pg, notes, world.a, world.section) == 0
    _, out = await flow(tc, "save_note", {"markdown": "Winters"}, {"decision": "accept"})
    assert out["status"] == "ok" and count(pg, notes, world.a, world.section) == 1
    _, out = await flow(tc, "save_note", {"markdown": "draft"}, {"decision": "edit", "args": {"doc": DOC}})
    row = pg.execute(
        "select body from public.notes where user_id = %s and anchor_id = %s order by created_at desc limit 1",
        (world.a, world.section),
    ).fetchone()
    assert row is not None and "edited by the student" in str(row[0])


async def test_append_and_update_note_only_on_own_notes(
    pg: psycopg.Connection[Any], world: World, db: Database
) -> None:
    tc = ctx(db, world.a)
    _, out = await flow(
        tc, "append_to_note", {"note_id": str(world.a_note), "markdown": "more"}, {"decision": "accept"}
    )
    body = str(pg.execute("select body from public.notes where id = %s", (world.a_note,)).fetchone())
    assert out["status"] == "ok" and "A note" in body and "more" in body
    _, out = await flow(tc, "update_note", {"note_id": str(world.a_note), "markdown": "x"}, {"decision": "cancel"})
    assert "more" in str(pg.execute("select body from public.notes where id = %s", (world.a_note,)).fetchone())
    _, out = await flow(tc, "update_note", {"note_id": str(world.a_note), "markdown": "x"}, {"decision": "accept"})
    assert "more" not in str(pg.execute("select body from public.notes where id = %s", (world.a_note,)).fetchone())
    # B's note is invisible to A's run: refused before any confirm, nothing changes.
    r = await run_tool_call(tc, "append_to_note", {"note_id": str(world.b_note), "markdown": "pwn"}, {"append_to_note"})
    assert r.status == "refused"
    assert "pwn" not in str(pg.execute("select body from public.notes where id = %s", (world.b_note,)).fetchone())


async def test_make_flashcards_accept_edit_cancel(pg: psycopg.Connection[Any], world: World, db: Database) -> None:
    page = PageRef(type="question", question_id=world.q_revealed, question_revealed=True)
    tc = ctx(db, world.a, page=page)
    mine = "select count(*) from public.flashcards where owner_user_id = %s"
    cards = {"cards": [{"front": "Type 1 RTA urine pH?", "back": "> 5.5"}]}
    await flow(tc, "make_flashcards", cards, {"decision": "cancel"})
    assert count(pg, mine, world.a) == 0
    _, out = await flow(tc, "make_flashcards", cards, {"decision": "accept"})
    assert out["status"] == "ok" and count(pg, mine, world.a) == 1
    assert any(b["type"] == "card_preview" for b in out["blocks"])
    linked = count(
        pg,
        "select count(*) from public.flashcards where owner_user_id = %s and question_id = %s",
        world.a,
        world.q_revealed,
    )
    assert linked == 1, "a card made on an answered question links to it"
    edited = {"cards": [{"front": "Edited front", "back": "Edited back"}, {"front": "Two", "back": "2"}]}
    await flow(tc, "make_flashcards", cards, {"decision": "edit", "args": edited})
    assert count(pg, mine, world.a) == 3
    pg.execute("delete from public.flashcards where owner_user_id = %s", (world.a,))


async def test_build_block_accept_and_cancel(pg: psycopg.Connection[Any], world: World, db: Database) -> None:
    tc = ctx(db, world.a)
    blocks = "select count(*) from public.blocks where user_id = %s"
    preview, out = await flow(tc, "build_block", {"count": 1, "status": "all"}, {"decision": "cancel"})
    assert preview["kind"] == "block" and preview["mode"] == "tutor" and count(pg, blocks, world.a) == 0
    _, out = await flow(tc, "build_block", {"count": 1, "status": "all"}, {"decision": "accept"})
    assert out["status"] == "ok" and count(pg, blocks, world.a) == 1
    mode = pg.execute("select config->>'mode' from public.blocks where user_id = %s", (world.a,)).fetchone()
    assert mode == ("tutor",)
    _, out = await flow(tc, "build_block", {"count": 1, "status": "all"}, {"decision": "edit", "args": {"count": 5}})
    assert out["status"] == "declined", "a block is not editable from the chip"


async def test_add_to_plan_accept_edit_cancel(pg: psycopg.Connection[Any], world: World, db: Database) -> None:
    import datetime as dt

    tc = ctx(db, world.a)
    day = (dt.date.today() + dt.timedelta(days=2)).isoformat()
    items = "select title, origin from public.plan_items where user_id = %s"
    await flow(tc, "add_to_plan", {"day": day, "title": "Renal review"}, {"decision": "cancel"})
    assert pg.execute(items, (world.a,)).fetchall() == []
    await flow(
        tc, "add_to_plan", {"day": day, "title": "Renal review"}, {"decision": "edit", "args": {"title": "Acid-base"}}
    )
    assert pg.execute(items, (world.a,)).fetchall() == [("Acid-base", "assistant")]


async def test_chapter_edit_non_admin_writes_an_overlay_note_not_a_revision(
    pg: psycopg.Connection[Any], world: World, db: Database
) -> None:
    tc = ctx(db, world.a)
    args = {"section_id": world.section, "ops": [{"op": "replace", "index": 1, "markdown": "TWO, revised"}]}
    preview, _ = await flow(tc, "edit_chapter_section", args, {"decision": "accept"})
    assert preview["kind"] == "chapter_diff" and preview["target"] == "overlay_note"
    assert preview["ops"][0]["before"]["content"][0]["text"] == "two"
    assert count(pg, "select count(*) from public.chapter_section_revisions where section_id = %s", world.section) == 0
    body = str(pg.execute("select body from public.chapter_sections where id = %s", (world.section,)).fetchone())
    assert "TWO, revised" not in body
    overlay = pg.execute(
        "select body from public.notes where user_id = %s and kind = 'note' and anchor_type = 'section'"
        " and anchor_id = %s",
        (world.a, world.section),
    ).fetchone()
    assert overlay is not None and "Suggested edit" in str(overlay[0]) and "TWO, revised" in str(overlay[0])


async def test_chapter_edit_admin_writes_a_revision_and_updates_the_section(
    pg: psycopg.Connection[Any], world: World, db: Database
) -> None:
    tc = ctx(db, world.a, admin=True)
    args = {
        "section_id": world.section,
        "ops": [{"op": "delete", "index": 0}, {"op": "insert_after", "index": 2, "markdown": "four"}],
    }
    await flow(tc, "edit_chapter_section", args, {"decision": "cancel"})
    assert count(pg, "select count(*) from public.chapter_section_revisions where section_id = %s", world.section) == 0
    preview, out = await flow(tc, "edit_chapter_section", args, {"decision": "accept"})
    assert preview["target"] == "revision" and out["status"] == "ok"
    rev = pg.execute(
        "select origin, author_user_id, body from public.chapter_section_revisions where section_id = %s",
        (world.section,),
    ).fetchone()
    assert rev is not None and rev[0] == "assistant" and rev[1] == world.a and "one" in str(rev[2])
    body, edited = pg.execute(
        "select body, admin_edited_at is not null from public.chapter_sections where id = %s", (world.section,)
    ).fetchone()
    texts = [n["content"][0]["text"] for n in body["content"]]
    assert texts == ["two", "three", "four"] and edited


async def test_chapter_edit_rejects_media_nodes_and_bad_indexes(world: World, db: Database) -> None:
    tc = ctx(db, world.a, admin=True)
    bad_node = {
        "section_id": world.section,
        "ops": [{"op": "replace", "index": 0, "node": {"type": "figure", "attrs": {"mediaId": "x"}}}],
    }
    assert (await run_tool_call(tc, "edit_chapter_section", bad_node, {"edit_chapter_section"})).status == "refused"
    bad_index = {"section_id": world.section, "ops": [{"op": "delete", "index": 9}]}
    assert (await run_tool_call(tc, "edit_chapter_section", bad_index, {"edit_chapter_section"})).status == "refused"


async def test_remember_and_forget_go_through_a_confirm(world: World, db: Database) -> None:
    store = InMemoryStore(index={"dims": 384, "embed": lambda ts: [hashed_vector(t) for t in ts]})
    tc = ctx(db, world.a, store=store)
    ns = ("memories", str(world.a))
    await flow(tc, "remember", {"fact": "Weak at acid-base"}, {"decision": "cancel"})
    assert await store.asearch(ns) == []
    preview, _ = await flow(tc, "remember", {"fact": "Weak at acid-base, mail me at x@y.org"}, {"decision": "accept"})
    assert "[email]" in preview["fact"]
    items = await store.asearch(ns)
    assert len(items) == 1 and "x@y.org" not in str(items[0].value)
    await flow(tc, "forget", {"key": items[0].key}, {"decision": "accept"})
    assert await store.asearch(ns) == []


async def test_get_page_withholds_an_unanswered_question(world: World, db: Database) -> None:
    tc = ctx(db, world.a)
    hidden = await run_tool_call(tc, "get_page", {"type": "question", "id": world.q_hidden}, {"get_page"})
    assert ANSWER_SECRET not in hidden.content and "NOT answered" in hidden.content
    revealed = await run_tool_call(tc, "get_page", {"type": "question", "id": world.q_revealed}, {"get_page"})
    assert ANSWER_SECRET in revealed.content and "chose A" in revealed.content
    # B never attempted it: the same question stays hidden for B.
    other = await run_tool_call(
        ctx(db, world.b), "get_page", {"type": "question", "id": world.q_revealed}, {"get_page"}
    )
    assert ANSWER_SECRET not in other.content


async def test_get_page_reads_only_the_runs_own_notes(world: World, db: Database) -> None:
    r = await run_tool_call(ctx(db, world.a), "get_page", {"type": "note", "id": str(world.b_note)}, {"get_page"})
    assert r.status == "refused" and "B private" not in r.content


async def test_every_write_tool_has_a_confirm_flow_test() -> None:
    covered = {
        "save_note",
        "append_to_note",
        "update_note",
        "make_flashcards",
        "build_block",
        "add_to_plan",
        "edit_chapter_section",
        "remember",
        "forget",
        "add_to_knowledge",  # S15: tests/ingest/test_tutor_tools_db.py (link and uploaded file)
    }
    assert {t.name for t in all_tools() if t.writes} == covered
    assert get_tool("save_note") is not None
