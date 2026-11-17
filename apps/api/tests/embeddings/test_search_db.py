"""Integration (real Postgres, migrated schema, real fastembed models): what the index holds, who can find
what, and that nothing answer-bearing can reach a client.

Every test builds its own rows under a fresh tag (distinctive nonsense words, so seeded content in the same
database never changes an expectation) and removes them afterwards. The models come from the fastembed cache
(`FASTEMBED_CACHE_PATH`; `python -m naadi_api.embeddings prefetch` fills it, CI does that in the db job).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from pydantic import SecretStr

from naadi_api.auth import TokenVerifier
from naadi_api.db import Database
from naadi_api.embeddings.engine import Engine
from naadi_api.embeddings.index import reconcile
from naadi_api.main import create_app
from naadi_api.search import SearchAnswer, SearchResult, set_search
from tests.auth.accounts import signed_in
from tests.auth.jwt_helpers import SUPABASE_URL, JwksServer, SigningKey
from tests.conftest import fake_service, make_settings

pytestmark = [pytest.mark.integration, pytest.mark.db]

ANSWER_MARKER = "SECRET-EXPLANATION-MARKER"


def doc(*paras: str) -> dict[str, Any]:
    return {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": p}]} for p in paras],
    }


@dataclass
class World:
    tag: str
    word: str  # a word that exists nowhere else
    shared_s: str
    draft_s: str
    noingest_s: str
    private_s: str
    q: str
    q_draft: str
    note_a: uuid.UUID
    note_b: uuid.UUID
    ink_surface: str
    a: uuid.UUID
    b: uuid.UUID
    shared_ch: str

    @property
    def section_ids(self) -> list[str]:
        return [self.shared_s, self.draft_s, self.noingest_s, self.private_s]


@pytest.fixture
def pg(url: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(url, autocommit=True) as conn:
        yield conn


def _insert(pg: psycopg.Connection[Any], table: str, row: dict[str, Any]) -> None:
    from tests.db.fixtures import insert_row

    insert_row(pg, table, row)


@pytest.fixture
def world(pg: psycopg.Connection[Any]) -> Iterator[World]:
    from tests.db.rls import create_auth_user

    tag = uuid.uuid4().hex[:8]
    word = f"zorbulin{tag}"
    w = World(
        tag=tag,
        word=word,
        shared_s=f"s{tag}-shared-1",
        draft_s=f"s{tag}-draft-1",
        noingest_s=f"s{tag}-noingest-1",
        private_s=f"s{tag}-mine-1",
        q=f"q{tag}-1",
        q_draft=f"q{tag}-2",
        note_a=uuid.uuid4(),
        note_b=uuid.uuid4(),
        ink_surface=f"s{tag}-shared-1",
        a=create_auth_user(pg),
        b=create_auth_user(pg),
        shared_ch=f"c{tag}-shared",
    )
    ok, no = f"src{tag}-ok", f"src{tag}-no"
    _insert(
        pg, "sources", {"id": ok, "name": "Naadi original", "license": "proprietary-naadi", "allow_ai_ingest": True}
    )
    _insert(pg, "sources", {"id": no, "name": "Link only", "license": "youtube-embed-only", "allow_ai_ingest": False})
    exam = f"x{tag}"
    chapters = [
        (w.shared_ch, "chapter", "published", None, ok),
        (f"c{tag}-draft", "chapter", "draft", None, ok),
        (f"c{tag}-noingest", "chapter", "published", None, no),
        (f"c{tag}-mine", "user_upload", "published", w.a, ok),
    ]
    for cid, kind, status, owner, src in chapters:
        _insert(
            pg,
            "chapters",
            {
                "id": cid,
                "exam": exam,
                "kind": kind,
                "title": f"Chapter {cid}",
                "status": status,
                "owner_user_id": owner,
                "source_id": src,
            },
        )
    text = f"The {word} syndrome causes hyperkalemia through low aldosterone. Treat with fludrocortisone."
    for sid, cid in (
        (w.shared_s, w.shared_ch),
        (w.draft_s, f"c{tag}-draft"),
        (w.noingest_s, f"c{tag}-noingest"),
        (w.private_s, f"c{tag}-mine"),
    ):
        _insert(
            pg,
            "chapter_sections",
            {"id": sid, "chapter_id": cid, "sort": 1, "heading": f"About {word}", "body": Jsonb(doc(text))},
        )
    opts = Jsonb([{"label": "A", "html": f"{word} deficiency"}, {"label": "B", "html": "Beta"}])
    for qid, status in ((w.q, "published"), (w.q_draft, "draft")):
        _insert(
            pg,
            "questions",
            {
                "id": qid,
                "chapter_id": None,
                "exam": exam,
                "stem_html": f"<p>A patient with {word} has potassium 6.2 mEq/L.</p>",
                "options": opts,
                "answer": "B",
                "answer_html": f"<p>{ANSWER_MARKER} answer</p>",
                "explanation_html": f"<p>{ANSWER_MARKER}</p>",
                "why_not_html": f"<p>{ANSWER_MARKER} why not</p>",
                "educational_objective": f"{ANSWER_MARKER} objective",
                "source_id": ok,
                "status": status,
            },
        )
    for nid, uid, anchor in ((w.note_a, w.a, "quillfeather"), (w.note_b, w.b, "quillfeather")):
        _insert(
            pg,
            "notes",
            {
                "id": nid,
                "user_id": uid,
                "anchor_type": "section",
                "anchor_id": w.shared_s,
                "body": Jsonb(doc(f"My note about {word} and the {anchor} trick")),
            },
        )
    _insert(
        pg,
        "ink_layers",
        {
            "user_id": w.a,
            "surface_type": "section",
            "surface_id": w.ink_surface,
            "recognized_text": f"{word} low aldo high K",
        },
    )
    yield w
    pg.execute(
        "delete from public.embeddings where ref_id like any(%s) or owner_user_id = any(%s)", ([f"%{tag}%"], [w.a, w.b])
    )
    pg.execute("delete from public.ink_layers where user_id = any(%s)", ([w.a, w.b],))
    pg.execute("delete from public.notes where user_id = any(%s)", ([w.a, w.b],))
    pg.execute("delete from public.questions where exam = %s", (exam,))
    pg.execute("delete from public.chapters where exam = %s", (exam,))
    pg.execute("delete from public.sources where id = any(%s)", ([ok, no],))
    pg.execute("delete from auth.users where id = any(%s)", ([w.a, w.b],))


def run[T](coro: Any) -> T:
    return asyncio.run(coro)  # type: ignore[no-any-return]


async def _reconcile(url: str, engine: Engine, w: World, kinds: Any = None, **kw: Any) -> Any:
    db = Database(SecretStr(url), max_size=3)
    await db.open()
    try:
        ids = kw.pop("ref_ids", None)
        out = {}
        plan = {
            "sections": w.section_ids,
            "questions": [w.q, w.q_draft],
            "notes": [str(w.note_a), str(w.note_b)],
            "ink": [f"section:{w.ink_surface}"],
        }
        for kind in kinds or plan:
            out[kind] = (
                await reconcile(db, engine, kinds=[kind], ref_ids=ids if ids is not None else plan[kind], **kw)
            ).kinds[kind]
        return out
    finally:
        await db.close()


def rows(pg: psycopg.Connection[Any], w: World) -> list[tuple[Any, ...]]:
    return pg.execute(
        "select ref_type, ref_id, owner_user_id, content, vector_dims(embedding) from public.embeddings"
        " where ref_id like %s or ref_id = any(%s) order by ref_type, ref_id, chunk_index",
        (f"%{w.tag}%", [str(w.note_a), str(w.note_b)]),
    ).fetchall()


def test_the_index_holds_only_what_it_may(url: str, engine: Engine, world: World, pg: psycopg.Connection[Any]) -> None:
    out = run(_reconcile(url, engine, world))
    found = {(r[0], r[1]) for r in rows(pg, world)}
    assert ("section", world.shared_s) in found
    assert ("upload", world.private_s) in found  # a student's own file: type upload, owner set
    assert ("section", world.draft_s) not in found  # unpublished
    assert ("section", world.noingest_s) not in found  # source does not allow AI ingest
    assert ("question", world.q) in found
    assert ("question", world.q_draft) not in found
    assert ("note", str(world.note_a)) in found and ("note", str(world.note_b)) in found
    assert ("ink", f"section:{world.ink_surface}") in found
    owners = {r[1]: r[2] for r in rows(pg, world)}
    assert owners[world.shared_s] is None and owners[world.private_s] == world.a
    assert owners[str(world.note_a)] == world.a and owners[str(world.note_b)] == world.b
    assert all(r[4] == 384 for r in rows(pg, world))  # halfvec(384) vectors, all present
    assert out["sections"].embedded == 2 and out["questions"].embedded == 1


def test_question_chunks_never_contain_answers_or_explanations(
    url: str, engine: Engine, world: World, pg: psycopg.Connection[Any]
) -> None:
    run(_reconcile(url, engine, world, ["questions"]))
    qrows = [r for r in rows(pg, world) if r[0] == "question"]
    assert len(qrows) == 1
    content = qrows[0][3]
    assert world.word in content and "A. " in content and "Beta" in content  # stem + options
    assert ANSWER_MARKER not in content
    leaked = pg.execute(
        "select count(*) from public.embeddings where content like %s", (f"%{ANSWER_MARKER}%",)
    ).fetchone()
    assert leaked == (0,)


def test_reindex_is_idempotent_and_incremental(
    url: str, engine: Engine, world: World, pg: psycopg.Connection[Any]
) -> None:
    run(_reconcile(url, engine, world))
    again = run(_reconcile(url, engine, world))
    assert all(r.embedded == 0 and r.deleted == 0 for r in again.values())
    assert again["sections"].skipped == again["sections"].chunks == 2

    # change one section: only that section's chunk is embedded again
    pg.execute(
        "update public.chapter_sections set body = %s where id = %s",
        (Jsonb(doc(f"The {world.word} story changed completely.")), world.shared_s),
    )
    changed = run(_reconcile(url, engine, world, ["sections"]))
    assert changed["sections"].embedded == 1 and changed["sections"].skipped == 1
    assert any("story changed completely" in r[3] for r in rows(pg, world) if r[1] == world.shared_s)

    # unpublish it: its rows disappear on the next reconcile; the private upload stays
    pg.execute("update public.chapters set status = 'draft' where id = %s", (world.shared_ch,))
    gone = run(_reconcile(url, engine, world, ["sections"]))
    assert gone["sections"].deleted == 1
    left = {(r[0], r[1]) for r in rows(pg, world)}
    assert ("section", world.shared_s) not in left and ("upload", world.private_s) in left

    # delete a note: its rows go too
    pg.execute("delete from public.notes where id = %s", (world.note_b,))
    run(_reconcile(url, engine, world, ["notes"]))
    assert ("note", str(world.note_b)) not in {(r[0], r[1]) for r in rows(pg, world)}


def test_clients_can_never_read_the_index(url: str, engine: Engine, world: World, pg: psycopg.Connection[Any]) -> None:
    run(_reconcile(url, engine, world, ["sections"]))
    for role in ("authenticated", "anon"):
        with psycopg.connect(url, autocommit=False) as conn:
            conn.execute(f"set local role {role}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("select content from public.embeddings limit 1")


# ── HTTP ──


@pytest.fixture
def key() -> SigningKey:
    return SigningKey()


@pytest.fixture
def client(url: str, key: SigningKey, static_dir: Path, engine: Engine) -> Iterator[TestClient]:
    s = make_settings(
        static_dir=static_dir,
        supabase_url=SUPABASE_URL,
        database_url=SecretStr(url),
        admin_emails=SecretStr("owner-admin@example.test"),
    )
    app = create_app(
        s,
        health=fake_service(),
        db=Database(s.database_url),
        verifier=TokenVerifier(SUPABASE_URL, transport=JwksServer(key).transport()),
    )
    app.state.embedding_jobs.engine = engine  # share the loaded models across tests
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def ids(res: Any) -> list[str]:
    assert res.status_code == 200, res.text
    return [h["id"] for h in res.json()["hits"]]


def test_search_returns_shared_and_own_content_only(
    client: TestClient, key: SigningKey, url: str, engine: Engine, world: World
) -> None:
    run(_reconcile(url, engine, world))
    a = signed_in(key, world.a)
    b = signed_in(key, world.b)
    q = {"q": f"{world.word} hyperkalemia"}

    mine = ids(client.get("/api/search", params=q, headers=a))
    assert f"section:{world.shared_s}" in mine
    assert f"upload:{world.private_s}" in mine  # A's own file
    assert f"note:{world.note_a}" in mine
    assert f"ink:section:{world.ink_surface}" in mine
    assert f"question:{world.q}" in mine
    assert f"note:{world.note_b}" not in mine  # B's note
    assert f"section:{world.draft_s}" not in mine and f"section:{world.noingest_s}" not in mine
    assert f"question:{world.q_draft}" not in mine

    theirs = ids(client.get("/api/search", params=q, headers=b))
    assert f"section:{world.shared_s}" in theirs and f"question:{world.q}" in theirs
    assert f"note:{world.note_b}" in theirs
    assert f"upload:{world.private_s}" not in theirs  # A's file
    assert f"note:{world.note_a}" not in theirs  # A's note
    assert not any(i.startswith("ink:") for i in theirs)  # A's handwriting


def test_scopes_filter_by_type_and_links_are_structured(
    client: TestClient, key: SigningKey, url: str, engine: Engine, world: World
) -> None:
    run(_reconcile(url, engine, world))
    a = signed_in(key, world.a)
    params = {"q": world.word}
    only_q = client.get("/api/search", params={**params, "scope": "questions"}, headers=a).json()["hits"]
    assert only_q and all(h["type"] == "question" for h in only_q)
    only_notes = client.get("/api/search", params={**params, "scope": "notes"}, headers=a).json()["hits"]
    assert [h["id"] for h in only_notes] == [f"note:{world.note_a}"]
    assert only_notes[0]["target"]["kind"] == "note" and only_notes[0]["target"]["note_id"] == str(world.note_a)
    hw = client.get("/api/search", params={**params, "scope": "handwriting"}, headers=a).json()["hits"]
    assert [h["group"] for h in hw] == ["handwriting"]
    assert hw[0]["target"]["chapter_id"] == world.shared_ch and hw[0]["target"]["section_id"] == world.ink_surface
    assert hw[0]["target"]["note_id"] is None and hw[0]["target"]["question_id"] is None  # section ink
    chapters = client.get("/api/search", params={**params, "scope": "chapters"}, headers=a).json()["hits"]
    sec = next(h for h in chapters if h["id"] == f"section:{world.shared_s}")
    assert sec["target"] == {
        "type": "section",
        "chapter_id": world.shared_ch,
        "section_id": world.shared_s,
        **{
            k: None
            for k in (
                "question_id",
                "note_id",
                "kind",
                "anchor_type",
                "anchor_id",
                "surface_type",
                "surface_id",
                "stage_key",
                "task_id",
                "guide_key",
            )
        },
    }
    assert any(seg["hit"] for seg in sec["snippet"]) and sec["title"] == f"About {world.word}"
    bad = client.get("/api/search", params={"q": "x", "scope": "everything-else"}, headers=a)
    assert bad.status_code == 422


def test_no_answer_text_in_any_response(
    client: TestClient, key: SigningKey, url: str, engine: Engine, world: World
) -> None:
    run(_reconcile(url, engine, world))
    res = client.get(
        "/api/search", params={"q": f"{world.word} potassium", "scope": "questions"}, headers=signed_in(key, world.a)
    )
    assert res.status_code == 200
    body = res.text
    assert world.q in body
    assert ANSWER_MARKER not in body and "answer" not in res.json()["hits"][0] and "explanation" not in body.lower()


def test_rls_has_the_last_word_on_stale_index_rows(
    client: TestClient, key: SigningKey, url: str, engine: Engine, world: World, pg: psycopg.Connection[Any]
) -> None:
    """A row that is still in the index but no longer visible (unpublished since the last reindex) is never
    returned: hydration runs as the caller under RLS."""
    run(_reconcile(url, engine, world))
    a = signed_in(key, world.a)
    assert f"section:{world.shared_s}" in ids(client.get("/api/search", params={"q": world.word}, headers=a))
    pg.execute("update public.chapters set status = 'draft' where id = %s", (world.shared_ch,))
    pg.execute("update public.questions set status = 'retired' where id = %s", (world.q,))
    after = ids(client.get("/api/search", params={"q": world.word}, headers=a))
    assert f"section:{world.shared_s}" not in after and f"question:{world.q}" not in after
    # and someone else's private chapter id planted under the wrong owner would not surface either
    pg.execute(
        "insert into public.embeddings (owner_user_id, ref_type, ref_id, chunk_index, content)"
        " values (null, 'upload', %s, 0, %s)",
        (world.private_s, f"{world.word} planted row without an owner"),
    )
    b = signed_in(key, world.b)
    assert f"upload:{world.private_s}" not in ids(client.get("/api/search", params={"q": world.word}, headers=b))


def test_unrelated_queries_find_nothing_and_ask_for_the_web(
    client: TestClient, key: SigningKey, url: str, engine: Engine, world: World
) -> None:
    """Vector search always has neighbours; the relevance floor is what lets the screen say "nothing found"."""
    run(_reconcile(url, engine, world))
    a = signed_in(key, world.a)
    for q in ("qzxv wplk jjrt", "how do I cook pasta tonight"):
        body = client.get("/api/search", params={"q": q}, headers=a).json()
        assert body["hits"] == [] and body["suggest_web"] is True, q
    good = client.get("/api/search", params={"q": f"{world.word} hyperkalemia"}, headers=a).json()
    assert good["hits"] and good["took_ms"] >= 0


def test_search_requires_sign_in_and_a_query(client: TestClient, key: SigningKey, world: World) -> None:
    assert client.get("/api/search", params={"q": "renal"}).status_code == 401
    assert client.get("/api/search", params={"q": ""}, headers=signed_in(key, world.a)).status_code == 422
    assert client.get("/api/search", params={"q": "   "}, headers=signed_in(key, world.a)).status_code == 422
    assert client.get("/api/search", params={"q": "x" * 301}, headers=signed_in(key, world.a)).status_code == 422


def test_admin_index_status_and_reindex(
    client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]
) -> None:
    pg.execute("update auth.users set email = 'owner-admin@example.test' where id = %s", (world.a,))
    admin = signed_in(key, world.a, email="owner-admin@example.test")
    other = signed_in(key, world.b)
    assert client.get("/api/admin/embeddings", headers=other).status_code == 403
    assert client.post("/api/admin/embeddings/reindex", headers=other).status_code == 403
    st = client.get("/api/admin/embeddings", headers=admin)
    assert st.status_code == 200 and st.json()["models_ready"] is True
    assert st.json()["job"]["status"] == "idle"
    bad = client.post("/api/admin/embeddings/reindex", headers=admin, json={"kinds": ["nonsense"]})
    assert bad.status_code == 422


# ── web ──


class StubWeb:
    """Stands in for segment 04's SearchService (its own tests cover the sources); this checks our mapping."""

    def __init__(self, results: list[SearchResult], layers: dict[str, str] | None = None) -> None:
        self.results = results
        self.layers = layers or {}
        self.calls: list[dict[str, Any]] = []

    async def search(self, query: str, *, user_id: Any, intent: Any = None, web: bool = False) -> SearchAnswer:
        self.calls.append({"web": web})
        return SearchAnswer("h", "medical", self.results, self.layers)


def test_web_results_are_labelled_by_source(client: TestClient, key: SigningKey, world: World) -> None:
    stub = StubWeb(
        [
            SearchResult("tavily", "ADA 2026", "https://example.org/a", "Summary", "unknown", False, kind="web"),
            SearchResult("pubmed", "A paper", "https://pubmed.example/1", "Abstract", "pubmed", False),
        ]
    )
    set_search(stub)  # type: ignore[arg-type]
    try:
        res = client.get("/api/search/web", params={"q": "latest ada", "web": "true"}, headers=signed_in(key, world.a))
    finally:
        set_search(None)
    body = res.json()
    assert res.status_code == 200 and body["status"] == "ok"
    assert body["labels"] == ["Tavily", "PubMed"]
    assert [r["source_label"] for r in body["results"]] == ["Tavily", "PubMed"]
    assert stub.calls == [{"web": True}]


def test_web_reports_unavailable_empty_and_limit(client: TestClient, key: SigningKey, world: World) -> None:
    a = signed_in(key, world.a)
    assert (
        client.get("/api/search/web", params={"q": "x"}, headers=a).json()["status"] == "unavailable"
    )  # not initialised
    set_search(StubWeb([], {"tavily": "skipped:monthly_credits"}))  # type: ignore[arg-type]
    try:
        assert client.get("/api/search/web", params={"q": "latest"}, headers=a).json()["status"] == "limit"
        set_search(StubWeb([], {"pubmed": "empty"}))  # type: ignore[arg-type]
        assert client.get("/api/search/web", params={"q": "rta"}, headers=a).json()["status"] == "empty"
    finally:
        set_search(None)


# ── review fixes: mock exams, source rule, current text ──


def add_mock(pg: psycopg.Connection[Any], w: World) -> tuple[str, str]:
    """A mock exam chapter whose section carries a key-like sentence, and one mock question."""
    exam = f"x{w.tag}"
    src = f"src{w.tag}-ok"
    pg.execute(
        "insert into public.chapters (id, exam, kind, title, source_id) values (%s, %s, 'mock_exam', 'Mock', %s)",
        (f"c{w.tag}-mock", exam, src),
    )
    sid, qid = f"s{w.tag}-mock-1", f"q{w.tag}-mock-1"
    pg.execute(
        "insert into public.chapter_sections (id, chapter_id, sort, heading, body) values (%s, %s, 1, %s, %s)",
        (sid, f"c{w.tag}-mock", f"Instructions {w.word}", Jsonb(doc(f"Answer key {w.word}: 1 B, 2 D"))),
    )
    pg.execute(
        "insert into public.questions (id, chapter_id, exam, stem_html, options, answer, source_id, status, block)"
        " values (%s, %s, %s, %s, %s, 'B', %s, 'published', 1)",
        (qid, f"c{w.tag}-mock", exam, f"<p>Mock {w.word} stem</p>", Jsonb([{"label": "A", "html": "x"}]), src),
    )
    return sid, qid


def test_mock_sections_and_questions_are_never_indexed_or_shown(
    client: TestClient, key: SigningKey, url: str, engine: Engine, world: World, pg: psycopg.Connection[Any]
) -> None:
    sid, qid = add_mock(pg, world)
    run(_reconcile(url, engine, world))
    run(_reconcile(url, engine, world, ["sections", "questions"], ref_ids=[sid, qid]))
    held = pg.execute("select count(*) from public.embeddings where ref_id = any(%s)", ([sid, qid],)).fetchone()
    assert held == (0,)
    # a row indexed before the rule existed is deleted by the next reconcile ...
    pg.execute(
        "insert into public.embeddings (owner_user_id, ref_type, ref_id, chunk_index, content)"
        " values (null, 'section', %s, 0, %s)",
        (sid, f"Instructions {world.word} answer key"),
    )
    a = signed_in(key, world.a)
    # ... and, until then, it is never shown (hydration drops mock chapters)
    assert f"section:{sid}" not in ids(client.get("/api/search", params={"q": f"answer key {world.word}"}, headers=a))
    run(_reconcile(url, engine, world, ["sections"], ref_ids=[sid]))
    assert pg.execute("select count(*) from public.embeddings where ref_id = %s", (sid,)).fetchone() == (0,)


def test_a_source_that_stops_allowing_ingest_disappears_without_a_reindex(
    client: TestClient, key: SigningKey, url: str, engine: Engine, world: World, pg: psycopg.Connection[Any]
) -> None:
    run(_reconcile(url, engine, world))
    a = signed_in(key, world.a)
    q = {"q": f"{world.word} hyperkalemia"}
    assert f"section:{world.shared_s}" in ids(client.get("/api/search", params=q, headers=a))
    pg.execute("update public.sources set allow_ai_ingest = false where id = %s", (f"src{world.tag}-ok",))
    after = ids(client.get("/api/search", params=q, headers=a))
    assert f"section:{world.shared_s}" not in after and f"question:{world.q}" not in after


def test_snippets_come_from_the_current_text_not_the_stored_chunk(
    client: TestClient, key: SigningKey, url: str, engine: Engine, world: World, pg: psycopg.Connection[Any]
) -> None:
    run(_reconcile(url, engine, world))
    pg.execute(
        "update public.chapter_sections set body = %s where id = %s",
        (
            Jsonb(doc(f"The {world.word} syndrome now reads: freshly edited sentence about fludrocortisone.")),
            world.shared_s,
        ),
    )
    res = client.get("/api/search", params={"q": f"{world.word} syndrome"}, headers=signed_in(key, world.a))
    hit = next(h for h in res.json()["hits"] if h["id"] == f"section:{world.shared_s}")
    text = "".join(s["text"] for s in hit["snippet"])
    assert "freshly edited" in text and "low aldosterone" not in text
