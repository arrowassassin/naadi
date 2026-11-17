"""Admin console (segment 19) over HTTP against the real database.

- Every admin route: 401 anonymous, 403 for a signed-in student; `authenticated` still cannot write shared tables.
- Chapter → section → draft → publish → the student sees it with no redeploy; drafts never reach a student; edit +
  rollback; stale drafts refused; unknown nodes refused with their path.
- Licence gates: unapproved / unlicensed / share-alike media refuse publishing with a reason.
- Media: figure (SVG sanitised, raster → WebP) and PDF uploads through the object store, YouTube add with a stubbed
  lookup, review gates; questions CRUD with snapshots and the publish workflow; coverage counts move.
- app_config: PUT → history → revert, visible through /api/public-config; audit rows for every write.
- Queues: counts, report_issues approve/reject, generated_media approve publishes a figure into a section.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from naadi_api.admin.media import VideoMeta
from naadi_api.auth import TokenVerifier
from naadi_api.db import Database
from naadi_api.main import create_app
from naadi_api.seed.importer import run_seed
from tests.auth.accounts import signed_in
from tests.auth.jwt_helpers import SUPABASE_URL, JwksServer, SigningKey
from tests.conftest import fake_service, make_settings

from .rls import DB_MARKS, create_auth_user, require_local
from .seed_helpers import bundle, options, remove_seed

pytestmark = DB_MARKS

ADMIN_EMAIL = "owner@example.com"
SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" aria-label="Square">'
    b'<rect width="10" height="10"/><script>alert(1)</script></svg>'
)
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


class MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, str]] = {}

    def put(self, bucket: str, path: str, data: bytes, content_type: str) -> None:
        self.objects[(bucket, path)] = (data, content_type)

    def get(self, bucket: str, path: str) -> bytes:
        return self.objects[(bucket, path)][0]


@pytest.fixture(scope="module")
def url(migrated_url: str) -> Iterator[str]:
    require_local(migrated_url)
    b = bundle()
    remove_seed(migrated_url, b)
    run_seed(options(migrated_url, only=("sources", "taxonomy", "figures", "chapters", "questions", "app_config")), bundle=b)
    with psycopg.connect(migrated_url, autocommit=True) as c:
        # fixture sources for the gates (removed again below)
        c.execute(
            "insert into public.sources (id, name, license, allow_copy, allow_modify, allow_commercial, allow_ai_ingest)"
            " values ('test-sa', 'SA test', 'CC-BY-SA-4.0', true, true, true, false),"
            " ('test-linkonly', 'Link only', 'link-only', false, false, false, false),"
            " ('naadi-ai-generated', 'AI generated', 'proprietary-naadi', true, true, true, false)"
            " on conflict (id) do nothing"
        )
    yield migrated_url
    with psycopg.connect(migrated_url, autocommit=True) as c:
        c.execute("delete from public.admin_audit")
        c.execute("delete from public.report_issues")
        c.execute("delete from public.generated_media")
        c.execute("delete from public.question_revisions")
        c.execute("delete from public.questions where id like 'q-%'")
        c.execute("delete from public.chapters where owner_user_id is null and id like 'admin-test-%'")
        c.execute("delete from public.section_media where media_id like 'fig-%' or media_id like 'pdf-%' or media_id like 'yt-%' or media_id like 'gen-%'")
        c.execute("delete from public.media_assets where id like 'fig-%' or id like 'pdf-%' or id like 'yt-%' or id like 'gen-%'")
    remove_seed(migrated_url, b)
    with psycopg.connect(migrated_url, autocommit=True) as c:
        c.execute("delete from public.sources where id in ('test-sa', 'test-linkonly', 'naadi-ai-generated')")


@pytest.fixture
def users(url: str) -> Iterator[dict[str, uuid.UUID]]:
    with psycopg.connect(url, autocommit=True) as c:
        ids = {"admin": create_auth_user(c), "student": create_auth_user(c)}
    yield ids
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("delete from public.report_issues where user_id = any(%s)", (list(ids.values()),))
        c.execute("delete from auth.users where id = any(%s)", (list(ids.values()),))


@pytest.fixture
def key() -> SigningKey:
    return SigningKey()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def client(url: str, key: SigningKey, static_dir: Path, store: MemoryStore) -> Iterator[TestClient]:
    s = make_settings(
        static_dir=static_dir,
        supabase_url=SUPABASE_URL,
        database_url=SecretStr(url),
        admin_emails=ADMIN_EMAIL,
        app_env="prod",
        supabase_service_role_key=None,
        supabase_jwt_secret=None,
        openrouter_api_key=None,
        nvidia_api_key=None,
        tavily_api_key=None,
    )
    app = create_app(
        s, health=fake_service(), db=Database(s.database_url), verifier=TokenVerifier(SUPABASE_URL, transport=JwksServer(key).transport())
    )
    app.state.admin_object_store = store

    async def lookup(youtube_id: str) -> VideoMeta | None:
        if youtube_id == "missing0000":
            return None
        return VideoMeta(youtube_id, "Cardiac cycle", "UCtest", "Test channel", youtube_id != "noembed0000", 615)

    app.state.admin_video_lookup = lookup
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def h(key: SigningKey, users: dict[str, uuid.UUID]) -> dict[str, dict[str, str]]:
    return {
        "admin": signed_in(key, users["admin"], email=ADMIN_EMAIL, header="Authorization"),
        "student": signed_in(key, users["student"], email="student@example.com", header="Authorization"),
    }


def _ok(r: Any, status: int = 200) -> Any:
    assert r.status_code == status, r.text
    return r.json() if r.content else None


def _gate(r: Any) -> str:
    assert r.status_code == 409, r.text
    err = r.json()["error"]
    assert err["code"] == "gate_refused", err
    return str(err["details"][0]["loc"][1])


def _para(text: str) -> dict[str, Any]:
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def _doc(*blocks: Any) -> dict[str, Any]:
    return {"type": "doc", "content": list(blocks)}


# ── gate ──


def test_every_admin_route_refuses_anonymous_and_students(client: TestClient, h: dict[str, dict[str, str]]) -> None:
    spec = client.get("/api/openapi.json").json()
    checked = 0
    for path, ops in spec["paths"].items():
        if not path.startswith("/api/admin/"):
            continue
        concrete = path
        for name in ("chapter_id", "section_id", "media_id", "question_id", "key", "kind", "item_id", "thread_id", "run_id"):
            concrete = concrete.replace("{" + name + "}", "x1-test-0000")
        concrete = concrete.replace("{revision_id}", "1").replace("{report_id}", str(uuid.uuid4())).replace("{run_id}", "1")
        for method in ops:
            anon = client.request(method.upper(), concrete, json={})
            student = client.request(method.upper(), concrete, json={}, headers=h["student"])
            assert anon.status_code == 401, (method, path, anon.status_code)
            assert student.status_code == 403, (method, path, student.status_code)
            checked += 1
    assert checked >= 40


def test_clients_still_cannot_write_shared_tables(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as c:
        for table in ("chapters", "chapter_sections", "chapter_section_revisions", "media_assets", "section_media", "questions",
                      "admin_audit", "question_revisions", "app_config"):
            for role in ("anon", "authenticated"):
                row = c.execute(
                    "select has_table_privilege(%s, %s, 'insert') or has_table_privilege(%s, %s, 'update')"
                    " or has_table_privilege(%s, %s, 'delete')",
                    (role, f"public.{table}",) * 3,
                ).fetchone()
                assert row == (False,), (table, role)
        for table in ("admin_audit", "question_revisions", "chapter_section_revisions"):
            row = c.execute("select has_table_privilege('authenticated', %s, 'select')", (f"public.{table}",)).fetchone()
            assert row == (False,), table


# ── chapters, drafts, publish, rollback ──


def _new_chapter(client: TestClient, h: dict[str, dict[str, str]], suffix: str) -> tuple[str, str]:
    ch = _ok(client.post("/api/admin/chapters", json={"id": f"admin-test-{suffix}", "exam": "step1", "title": f"Test {suffix}",
                                                     "source_id": "naadi-original-v1"}, headers=h["admin"]), 201)
    assert ch["status"] == "draft"
    sec = _ok(client.post(f"/api/admin/chapters/{ch['id']}/sections", json={"heading": "Intro",
                                                                             "taxonomy_ids": ["topic.dev.milestones"]},
                          headers=h["admin"]), 201)
    return ch["id"], sec["id"]


def _publish(client: TestClient, h: dict[str, dict[str, str]], sid: str, body: dict[str, Any]) -> dict[str, Any]:
    ed = _ok(client.get(f"/api/admin/sections/{sid}/editor", headers=h["admin"]))
    draft = ed["draft"]
    d = _ok(client.put(f"/api/admin/sections/{sid}/draft", json={
        "body": body, "parent_revision_id": ed["live"]["revision_id"],
        "expected_updated_at": draft["updated_at"] if draft else None}, headers=h["admin"]))
    r = client.post(f"/api/admin/sections/{sid}/publish", json={"revision_id": d["id"], "expected_updated_at": d["updated_at"]},
                    headers=h["admin"])
    return {"response": r, "draft": d}


def test_chapter_goes_live_without_a_redeploy_and_drafts_stay_hidden(
    client: TestClient, h: dict[str, dict[str, str]], url: str
) -> None:
    cid, sid = _new_chapter(client, h, "live")
    assert client.get(f"/api/chapters/{cid}", headers=h["student"]).status_code == 404  # draft chapter
    out = _publish(client, h, sid, _doc(_para("First version")))
    rev = _ok(out["response"])
    assert rev["status"] == "published" and rev["is_live"]
    # publishing needs every section to have a body and passes the gates
    _ok(client.post(f"/api/admin/chapters/{cid}/status", json={"status": "in_review"}, headers=h["admin"]))
    _ok(client.post(f"/api/admin/chapters/{cid}/status", json={"status": "published"}, headers=h["admin"]))
    seen = _ok(client.get(f"/api/chapters/{cid}", headers=h["student"]))
    assert "First version" in str(seen)
    # a new draft is invisible to the student until it is published
    ed = _ok(client.get(f"/api/admin/sections/{sid}/editor", headers=h["admin"]))
    _ok(client.put(f"/api/admin/sections/{sid}/draft", json={"body": _doc(_para("Secret draft")), "parent_revision_id":
                                                             ed["live"]["revision_id"], "expected_updated_at": None},
                   headers=h["admin"]))
    assert "Secret draft" not in client.get(f"/api/chapters/{cid}", headers=h["student"]).text
    with psycopg.connect(url) as c:
        c.execute("set local role authenticated")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("select body from public.chapter_section_revisions limit 1")
    # bad transition
    r = client.post(f"/api/admin/chapters/{cid}/status", json={"status": "draft"}, headers=h["admin"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "bad_transition"


def test_edit_rollback_diff_and_stale_drafts(client: TestClient, h: dict[str, dict[str, str]]) -> None:
    cid, sid = _new_chapter(client, h, "rollback")
    first = _ok(_publish(client, h, sid, _doc(_para("Alpha")))["response"])
    second = _ok(_publish(client, h, sid, _doc(_para("Alpha"), _para("Beta")))["response"])
    diff = _ok(client.get(f"/api/admin/revisions/{second['id']}/diff", params={"against": first["id"]}, headers=h["admin"]))
    assert [(b["op"], b["text"]) for b in diff["blocks"]] == [("equal", "Alpha"), ("insert", "Beta")]
    back = _ok(client.post(f"/api/admin/revisions/{first['id']}/rollback", json={}, headers=h["admin"]))
    assert back["restored_from_id"] == first["id"] and back["is_live"]
    ed = _ok(client.get(f"/api/admin/sections/{sid}/editor", headers=h["admin"]))
    assert ed["live"]["body"] == _doc(_para("Alpha")) and ed["live"]["revision_id"] == back["id"]
    revs = _ok(client.get(f"/api/admin/sections/{sid}/revisions", headers=h["admin"]))["items"]
    assert [r["id"] for r in revs][:3] == [back["id"], second["id"], first["id"]]
    # a draft started before someone else published is refused at publish
    d = _ok(client.put(f"/api/admin/sections/{sid}/draft", json={"body": _doc(_para("Old base")), "parent_revision_id": second["id"],
                                                                 "expected_updated_at": None}, headers=h["admin"]))
    r = client.post(f"/api/admin/sections/{sid}/publish", json={"revision_id": d["id"], "expected_updated_at": d["updated_at"]},
                    headers=h["admin"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_parent"
    # autosave with a stale timestamp is refused; the right one succeeds
    r = client.put(f"/api/admin/sections/{sid}/draft", json={"body": _doc(_para("x")), "parent_revision_id": back["id"],
                                                             "expected_updated_at": "2020-01-01T00:00:00Z"}, headers=h["admin"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_draft"
    _ok(client.delete(f"/api/admin/sections/{sid}/draft", headers=h["admin"]), 204)
    # published revisions are immutable in the database itself
    with pytest.raises(psycopg.errors.CheckViolation), psycopg.connect(client.app.state.settings.database_url.get_secret_value()) as c:  # type: ignore[attr-defined]
        c.execute("update public.chapter_section_revisions set body = '{}'::jsonb where id = %s", (first["id"],))


def test_unknown_nodes_are_refused_with_their_path(client: TestClient, h: dict[str, dict[str, str]]) -> None:
    _cid, sid = _new_chapter(client, h, "schema")
    r = client.put(f"/api/admin/sections/{sid}/draft", json={"body": _doc(_para("ok"), {"type": "iframe", "attrs": {"src": "x"}}),
                                                             "parent_revision_id": None, "expected_updated_at": None},
                   headers=h["admin"])
    assert r.status_code == 422, r.text
    detail = r.json()["error"]["details"][0]
    assert detail["loc"] == ["body", "body", "content", 1] and detail["msg"] == "unknown_node"


# ── media and gates ──


def test_media_uploads_gates_and_youtube(client: TestClient, h: dict[str, dict[str, str]], store: MemoryStore) -> None:
    a = h["admin"]
    q = {"source_id": "naadi-original-v1", "title": "Square figure", "caption": "A square"}
    fig = _ok(client.post("/api/admin/media/files", params=q, content=SVG, headers={**a, "content-type": "image/svg+xml"}), 201)
    assert fig["kind"] == "svg" and fig["review_status"] == "unreviewed" and (fig["width"], fig["height"]) == (10, 10)
    stored = next(v for (b, p), v in store.objects.items() if p.endswith(f"{fig['id']}.svg"))
    assert b"<script" not in stored[0]
    png = io.BytesIO()
    Image.new("RGB", (40, 20), "red").save(png, format="PNG")
    img = _ok(client.post("/api/admin/media/files", params=q, content=png.getvalue(), headers={**a, "content-type": "image/png"}), 201)
    assert img["kind"] == "image" and any(p.endswith(f"{img['id']}.webp") for (_b, p) in store.objects)
    pdf = _ok(client.post("/api/admin/media/files", params={**q, "title": "Handout"}, content=PDF,
                          headers={**a, "content-type": "application/pdf"}), 201)
    assert any(b == "content-private" and p.endswith(f"{pdf['id']}.pdf") for (b, p) in store.objects)
    assert client.post("/api/admin/media/files", params=q, content=b"not a pdf", headers={**a, "content-type": "application/pdf"}).status_code == 415
    assert client.post("/api/admin/media/files", params=q, content=b"x", headers={**a, "content-type": "text/html"}).status_code == 415
    # licence: a link-only source cannot host a copy; nothing is stored
    before = len(store.objects)
    assert _gate(client.post("/api/admin/media/files", params={**q, "source_id": "test-linkonly"}, content=SVG,
                             headers={**a, "content-type": "image/svg+xml"})) == "copy_not_allowed"
    assert len(store.objects) == before
    # an unreviewed figure in a body blocks publishing; after approval it publishes
    _cid, sid = _new_chapter(client, h, "media")
    body = _doc(_para("See"), {"type": "figure", "attrs": {"mediaId": fig["id"]}}, {"type": "pdf", "attrs": {"mediaId": pdf["id"], "page": None}})
    assert _gate(_publish(client, h, sid, body)["response"]) == "media_not_approved"
    for m in (fig, pdf):
        assert _ok(client.post(f"/api/admin/media/{m['id']}/review", json={"status": "approved"}, headers=a))["review_status"] == "approved"
    ed = _ok(client.get(f"/api/admin/sections/{sid}/editor", headers=a))
    d = ed["draft"]
    _ok(client.post(f"/api/admin/sections/{sid}/publish", json={"revision_id": d["id"], "expected_updated_at": d["updated_at"]}, headers=a))
    lib = _ok(client.get("/api/admin/media", params={"q": "Square"}, headers=a))
    assert sid in next(m for m in lib["items"] if m["id"] == fig["id"])["section_ids"]
    # share-alike media cannot go into a proprietary chapter
    sa = _ok(client.post("/api/admin/media/files", params={**q, "source_id": "test-sa"}, content=SVG,
                         headers={**a, "content-type": "image/svg+xml"}), 201)
    assert _gate(client.post(f"/api/admin/media/{sa['id']}/review", json={"status": "approved"}, headers=a)) == "share_alike_in_proprietary"
    # YouTube: lookup, add, embeddable gate, duplicates
    look = _ok(client.post("/api/admin/media/youtube/lookup", json={"url": "https://youtu.be/czY5nyvZ7cU"}, headers=a))
    assert look["embeddable"] is True and look["channel_title"] == "Test channel" and look["existing_media_id"] is None
    yt = _ok(client.post("/api/admin/media/youtube", json={"url": "https://www.youtube.com/watch?v=czY5nyvZ7cU", "source_id": "youtube"},
                         headers=a), 201)
    assert yt["kind"] == "video_youtube" and yt["duration_s"] == 615
    assert _gate(client.post("/api/admin/media/youtube", json={"url": "https://youtu.be/czY5nyvZ7cU", "source_id": "youtube"}, headers=a)) == "duplicate_video"
    assert _gate(client.post("/api/admin/media/youtube", json={"url": "https://youtu.be/noembed0000", "source_id": "youtube"}, headers=a)) == "not_embeddable"
    assert client.post("/api/admin/media/youtube/lookup", json={"url": "https://youtu.be/missing0000"}, headers=a).status_code == 404
    assert client.post("/api/admin/media/youtube/lookup", json={"url": "https://example.com/v"}, headers=a).status_code == 422
    _ok(client.post(f"/api/admin/media/{yt['id']}/review", json={"status": "approved"}, headers=a))
    att = _ok(client.put(f"/api/admin/sections/{sid}/media/{yt['id']}", json={"start_s": 10, "end_s": 70}, headers=a))
    assert (att["start_s"], att["end_s"]) == (10, 70)
    # the figure is in the live body: rejecting it or detaching it is refused
    assert _gate(client.delete(f"/api/admin/sections/{sid}/media/{fig['id']}", headers=a)) == "media_in_body"


# ── questions ──


def _question(**kw: Any) -> dict[str, Any]:
    base = {
        "exam": "step1",
        "stem_html": "<p>A 7-year-old boy has blistering after sun exposure. Which process is defective?</p>",
        "options": [{"label": c, "html": f"<p>Process {c}</p>"} for c in "ABCDE"],
        "answer": "C",
        "explanation_html": "<p>Nucleotide excision repair.</p>",
        "difficulty": "easy",
        "taxonomy_ids": ["topic.dev.milestones", "step1.disc.pathology"],
        "source_id": "naadi-original-v1",
    }
    return {**base, **kw}


def test_question_crud_workflow_and_snapshots(client: TestClient, h: dict[str, dict[str, str]], url: str) -> None:
    a = h["admin"]
    q = _ok(client.post("/api/admin/questions", json=_question(), headers=a), 201)
    assert q["status"] == "draft" and q["answer"] == "C"
    student_block = client.post("/api/blocks/single", json={"question_id": q["id"]}, headers=h["student"])
    assert student_block.status_code == 404  # drafts never reach students
    upd = _ok(client.put(f"/api/admin/questions/{q['id']}", json={**_question(answer="B"), "expected_version": q["version"]}, headers=a))
    stale = client.put(f"/api/admin/questions/{q['id']}", json={**_question(), "expected_version": q["version"]}, headers=a)
    assert stale.status_code == 409
    for status in ("in_review", "published"):
        upd = _ok(client.post(f"/api/admin/questions/{q['id']}/status", json={"status": status, "expected_version": upd["version"]}, headers=a))
    assert upd["status"] == "published"
    blk = client.post("/api/blocks/single", json={"question_id": q["id"]}, headers=h["student"])
    assert blk.status_code == 201, blk.text
    first = client.get(f"/api/blocks/{blk.json()['id']}/questions/1", headers=h["student"])
    assert first.status_code == 200, first.text
    for leak in ('"answer":', '"answer_html"', '"explanation_html"', "Nucleotide excision"):
        assert leak not in blk.text and leak not in first.text, leak
    # editing the stem of a published item sends it back to review
    edited = _ok(client.put(f"/api/admin/questions/{q['id']}", json={**_question(answer="B", stem_html="<p>Changed stem</p>"),
                                                                     "expected_version": upd["version"]}, headers=a))
    assert edited["status"] == "in_review"
    revs = _ok(client.get(f"/api/admin/questions/{q['id']}/revisions", headers=a))["items"]
    assert [r["version"] for r in revs] == [5, 4, 3, 2, 1]
    assert _ok(client.get(f"/api/admin/questions/{q['id']}/lint", headers=a))["available"] in (True, False)
    listed = _ok(client.get("/api/admin/questions", params={"q": "Changed stem"}, headers=a))["items"]
    assert [i["id"] for i in listed] == [q["id"]] and "answer" not in listed[0]
    assert _gate(client.post("/api/admin/questions", json=_question(taxonomy_ids=["topic.nope"]), headers=a)) == "taxonomy_missing"


# ── coverage ──


def test_coverage_counts_move_with_published_content(client: TestClient, h: dict[str, dict[str, str]]) -> None:
    a = h["admin"]

    def topic() -> dict[str, Any]:
        rep = _ok(client.get("/api/admin/coverage", params={"exam": "step1"}, headers=a))
        assert rep["goals"] == {"sections": 1, "videos": 1, "questions": 15, "cards": 10}
        assert {c["status"] for c in rep["cells"]} <= {"gap", "thin", "covered"}
        return next(t for t in rep["topics"] if t["id"] == "topic.dev.milestones")

    before = topic()
    q = _ok(client.post("/api/admin/questions", json=_question(), headers=a), 201)
    for status in ("in_review", "published"):
        q = _ok(client.post(f"/api/admin/questions/{q['id']}/status", json={"status": status, "expected_version": q["version"]}, headers=a))
    after = topic()
    assert after["counts"]["questions"] == before["counts"]["questions"] + 1
    assert after["status"] in ("thin", "covered") and "videos" in after["missing"] or after["counts"]["videos"] >= 1


# ── config ──


def test_config_edit_history_revert_and_public_visibility(client: TestClient, h: dict[str, dict[str, str]], url: str) -> None:
    a = h["admin"]
    cur = _ok(client.get("/api/admin/config/feature_flags", headers=a))
    flags = dict(cur["value"])
    flags["assistant"] = {**flags["assistant"], "enabled": not flags["assistant"]["enabled"]}
    saved = _ok(client.put("/api/admin/config/feature_flags", json={"value": flags, "expected_updated_at": cur["updated_at"]}, headers=a))
    assert client.get("/api/public-config").json()["features"]["assistant"] is flags["assistant"]["enabled"]
    hist = _ok(client.get("/api/admin/config/feature_flags/history", headers=a))["items"]
    assert hist and hist[0]["value"] == cur["value"]
    back = _ok(client.post("/api/admin/config/feature_flags/revert", json={"history_id": hist[0]["id"],
                                                                         "expected_updated_at": saved["updated_at"]}, headers=a))
    assert back["value"] == cur["value"]
    assert client.get("/api/public-config").json()["features"]["assistant"] is cur["value"]["assistant"]["enabled"]
    assert client.post("/api/admin/config/feature_flags/revert", json={"history_id": hist[0]["id"],
                                                                     "expected_updated_at": saved["updated_at"]}, headers=a).status_code == 409
    actions = [r["action"] for r in _ok(client.get("/api/admin/audit", params={"ref_type": "app_config", "ref_id": "feature_flags"}, headers=a))["items"]]
    assert actions[:2] == ["config.revert", "config.update"]


# ── queues ──


def test_queues_reports_and_generated_media(client: TestClient, h: dict[str, dict[str, str]], users: dict[str, uuid.UUID],
                                             url: str, store: MemoryStore) -> None:
    a = h["admin"]
    _cid, sid = _new_chapter(client, h, "queues")
    _ok(_publish(client, h, sid, _doc(_para("Body")))["response"])
    with psycopg.connect(url, autocommit=True) as c:
        rep = c.execute("insert into public.report_issues (user_id, ref_type, ref_id, body) values (%s, 'section', %s, 'Typo')"
                        " returning id", (users["student"], sid)).fetchone()
        gen = c.execute("insert into public.generated_media (user_id, kind, title, prompt, model, storage_path, width, height)"
                        " values (%s, 'image', 'Heart', 'a heart diagram', 'flux', %s, 64, 32) returning id",
                        (users["student"], f"generated-media/{users['student']}/heart.png")).fetchone()
    assert rep and gen
    png = io.BytesIO()
    Image.new("RGB", (64, 32), "blue").save(png, format="PNG")
    store.put("generated-media", f"{users['student']}/heart.png", png.getvalue(), "image/png")
    counts = {i["kind"]: i for i in _ok(client.get("/api/admin/queues", headers=a))["items"]}
    assert counts["report_issues"]["count"] >= 1 and counts["report_issues"]["decides_here"]
    assert counts["generated_media"]["count"] >= 1
    page = _ok(client.get("/api/admin/queues/report_issues", headers=a))
    item = next(i for i in page["items"] if i["id"] == str(rep[0]))
    assert item["ref"] == {"type": "section", "id": sid} and item["detail"]["body"] == "Typo"
    _ok(client.post(f"/api/admin/queues/report_issues/{rep[0]}/approve", json={"note": "fixed"}, headers=a))
    assert client.post(f"/api/admin/queues/report_issues/{rep[0]}/approve", json={}, headers=a).status_code == 404
    reports = _ok(client.get("/api/admin/reports", params={"status": "resolved"}, headers=a))
    assert any(r["id"] == str(rep[0]) and r["link"]["section_id"] == sid for r in reports["items"])
    # generated media: approve needs a section and a caption, then publishes a labelled figure into the section
    assert _gate(client.post(f"/api/admin/queues/generated_media/{gen[0]}/approve", json={"params": {}}, headers=a)) == "missing_section_id"
    dec = _ok(client.post(f"/api/admin/queues/generated_media/{gen[0]}/approve",
                          json={"params": {"section_id": sid, "caption": "Heart chambers"}}, headers=a))
    assert dec["status"] == "approved"
    ed = _ok(client.get(f"/api/admin/sections/{sid}/editor", headers=a))
    fig = ed["live"]["body"]["content"][-1]
    assert fig["type"] == "figure"
    with psycopg.connect(url) as c:
        cap, src, rs = c.execute("select caption, source_id, review_status from public.media_assets where id = %s",
                                 (fig["attrs"]["mediaId"],)).fetchone()  # type: ignore[misc]
    assert src == "naadi-ai-generated" and rs == "approved" and cap.endswith("not clinical reference")
    assert any(b == "content-public" and fig["attrs"]["mediaId"] in p for (b, p) in store.objects)
    assert client.get("/api/admin/queues/nope", headers=a).status_code == 404
    audit = [r["action"] for r in _ok(client.get("/api/admin/audit", headers=a))["items"]]
    assert "queue.generated_media.approve" in audit and "queue.report_issues.approve" in audit
