"""Integration (real Postgres, migrated schema): courses map, chapter reader, reading positions, report issue.

The full HTTP path runs: bearer token -> JWKS verification -> `user_tx` (role authenticated + claims) -> RLS.
Every test builds its own content under a fresh exam id, so seeded content in the same database (segment 05)
never changes the expectations, and removes it afterwards.
"""

from __future__ import annotations

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
from naadi_api.main import create_app
from tests.auth.accounts import signed_in
from tests.auth.jwt_helpers import SUPABASE_URL, JwksServer, SigningKey
from tests.conftest import fake_service, make_settings

pytestmark = [pytest.mark.integration, pytest.mark.db]


def p(*inline: dict[str, Any]) -> dict[str, Any]:
    return {"type": "paragraph", "content": list(inline)}


def t(text: str, *marks: str) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text", "text": text}
    if marks:
        node["marks"] = [{"type": m} for m in marks]
    return node


@dataclass
class World:
    exam: str
    ch1: str
    ch2: str
    draft: str
    mock: str
    private: str
    s11: str
    s12: str
    s21: str
    draft_s: str
    private_s: str
    fig: str
    hidden_fig: str
    q1: str
    sys_a: str
    sys_b: str
    owner: uuid.UUID
    other: uuid.UUID


@pytest.fixture(scope="module")
def url() -> str:
    from tests.db.rls import database_url, ensure_migrated, require_local

    u = database_url()
    require_local(u)  # never a hosted database: these tests insert and delete rows
    ensure_migrated(u)
    return u


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
    exam = f"tx{tag}"
    w = World(
        exam=exam,
        ch1=f"t{tag}-ch1",
        ch2=f"t{tag}-ch2",
        draft=f"t{tag}-draft",
        mock=f"t{tag}-mock",
        private=f"t{tag}-mine",
        s11=f"t{tag}-ch1-s1",
        s12=f"t{tag}-ch1-s2",
        s21=f"t{tag}-ch2-s1",
        draft_s=f"t{tag}-draft-s1",
        private_s=f"t{tag}-mine-s1",
        fig=f"t{tag}-fig1",
        hidden_fig=f"t{tag}-fig2",
        q1=f"t{tag}-q1",
        sys_a=f"{exam}.sys.cardio",
        sys_b=f"{exam}.sys.renal",
        owner=create_auth_user(pg),
        other=create_auth_user(pg),
    )
    src = f"t{tag}-src"
    _insert(pg, "sources", {"id": src, "name": "Naadi original", "license": "proprietary-naadi"})
    _insert(pg, "taxonomy_nodes", {"id": exam, "kind": "exam", "label": f"Exam {tag}", "exam": [exam], "sort": 1})
    for i, sid in enumerate((w.sys_a, w.sys_b)):
        _insert(
            pg,
            "taxonomy_nodes",
            {"id": sid, "kind": "system", "parent_id": exam, "label": f"System {i}", "exam": [exam], "sort": i},
        )
    chapters = [
        {"id": w.ch1, "kind": "chapter", "title": "Cardio basics", "sort": 1},
        {"id": w.ch2, "kind": "chapter", "title": "Renal basics", "sort": 2},
        {"id": w.draft, "kind": "chapter", "title": "Draft", "sort": 3, "status": "draft"},
        {"id": w.mock, "kind": "mock_exam", "title": "Mock A", "sort": 9},
        {"id": w.private, "kind": "user_upload", "title": "My notes", "sort": 1, "owner_user_id": w.owner},
    ]
    for c in chapters:
        _insert(pg, "chapters", {"exam": exam, "source_id": src, **c})
    body1 = {
        "type": "doc",
        "content": [
            {"type": "heading", "attrs": {"level": 3}, "content": [t("Heading")]},
            p(t("Key "), t("fact", "bold")),
            {"type": "figure", "attrs": {"mediaId": w.fig}},
            {"type": "figure", "attrs": {"mediaId": w.hidden_fig}},
            {"type": "questionEmbed", "attrs": {"questionId": w.q1}},
            {"type": "sparkle", "content": [p(t("from the future"))]},
        ],
    }
    sections = [
        {"id": w.s11, "chapter_id": w.ch1, "sort": 1, "heading": "One", "body": Jsonb(body1), "reading_minutes": 3},
        {
            "id": w.s12,
            "chapter_id": w.ch1,
            "sort": 2,
            "heading": "Two",
            "body": Jsonb({"type": "doc", "content": [p(t("two"))]}),
            "body_html": "<p>" + " ".join(["word"] * 450) + "</p>",
        },
        {
            "id": w.s21,
            "chapter_id": w.ch2,
            "sort": 1,
            "heading": "Renal one",
            "body": Jsonb({"type": "doc", "content": [p(t("r"))]}),
            "reading_minutes": 2,
            "taxonomy_ids": [w.sys_b],
        },
        {"id": w.draft_s, "chapter_id": w.draft, "sort": 1, "heading": "Hidden", "body": Jsonb({"type": "doc"})},
        {
            "id": w.private_s,
            "chapter_id": w.private,
            "sort": 1,
            "heading": "Mine",
            "body": Jsonb({"type": "doc", "content": [p(t("mine"))]}),
            "reading_minutes": 1,
        },
    ]
    for s in sections:
        _insert(pg, "chapter_sections", s)
    _insert(
        pg,
        "media_assets",
        {
            "id": w.fig,
            "kind": "svg",
            "source_id": src,
            "caption": "Figure 1. Pedigree",
            "attribution": "Naadi original",
            "url": f"content-public/figures/{w.fig}.svg",
            "width": 800,
            "height": 600,
            "review_status": "approved",
        },
    )
    _insert(
        pg,
        "media_assets",
        {"id": w.hidden_fig, "kind": "svg", "source_id": src, "url": f"content-public/figures/{w.hidden_fig}.svg"},
    )
    _insert(pg, "section_media", {"section_id": w.s11, "media_id": w.fig, "sort": 0})
    _insert(pg, "section_media", {"section_id": w.s11, "media_id": w.hidden_fig, "sort": 1})
    opts = Jsonb([{"label": "A", "html": "Alpha"}, {"label": "B", "html": "Beta"}])
    for i in range(1, 11):
        _insert(
            pg,
            "questions",
            {
                "id": w.q1 if i == 1 else f"t{tag}-q{i}",
                "chapter_id": w.ch1,
                "exam": exam,
                "stem_html": f"<p>Stem {i}</p>",
                "options": opts,
                "answer": "B",
                "explanation_html": "<p>SECRET-EXPLANATION</p>",
                "source_id": src,
                # 8 cardio, 1 renal (below the share threshold), so ch1 lists under cardio only
                "taxonomy_ids": [w.sys_a] if i <= 8 else ([w.sys_b] if i == 9 else []),
            },
        )
    _insert(pg, "flashcards", {"front_html": "f", "back_html": "b", "origin": "seed", "section_id": w.s11})
    yield w
    pg.execute("delete from public.flashcards where section_id like %s", (f"t{tag}-%",))
    pg.execute("delete from public.questions where exam = %s", (exam,))
    pg.execute("delete from public.report_issues where user_id = any(%s)", ([w.owner, w.other],))
    pg.execute("delete from public.chapters where exam = %s", (exam,))
    pg.execute("delete from public.media_assets where source_id = %s", (src,))
    pg.execute("delete from public.taxonomy_nodes where id = any(%s)", ([w.sys_a, w.sys_b],))
    pg.execute("delete from public.taxonomy_nodes where id = %s", (exam,))
    pg.execute("delete from public.sources where id = %s", (src,))
    pg.execute("delete from auth.users where id = any(%s)", ([w.owner, w.other],))


@pytest.fixture
def key() -> SigningKey:
    return SigningKey()


@pytest.fixture
def client(url: str, key: SigningKey, static_dir: Path) -> Iterator[TestClient]:
    # Explicit None for every secret, so nothing from the environment or a .env file can reach this app.
    s = make_settings(
        static_dir=static_dir,
        supabase_url=SUPABASE_URL,
        database_url=SecretStr(url),
        supabase_anon_key=None,
        supabase_service_role_key=None,
        supabase_jwt_secret=None,
        openrouter_api_key=None,
        nvidia_api_key=None,
        tavily_api_key=None,
        admin_emails=None,
    )
    app = create_app(
        s,
        health=fake_service(),
        db=Database(s.database_url),
        verifier=TokenVerifier(SUPABASE_URL, transport=JwksServer(key).transport()),
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def h(key: SigningKey, uid: uuid.UUID) -> dict[str, str]:
    # A confirmed account with a live session (the API checks both in Supabase's auth schema).
    return signed_in(key, uid)


def test_courses_requires_sign_in(client: TestClient) -> None:
    res = client.get("/api/courses")
    assert res.status_code == 401
    assert res.json()["error"]["code"] in {"unauthorized", "missing_token"}


def test_course_map_lists_published_and_own_chapters_with_coverage(
    client: TestClient, key: SigningKey, world: World
) -> None:
    res = client.get("/api/courses", params={"exam": world.exam}, headers=h(key, world.owner))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["exam"] == world.exam
    assert any(e["id"] == world.exam and e["chapter_count"] == 2 for e in body["exams"])
    ids = [c["id"] for c in body["chapters"]]
    assert ids == [world.ch1, world.ch2, world.private]  # shared in order, then own files; no draft, no mock
    ch1 = body["chapters"][0]
    assert ch1["number"] == 1 and body["chapters"][2]["number"] is None
    assert ch1["systems"] == [world.sys_a]
    # one approved figure (the unapproved one is invisible), 10 questions, 1 card, 450 words -> 3 min + 3 min
    assert ch1["coverage"] == {
        "sections": 2,
        "reading_minutes": 6,
        "figures": 1,
        "videos": 0,
        "questions": 10,
        "cards": 1,
    }
    assert ch1["format"] == "read"
    assert ch1["progress"] == {
        "sections_read": 0,
        "sections_total": 2,
        "status": "not_started",
        "last_section_id": None,
        "last_read_at": None,
    }
    assert body["chapters"][1]["systems"] == [world.sys_b]  # from the section's taxonomy ids
    systems = {s["id"]: s for s in body["systems"]}
    assert systems[world.sys_a]["chapter_ids"] == [world.ch1]
    assert systems[world.sys_b]["chapter_ids"] == [world.ch2]
    assert body["mock_exams"] == [{"id": world.mock, "title": "Mock A", "meta": None, "question_count": 0}]
    assert body["next_up"]["reason"] == "start"
    assert body["next_up"]["section_id"] == world.s11


def test_other_users_do_not_see_private_chapters(client: TestClient, key: SigningKey, world: World) -> None:
    body = client.get("/api/courses", params={"exam": world.exam}, headers=h(key, world.other)).json()
    assert world.private not in [c["id"] for c in body["chapters"]]
    assert client.get(f"/api/chapters/{world.private}", headers=h(key, world.other)).status_code == 404
    assert client.get(f"/api/chapters/{world.private}", headers=h(key, world.owner)).status_code == 200
    assert client.get(f"/api/chapters/{world.draft}", headers=h(key, world.owner)).status_code == 404


def test_system_filter_and_unknown_filter(client: TestClient, key: SigningKey, world: World) -> None:
    body = client.get(
        "/api/courses", params={"exam": world.exam, "system": world.sys_b}, headers=h(key, world.owner)
    ).json()
    assert [c["id"] for c in body["chapters"]] == [world.ch2]
    assert body["system"] == world.sys_b
    assert len(body["systems"]) == 2  # the tiles stay complete
    empty = client.get(
        "/api/courses", params={"exam": world.exam, "system": "nope.sys.x"}, headers=h(key, world.owner)
    ).json()
    assert empty["chapters"] == []
    bad = client.get("/api/courses", params={"exam": "Step 1!"}, headers=h(key, world.owner))
    assert bad.status_code == 422


def test_chapter_detail_resolves_media_and_never_embeds_answers(
    client: TestClient, key: SigningKey, world: World
) -> None:
    res = client.get(f"/api/chapters/{world.ch1}", headers=h(key, world.owner))
    assert res.status_code == 200, res.text
    assert "SECRET-EXPLANATION" not in res.text
    body = res.json()
    assert [s["id"] for s in body["sections"]] == [world.s11, world.s12]
    assert body["sections"][1]["reading_minutes"] == 3  # computed from the HTML fallback
    assert body["number"] == 1 and body["chapter_count"] == 2
    assert body["next_chapter"] == {"id": world.ch2, "title": "Renal basics"}
    assert body["systems"] == [{"id": world.sys_a, "label": "System 0"}]
    assert set(body["media"]) == {world.fig}  # the unapproved figure is not visible
    fig = body["media"][world.fig]
    assert fig["url"] == f"{SUPABASE_URL}/storage/v1/object/public/content-public/figures/{world.fig}.svg"
    assert (fig["width"], fig["height"], fig["caption"]) == (800, 600, "Figure 1. Pedigree")
    assert fig["source"]["license"] == "proprietary-naadi"
    q = body["questions"][world.q1]
    assert q == {
        "id": world.q1,
        "stem_html": "<p>Stem 1</p>",
        "options": [{"label": "A", "html": "Alpha"}, {"label": "B", "html": "Beta"}],
    }
    # The unknown node is passed through untouched (the reader shows/logs it), never dropped by the API.
    assert body["sections"][0]["body"]["content"][-1]["type"] == "sparkle"


def test_unknown_nodes_and_missing_media_are_logged_with_the_request_id(
    client: TestClient, key: SigningKey, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    headers = {**h(key, world.owner), "x-request-id": "req-unknown-node-1"}
    with caplog.at_level("WARNING", logger="naadi.courses"):
        assert client.get(f"/api/chapters/{world.ch1}", headers=headers).status_code == 200
    events = {r.msg["event"]: r.msg for r in caplog.records if isinstance(r.msg, dict)}
    unknown = events["content_unknown_nodes"]
    assert unknown["node_types"] == ["sparkle"] and unknown["request_id"] == "req-unknown-node-1"
    assert events["content_media_missing"]["media_ids"] == [world.hidden_fig]


def test_chapter_etag_and_gzip(client: TestClient, key: SigningKey, world: World) -> None:
    first = client.get(f"/api/chapters/{world.ch1}", headers={**h(key, world.owner), "accept-encoding": "gzip"})
    assert first.headers["content-encoding"] == "gzip"
    assert first.headers["cache-control"] == "private, no-cache"
    etag = first.headers["etag"]
    again = client.get(f"/api/chapters/{world.ch1}", headers={**h(key, world.owner), "if-none-match": etag})
    assert again.status_code == 304
    assert again.content == b""


def test_reading_position_upsert_sync_and_ordering(client: TestClient, key: SigningKey, world: World) -> None:
    a = h(key, world.owner)
    put = client.put(
        "/api/reading-position",
        headers=a,
        json={
            "chapter_id": world.ch1,
            "section_id": world.s11,
            "offset": 2.5,
            "client_updated_at": "2026-11-16T09:00:00Z",
        },
    )
    assert put.status_code == 200, put.text
    assert put.json()["applied"] is True
    # A newer write from a second device
    newer = {
        "chapter_id": world.ch1,
        "section_id": world.s11,
        "offset": 4.0,
        "client_updated_at": "2026-11-16T09:05:00Z",
    }
    assert client.put("/api/reading-position", headers=a, json=newer).json()["position"]["offset"] == 4.0
    # A late offline write from the first device does not win, but completion still sticks
    stale = {
        "chapter_id": world.ch1,
        "section_id": world.s11,
        "offset": 1.0,
        "completed": True,
        "client_updated_at": "2026-11-16T09:01:00Z",
    }
    res = client.put("/api/reading-position", headers=a, json=stale).json()
    assert res["applied"] is False
    assert res["position"]["offset"] == 4.0 and res["position"]["completed"] is True
    # Completion is never unset by a later write
    res = client.put(
        "/api/reading-position",
        headers=a,
        json={**newer, "offset": 5.0, "client_updated_at": "2026-11-16T09:10:00Z"},
    ).json()
    assert res["applied"] is True and res["position"]["completed"] is True
    client.put(
        "/api/reading-position",
        headers=a,
        json={
            "chapter_id": world.ch1,
            "section_id": world.s12,
            "offset": 0.3,
            "client_updated_at": "2026-11-16T09:20:00Z",
        },
    )
    got = client.get("/api/reading-position", params={"chapter_id": world.ch1}, headers=a).json()
    assert got["last"]["section_id"] == world.s12
    assert {s["section_id"] for s in got["sections"]} == {world.s11, world.s12}
    # Another user sees nothing of it
    other = client.get("/api/reading-position", params={"chapter_id": world.ch1}, headers=h(key, world.other)).json()
    assert other == {"chapter_id": world.ch1, "last": None, "sections": []}
    # The course map reflects the progress and the next action
    body = client.get("/api/courses", params={"exam": world.exam}, headers=a).json()
    ch1 = next(c for c in body["chapters"] if c["id"] == world.ch1)
    assert ch1["progress"]["sections_read"] == 1 and ch1["progress"]["status"] == "in_progress"
    assert ch1["progress"]["last_section_id"] == world.s12
    assert body["next_up"] == {
        "reason": "continue",
        "chapter_id": world.ch1,
        "chapter_title": "Cardio basics",
        "section_id": world.s12,
        "section_heading": "Two",
    }


def test_future_client_timestamps_are_clamped(client: TestClient, key: SigningKey, world: World) -> None:
    a = h(key, world.owner)
    far = {"chapter_id": world.ch1, "section_id": world.s11, "offset": 9.0, "client_updated_at": "2099-01-01T00:00:00Z"}
    res = client.put("/api/reading-position", headers=a, json=far).json()
    assert res["position"]["client_updated_at"].startswith("20") and not res["position"][
        "client_updated_at"
    ].startswith("2099")
    # so a normal write right after still wins
    now = client.put(
        "/api/reading-position", headers=a, json={"chapter_id": world.ch1, "section_id": world.s11, "offset": 1.0}
    ).json()
    assert now["applied"] is True and now["position"]["offset"] == 1.0


@pytest.mark.parametrize(
    "case",
    ["wrong_chapter", "draft", "foreign_private", "missing"],
)
def test_reading_position_refuses_hidden_or_mismatched_sections(
    client: TestClient, key: SigningKey, world: World, case: str
) -> None:
    body = {
        "wrong_chapter": {"chapter_id": world.ch2, "section_id": world.s11},
        "draft": {"chapter_id": world.draft, "section_id": world.draft_s},
        "foreign_private": {"chapter_id": world.private, "section_id": world.private_s},
        "missing": {"chapter_id": world.ch1, "section_id": "does-not-exist"},
    }[case]
    res = client.put("/api/reading-position", headers=h(key, world.other), json={**body, "offset": 0})
    assert res.status_code == 404, res.text
    assert res.json()["error"]["code"] == "not_found"


def test_reading_position_validation(client: TestClient, key: SigningKey, world: World) -> None:
    a = h(key, world.owner)
    base = {"chapter_id": world.ch1, "section_id": world.s11}
    assert client.put("/api/reading-position", headers=a, json={**base, "offset": -1}).status_code == 422
    assert client.put("/api/reading-position", headers=a, json={**base, "offset": 1e9}).status_code == 422
    assert client.put("/api/reading-position", headers=a, json={**base, "offset": 1, "x": 1}).status_code == 422
    naive = {**base, "offset": 1, "client_updated_at": "2026-11-16T09:00:00"}
    assert client.put("/api/reading-position", headers=a, json=naive).status_code == 422


def test_report_issue(client: TestClient, key: SigningKey, world: World, pg: psycopg.Connection[Any]) -> None:
    a = h(key, world.owner)
    res = client.post(
        "/api/report-issue", headers=a, json={"ref_type": "section", "ref_id": world.s11, "body": "  Typo here  "}
    )
    assert res.status_code == 201, res.text
    out = res.json()
    assert out["status"] == "open"
    row = pg.execute("select user_id, body, ref_type from public.report_issues where id = %s", (out["id"],)).fetchone()
    assert row == (world.owner, "Typo here", "section")
    hidden = client.post(
        "/api/report-issue", headers=a, json={"ref_type": "media", "ref_id": world.hidden_fig, "body": "x"}
    )
    assert hidden.status_code == 404
    assert (
        client.post(
            "/api/report-issue", headers=a, json={"ref_type": "section", "ref_id": world.s11, "body": "   "}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/report-issue", headers=a, json={"ref_type": "assistant_message", "ref_id": "x", "body": "x"}
        ).status_code
        == 422
    )
