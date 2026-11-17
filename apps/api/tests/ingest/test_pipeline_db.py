"""The ingest pipeline end to end against the migrated local database (in-memory Storage, a spy model gateway).

Proves: a PDF becomes an RLS-private chapter (B sees nothing), every placement waits for a choice and Undo deletes
exactly what it wrote; a personal document stops at the gate with NO model call and moves to the vault; the "not
personal" path redacts identifiers before any model sees text; cards / own MCQs / KB entries / duplicates / bundles /
rejections; the in-process graph run with both interrupts.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import psycopg
import pytest
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import SecretStr

from naadi_api.config_api.schemas import IngestRules, StorageBuckets
from naadi_api.db import Database
from naadi_api.ingest import pipeline, routes
from naadi_api.ingest import store as st
from naadi_api.ingest.storage import MemoryStorage, upload_path

from ..db.rls import DB_MARKS, database_url, ensure_migrated
from . import files

pytestmark = DB_MARKS

BUCKETS = StorageBuckets(
    content_public="content-public",
    content_private="content-private",
    user_uploads="user-uploads",
    vault="vault",
    generated_media="generated-media",
)


class SpyGateway:
    """Records every message a model would have received; answers like the structured / vision roles."""

    def __init__(self, kind: str = "reading") -> None:
        self.kind = kind
        self.seen: list[tuple[str, list[BaseMessage]]] = []

    async def ainvoke(self, role: str, messages: list[BaseMessage], ctx: Any, **kw: Any) -> AIMessage:
        self.seen.append((role, list(messages)))
        if role == "vision":
            return AIMessage(content='{"text": "Handwritten: loop of Henle", "handwritten": true}')
        return AIMessage(content=f'{{"kind": "{self.kind}", "title": "Renal lecture", "taxonomy_ids": []}}')

    def text(self) -> str:
        out = []
        for _role, msgs in self.seen:
            for m in msgs:
                out.append(m.content if isinstance(m.content, str) else str(m.content))
        return "\n".join(out)


@pytest.fixture(scope="module")
def url() -> str:
    u = database_url()
    ensure_migrated(u)
    return u


@pytest.fixture
def users(url: str) -> Iterator[tuple[uuid.UUID, uuid.UUID]]:
    a, b = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(url, autocommit=True) as c:
        for u in (a, b):
            c.execute(
                "insert into auth.users (id, email, aud, role) values (%s, %s, 'authenticated', 'authenticated')",
                (u, f"{u.hex[:10]}@test.invalid"),
            )
    yield a, b
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("delete from public.chapters where owner_user_id = any(%s)", ([a, b],))
        c.execute("delete from auth.users where id = any(%s)", ([a, b],))


@pytest.fixture
async def db(url: str) -> AsyncIterator[Database]:
    d = Database(SecretStr(url), max_size=3)
    await d.open()
    yield d
    await d.close()


def make_deps(
    db: Database,
    storage: MemoryStorage,
    gw: SpyGateway,
    rules: IngestRules | None = None,
    flags: dict[str, bool] | None = None,
) -> pipeline.Deps:
    async def r() -> IngestRules:
        return rules or IngestRules()

    async def f(_u: uuid.UUID) -> dict[str, bool]:
        return flags or {"library": True}

    return pipeline.Deps(db=db, storage=storage, gateway=gw, buckets=BUCKETS, rules=r, flags_for=f, engine=lambda: None)


async def add_upload(
    db: Database, storage: MemoryStorage, user: uuid.UUID, data: bytes, name: str, batch: uuid.UUID | None = None
) -> uuid.UUID:
    item, upload = uuid.uuid4(), uuid.uuid4()
    path = upload_path(user, item)
    await storage.upload("user-uploads", path, data, "application/octet-stream")
    async with st.service_tx(db) as conn:
        await conn.execute(
            "insert into public.uploads (id, user_id, kind, storage_path, original_name, bytes)"
            " values (%s, %s, 'pdf', %s, %s, %s)",
            (upload, user, path, name, len(data)),
        )
        await conn.execute(
            "insert into public.ingest_items (id, user_id, upload_id, input_kind, batch_id) values (%s,%s,%s,'pdf',%s)",
            (item, user, upload, batch),
        )
    return item


def as_user(url: str, user: uuid.UUID, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    import json

    with psycopg.connect(url) as c, c.transaction(force_rollback=True):
        c.execute("set local role authenticated")
        c.execute(
            "select set_config('request.jwt.claims', %s, true), set_config('request.jwt.claim.sub', %s, true)",
            (json.dumps({"sub": str(user), "role": "authenticated"}), str(user)),
        )
        return c.execute(sql, params).fetchall()


async def item(db: Database, item_id: uuid.UUID, user: uuid.UUID) -> dict[str, Any]:
    async with st.service_tx(db) as conn:
        row = await st.get_item(conn, item_id, user)
    assert row is not None
    return row


async def test_pdf_becomes_a_private_chapter_and_undo_deletes_it(
    db: Database, users: tuple[uuid.UUID, uuid.UUID], url: str
) -> None:
    a, b = users
    storage, gw = MemoryStorage(), SpyGateway("reading")
    deps = make_deps(db, storage, gw)
    item_id = await add_upload(db, storage, a, files.pdf(3), "Renal.pdf")
    # Everything up to the proposal; nothing placed before the choice.
    for step in (pipeline.receive, pipeline.gate):
        out = await pipeline.guarded(deps, item_id, a, lambda s=step: s(deps, item_id, a))
    assert out.next == "understand"
    out = await pipeline.understand_step(deps, item_id, a)
    out = await pipeline.propose(deps, item_id, a)
    assert out.next == "ask_confirm"
    row = await item(db, item_id, a)
    assert row["status"] == "needs_confirmation" and row["sha256"] and row["input_kind"] == "pdf"
    options = row["proposed_placement"]["options"]
    assert options[0]["kind"] == "private_chapter" and options[0]["recommended"]
    assert not as_user(url, a, "select id from public.chapters where owner_user_id = %s", (a,))
    # One structured call; its prompt is bounded document text, never the PDF bytes or the file name's owner.
    assert [r for r, _ in gw.seen] == ["structured"]
    assert "%PDF" not in gw.text()

    out = await pipeline.place(deps, item_id, a, options[0]["option_id"])
    assert out.next == "index"
    await pipeline.index_step(deps, item_id, a)
    row = await item(db, item_id, a)
    assert row["status"] == "ready" and row["progress"] == 100 and row["processed_chapter_id"].startswith("u-")
    chapter = row["processed_chapter_id"]
    mine = as_user(url, a, "select kind, source_id, status from public.chapters where id = %s", (chapter,))
    assert mine == [("user_upload", "user-provided", "published")]
    assert len(as_user(url, a, "select id from public.chapter_sections where chapter_id = %s", (chapter,))) == 3
    figs = as_user(
        url, a, "select kind, review_status, owner_user_id from public.media_assets where id like %s", (chapter + "-%",)
    )
    assert sorted(f[0] for f in figs) == ["image", "pdf"] and all(f[1] == "approved" and f[2] == a for f in figs)
    # User B sees none of it.
    assert as_user(url, b, "select id from public.chapters where id = %s", (chapter,)) == []
    assert as_user(url, b, "select id from public.chapter_sections where chapter_id = %s", (chapter,)) == []
    assert as_user(url, b, "select id from public.media_assets where id like %s", (chapter + "-%",)) == []
    assert as_user(url, b, "select id from public.ingest_items where id = %s", (item_id,)) == []
    # Undo deletes exactly what was written and the item waits for a choice again.
    records = await pipeline.undo(deps, item_id, a)
    assert {r["table"] for r in records} == {"chapters", "media_assets"}
    assert as_user(url, a, "select id from public.chapters where id = %s", (chapter,)) == []
    assert as_user(url, a, "select id from public.media_assets where id like %s", (chapter + "-%",)) == []
    row = await item(db, item_id, a)
    assert row["status"] == "needs_confirmation" and row["placements"] == [] and row["processed_chapter_id"] is None
    # ... and can be placed again (as a KB entry this time).
    kb = next(o for o in row["proposed_placement"]["options"] if o["kind"] == "kb_entry")
    assert (await pipeline.place(deps, item_id, a, kb["option_id"])).next == "index"
    assert as_user(url, a, "select kind, status from public.kb_sources where ingest_item_id = %s", (item_id,)) == [
        ("file", "ready")
    ]


async def test_a_personal_document_reaches_no_model_and_goes_to_the_vault(
    db: Database, users: tuple[uuid.UUID, uuid.UUID], url: str
) -> None:
    a, _ = users
    storage, gw = MemoryStorage(), SpyGateway()
    deps = make_deps(db, storage, gw)
    passport = files.pdf(1, text="", extra=files.passport_text(), image=False)
    item_id = await add_upload(db, storage, a, passport, "scan.pdf")
    await pipeline.receive(deps, item_id, a)
    out = await pipeline.gate(deps, item_id, a)
    assert out.next == "ask_vault" and "passport_mrz" in out.detail["signals"]
    row = await item(db, item_id, a)
    assert row["status"] == "needs_confirmation" and row["sensitivity"] == "personal"
    out = await pipeline.vault(deps, item_id, a, "vault")
    assert out.detail == {"vaulted": True}
    assert gw.seen == []  # not one model call
    row = await item(db, item_id, a)
    assert row["status"] == "vaulted" and row["storage_path"] is None
    docs = as_user(url, a, "select id, doc_type, storage_path, mime from public.vault_documents")
    assert len(docs) == 1 and docs[0][1:] == ("passport", f"{a}/{docs[0][0]}", "application/pdf")
    assert [k for k in storage.objects if k[0] == "vault"] == [("vault", f"{a}/{docs[0][0]}")]
    assert not [k for k in storage.objects if k[0] == "user-uploads"]
    async with st.service_tx(db) as conn:
        assert await st.load_extract(conn, item_id, a) is None
        emb = await st.rows(conn, "select 1 from public.embeddings where owner_user_id = %s", (a,))
    assert emb == []


async def test_not_personal_redacts_identifiers_before_any_model_call(
    db: Database, users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, _ = users
    storage, gw = MemoryStorage(), SpyGateway()
    deps = make_deps(db, storage, gw)
    _l1, l2 = files.mrz_td3()
    aad = files.aadhaar()
    item_id = await add_upload(db, storage, a, files.pdf(2, extra=f" Aadhaar {aad} " + files.passport_text()), "x.pdf")
    await pipeline.receive(deps, item_id, a)
    assert (await pipeline.gate(deps, item_id, a)).next == "ask_vault"
    assert (await pipeline.vault(deps, item_id, a, "continue")).next == "extract"
    assert (await pipeline.extract(deps, item_id, a)).next == "understand"
    await pipeline.understand_step(deps, item_id, a)
    seen = gw.text()
    assert seen and aad not in seen and l2 not in seen
    async with st.service_tx(db) as conn:
        doc = await st.load_extract(conn, item_id, a)
    assert doc is not None and aad not in doc.full_text() and "[id]" in doc.full_text()


async def test_cards_questions_and_paste(db: Database, users: tuple[uuid.UUID, uuid.UUID], url: str) -> None:
    a, b = users
    storage = MemoryStorage()
    deps = make_deps(db, storage, SpyGateway("flashcard_deck"))
    cards = await add_upload(db, storage, a, files.csv_qa(), "deck.csv")
    out = await pipeline.run_all(deps, cards, a)
    assert out.next == "end"
    row = await item(db, cards, a)
    assert row["status"] == "ready" and row["confirmed_placement"]["kind"] == "flashcards"
    assert as_user(url, a, "select count(*) from public.flashcards where owner_user_id = %s", (a,)) == [(10,)]
    assert as_user(url, b, "select count(*) from public.flashcards where owner_user_id = %s", (a,)) == [(0,)]

    deps = make_deps(db, storage, SpyGateway("question_set"))
    mcq = await add_upload(db, storage, a, files.mcq_text(), "own-questions.txt")
    await pipeline.run_all(deps, mcq, a)
    row = await item(db, mcq, a)
    assert row["confirmed_placement"]["kind"] == "private_questions"
    assert as_user(url, a, "select count(*) from public.questions where owner_user_id = %s", (a,)) == [(3,)]
    assert as_user(url, b, "select count(*) from public.questions where owner_user_id = %s", (a,)) == [(0,)]
    # The answer and explanation of the student's own questions stay behind the same attempt gate as shared ones.
    for col in ("answer", "explanation_html"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            as_user(url, a, f"select {col} from public.questions where owner_user_id = %s", (a,))  # noqa: S608
    stored = as_user(url, a, "select id from public.questions where owner_user_id = %s", (a,))
    async with st.service_tx(db) as conn:
        expl = await st.rows(conn, "select explanation_html from public.questions where id = %s", (stored[0][0],))
    assert "two thirds" in (expl[0]["explanation_html"] or "")  # written, for the Qbank to show after an attempt
    # Neither the proposal nor the item the Library API returns carries an answer letter or the explanation.
    out = routes.item_out(row).model_dump_json()
    for text in (str(row["proposed_placement"]), out):
        assert "Answer" not in text and "'A'" not in text and "two thirds" not in text
    await pipeline.undo(deps, mcq, a)
    assert as_user(url, a, "select count(*) from public.questions where owner_user_id = %s", (a,)) == [(0,)]


async def test_exact_duplicate_bundles_and_rejections(db: Database, users: tuple[uuid.UUID, uuid.UUID]) -> None:
    a, _ = users
    storage = MemoryStorage()
    started: list[uuid.UUID] = []

    async def start_child(child: uuid.UUID, _u: uuid.UUID) -> None:
        started.append(child)

    deps = make_deps(db, storage, SpyGateway())
    deps.start_child = start_child
    data = files.pdf(2)
    first = await add_upload(db, storage, a, data, "a.pdf")
    await pipeline.receive(deps, first, a)
    second = await add_upload(db, storage, a, data, "copy of a.pdf")
    out = await pipeline.receive(deps, second, a)
    assert out.detail == {"duplicate_of": str(first)}
    row = await item(db, second, a)
    assert row["status"] == "ready" and row["duplicate_of"] == first and row["storage_path"] is None

    bundle = files.zip_bundle({"x/one.pdf": files.pdf(1), "two.csv": files.csv_qa(), "evil.exe": b"MZ" + b"\0" * 99})
    zip_item = await add_upload(db, storage, a, bundle, "lecture.zip")
    out = await pipeline.receive(deps, zip_item, a)
    assert len(started) == 2 and out.detail["children"] == [str(c) for c in started]
    row = await item(db, zip_item, a)
    assert row["stage_detail"]["bundle"] == {"files": 2, "skipped": 1}
    child = await item(db, started[0], a)
    assert child["batch_id"] == zip_item and child["original_name"] == "one.pdf"

    exe = await add_upload(db, storage, a, b"MZ" + b"\0" * 300, "lecture.pdf")
    await pipeline.run_all(deps, exe, a)
    row = await item(db, exe, a)
    assert (row["status"], row["error"]) == ("failed", "executable_rejected")
    bomb = await add_upload(db, storage, a, files.zip_bomb(), "notes.zip")
    await pipeline.run_all(deps, bomb, a)
    assert (await item(db, bomb, a))["error"] == "zip_bomb"


async def test_the_graph_runs_in_process_with_both_interrupts(
    db: Database, users: tuple[uuid.UUID, uuid.UUID], monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    from naadi_api.ingest import deps as deps_mod
    from naadi_api.ingest.runner import InProcessRunner

    a, _ = users
    storage, gw = MemoryStorage(), SpyGateway("reading")
    d = make_deps(db, storage, gw)

    async def fake_build(_config: Any) -> pipeline.Deps:
        return d

    monkeypatch.setattr("naadi_api.agent.graphs.ingest.build_deps", fake_build)
    _ = deps_mod
    runner = InProcessRunner()
    item_id = await add_upload(db, storage, a, files.pdf(2), "graph.pdf")
    thread, _run = await runner.start(a, False, item_id, {"item_id": str(item_id), "mode": "start"}, db)
    await runner.drain()
    row = await item(db, item_id, a)
    assert row["status"] == "needs_confirmation" and row["thread_id"] == thread
    option = row["proposed_placement"]["options"][0]["option_id"]
    await runner.resume(a, False, item_id, thread, {"option_id": option}, db)
    await runner.drain()
    row = await item(db, item_id, a)
    assert row["status"] == "ready" and row["placements"]

    vault_item = await add_upload(
        db, storage, a, files.pdf(1, text="", extra=files.passport_text(), image=False), "p.pdf"
    )
    thread, _run = await runner.start(a, False, vault_item, {"item_id": str(vault_item), "mode": "start"}, db)
    await runner.drain()
    assert (await item(db, vault_item, a))["sensitivity"] == "personal"
    calls = len(gw.seen)
    await runner.resume(a, False, vault_item, thread, {"decision": "vault"}, db)
    await runner.drain()
    assert (await item(db, vault_item, a))["status"] == "vaulted" and len(gw.seen) == calls
