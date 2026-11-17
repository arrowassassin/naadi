"""S15's tutor tools against the local Postgres: `add_to_knowledge` through the real confirm interrupt (link and
uploaded file; cancel writes nothing, accept only starts the run) and `get_page` on `pdf_page` (the student's own,
gated PDF only; never another student's, never a file before its sensitivity gate, never a personal page).

    apps/api/tests/db/stack.sh up && DATABASE_URL=... uv run pytest tests/ingest -m db
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from naadi_api.agent.tools import knowledge, tools_for
from naadi_api.agent.tools.executor import run_tool_call
from naadi_api.agent.tools.registry import PageRef, ToolContext, web_url_key
from naadi_api.db import Database
from naadi_api.ingest import pipeline
from naadi_api.ingest import routes as ingest_routes
from naadi_api.ingest import runner as ingest_runner
from naadi_api.ingest import store as st
from naadi_api.ingest.storage import MemoryStorage
from naadi_api.llm import CallContext

from ..assistant.test_tools_db import flow
from ..db.rls import DB_MARKS
from . import files
from . import test_pipeline_db as _pipeline
from .test_pipeline_db import SpyGateway, add_upload, make_deps

pytestmark = DB_MARKS
# The pipeline tests' fixtures (database, two students, migrated URL).
url, users, db = _pipeline.url, _pipeline.users, _pipeline.db
ON = {"library": True}


class RecordingRunner:
    def __init__(self) -> None:
        self.started: list[tuple[uuid.UUID, uuid.UUID, dict[str, Any]]] = []

    async def start(
        self, user_id: uuid.UUID, is_admin: bool, item_id: uuid.UUID, payload: dict[str, Any], db: Database
    ) -> tuple[str, str]:
        self.started.append((user_id, item_id, payload))
        return "run", "thread"

    async def resume(self, *a: Any, **k: Any) -> tuple[str, str]:
        raise AssertionError("the tool never resumes a run")


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> MemoryStorage:
    mem = MemoryStorage()
    monkeypatch.setattr(ingest_routes, "get_storage", lambda _settings: mem)
    return mem


@pytest.fixture
def runner() -> Iterator[RecordingRunner]:
    r = RecordingRunner()
    ingest_runner.set_runner(r)  # type: ignore[arg-type]
    yield r
    ingest_runner.set_runner(None)


def tc(
    db: Database,
    user: uuid.UUID,
    flags: dict[str, bool] | None = None,
    page: PageRef | None = None,
    urls: tuple[str, ...] = (),
) -> ToolContext:
    return ToolContext(
        user_id=user,
        is_admin=False,
        run_id="test",
        thread_id=None,
        db=db,
        exam_active=False,
        page=page or PageRef(),
        gateway=None,  # type: ignore[arg-type]  # these tools call no model
        call=CallContext(purpose="chat"),
        flags=ON if flags is None else flags,
        web_urls={web_url_key(u) for u in urls},
    )


def rows(url: str, sql: str, *params: Any) -> list[tuple[Any, ...]]:
    with psycopg.connect(url) as c:
        return c.execute(sql, params).fetchall()


async def upload_id_of(db: Database, item_id: uuid.UUID) -> uuid.UUID:
    async with st.service_tx(db) as conn:
        row = await st.one(conn, "select upload_id from public.ingest_items where id = %s", (item_id,))
    assert row is not None
    return uuid.UUID(str(row["upload_id"]))


def test_the_tools_exist_only_while_the_library_flag_is_on() -> None:
    assert "add_to_knowledge" in {t.name for t in tools_for(ON)}
    assert "add_to_knowledge" not in {t.name for t in tools_for({})}


async def test_add_a_link_cancel_writes_nothing_accept_starts_the_run(
    db: Database, users: tuple[uuid.UUID, uuid.UUID], url: str, storage: MemoryStorage, runner: RecordingRunner
) -> None:
    a, _ = users
    link = "https://example.org/renal/acid-base-review"
    # A link the student did not type this turn (e.g. written inside a document) is refused before any confirm.
    r = await run_tool_call(tc(db, a), "add_to_knowledge", {"url": link}, {"add_to_knowledge"})
    assert r.content == "[refused: url_not_from_student]" and not runner.started
    preview, out = await flow(tc(db, a, urls=(link,)), "add_to_knowledge", {"url": link}, {"decision": "cancel"})
    assert preview == {"kind": "knowledge", "source": "link", "label": link, "host": "example.org"}
    assert out["status"] == "declined" and not runner.started
    assert rows(url, "select id from public.ingest_items where user_id = %s", a) == []

    _, out = await flow(tc(db, a, urls=(link,)), "add_to_knowledge", {"url": link}, {"decision": "accept"})
    assert out["status"] == "ok" and "Library" in out["content"]
    assert out["blocks"][0]["data"]["items"][0]["action"] == "open_library"
    made = rows(
        url,
        "select i.id, i.input_kind, i.status, k.url from public.ingest_items i"
        " join public.kb_sources k on k.id = i.kb_source_id where i.user_id = %s",
        a,
    )
    assert [(m[1], m[2], m[3]) for m in made] == [("link", "received", link)]
    assert runner.started == [(a, made[0][0], {"item_id": str(made[0][0]), "mode": "start"})]

    # Arguments are checked before any confirm: exactly one of url / upload_id, https only.
    for bad in ({}, {"url": "http://example.org/x"}, {"url": link, "upload_id": str(uuid.uuid4())}):
        r = await run_tool_call(tc(db, a), "add_to_knowledge", bad, {"add_to_knowledge"})
        assert r.status == "error" and r.content.startswith("[error: invalid_arguments")


async def test_add_an_uploaded_file_only_the_students_own_and_only_once(
    db: Database, users: tuple[uuid.UUID, uuid.UUID], url: str, storage: MemoryStorage, runner: RecordingRunner
) -> None:
    a, b = users
    item_a = await add_upload(db, storage, a, files.pdf(2), "Acid-base lecture.pdf")
    item_b = await add_upload(db, storage, b, files.pdf(2), "B private.pdf")
    up_a, up_b = await upload_id_of(db, item_a), await upload_id_of(db, item_b)

    # B's file is invisible to A's run: refused before any confirm, nothing starts.
    r = await run_tool_call(tc(db, a), "add_to_knowledge", {"upload_id": str(up_b)}, {"add_to_knowledge"})
    assert r.status == "refused" and r.content == "[refused: upload_not_found]" and "B private" not in r.content

    preview, out = await flow(tc(db, a), "add_to_knowledge", {"upload_id": str(up_a)}, {"decision": "accept"})
    assert preview == {"kind": "knowledge", "source": "file", "label": "Acid-base lecture.pdf", "host": None}
    assert out["status"] == "ok" and runner.started == [(a, item_a, {"item_id": str(item_a), "mode": "start"})]

    # Once the run has started (run_id set), the file cannot be added again.
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("update public.ingest_items set run_id = 'r1' where id = %s", (item_a,))
    r = await run_tool_call(tc(db, a), "add_to_knowledge", {"upload_id": str(up_a)}, {"add_to_knowledge"})
    assert r.content == "[refused: already_added]" and len(runner.started) == 1

    # A vaulted file is never offered.
    item_v = await add_upload(db, storage, a, files.pdf(1), "Passport scan.pdf")
    up_v = await upload_id_of(db, item_v)
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("update public.uploads set status = 'vaulted' where id = %s", (up_v,))
    r = await run_tool_call(tc(db, a), "add_to_knowledge", {"upload_id": str(up_v)}, {"add_to_knowledge"})
    assert r.content == "[refused: upload_not_found]" and "Passport" not in r.content


async def test_get_page_reads_a_gated_page_of_the_students_own_pdf(
    db: Database, users: tuple[uuid.UUID, uuid.UUID], url: str, storage: MemoryStorage
) -> None:
    a, b = users
    deps = make_deps(db, storage, SpyGateway("reading"))
    item_id = await add_upload(db, storage, a, files.pdf(3), "Renal.pdf")
    up = await upload_id_of(db, item_id)
    ref = f"{up}:2"

    # Before the sensitivity gate has run, the page never reaches a model.
    r = await run_tool_call(tc(db, a), "get_page", {"type": "pdf_page", "id": ref}, {"get_page"})
    assert r.content == "[refused: not_processed]"

    for step in (pipeline.receive, pipeline.gate):
        await pipeline.guarded(deps, item_id, a, lambda s=step: s(deps, item_id, a))
    await pipeline.understand_step(deps, item_id, a)
    await pipeline.propose(deps, item_id, a)

    r = await run_tool_call(tc(db, a), "get_page", {"type": "pdf_page", "id": ref}, {"get_page"})
    assert r.status == "ok" and r.content.startswith("PDF page 2 of 3:") and "Section heading 2" in r.content
    assert r.citations[0]["target"] == {"type": "pdf_page", "upload_id": str(up), "page": "2"}
    assert r.citations[0]["layer"] == "notes" and r.citations[0]["title"] == "Renal.pdf"
    # The page the student is on, as `current`.
    page = PageRef(type="pdf_page", id=f"{up}:1")
    r = await knowledge.get_page(tc(db, a, page=page), knowledge.PageArgs(type="current"))
    assert r.content.startswith("PDF page 1 of 3:")

    # Another student, a page out of range, a malformed id, or the flag off: refused.
    for who, rid, flags in ((b, ref, ON), (a, f"{up}:9", ON), (a, "nope", ON), (a, ref, {})):
        r = await run_tool_call(tc(db, who, flags), "get_page", {"type": "pdf_page", "id": rid}, {"get_page"})
        assert r.status == "refused", (who, rid, flags, r.content)
        assert "Section heading" not in r.content


async def test_get_page_withholds_a_page_with_a_personal_document_signal(
    db: Database, users: tuple[uuid.UUID, uuid.UUID], url: str, storage: MemoryStorage
) -> None:
    a, _ = users
    l1, l2 = files.mrz_td3()
    item_id = await add_upload(db, storage, a, files.pdf(2, extra=f"{l1}\n{l2}"), "Scan.pdf")
    up = await upload_id_of(db, item_id)
    # As if the student chose "continue" at the Vault prompt: past the gate, no extract stored.
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("update public.ingest_items set status = 'needs_confirmation' where id = %s", (item_id,))
    r = await run_tool_call(tc(db, a), "get_page", {"type": "pdf_page", "id": f"{up}:1"}, {"get_page"})
    assert r.content == "[refused: sensitive_page]" and l1 not in r.content
