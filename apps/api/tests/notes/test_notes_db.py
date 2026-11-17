"""Integration (real Postgres, migrated schema, full HTTP path): notes, highlights, notebooks and pages.

Anchor gates (question anchors need a graded attempt; hidden sections are 404), idempotent creates, body_text, the
index hook, embeddings and page ink removed on delete, page order under every page operation, isolation between
students, request limits, and that note bodies never reach the logs.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from naadi_api.notes import routes as notes_routes
from tests.auth.jwt_helpers import SigningKey
from tests.db.fixtures import insert_row

from .conftest import Ink

pytestmark = [pytest.mark.integration, pytest.mark.db]


def doc(*texts: str) -> dict[str, Any]:
    return {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": t}]} for t in texts]}


def pos(quote: str = "One") -> dict[str, Any]:
    return {"from": 1, "to": 4, "quote": quote, "prefix": "", "suffix": "", "revision": "r1"}


@pytest.fixture
def reindexed(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(app: Any, kinds: Any, **kw: Any) -> None:
        calls.append({"kinds": tuple(kinds), **kw})

    monkeypatch.setattr(notes_routes, "schedule_reindex", fake)
    return calls


def create(client: TestClient, h: dict[str, str], **body: Any) -> Any:
    return client.post("/api/notes", json=body, headers=h)


def graded_attempt(pg: psycopg.Connection[Any], ink: Ink, *, graded: bool = True) -> None:
    pg.execute(
        "insert into public.attempts (user_id, question_id, block_id, chosen, correct, flagged)"
        " values (%s, %s, %s, %s, %s, %s)",
        (ink.owner, ink.question, ink.block, "B" if graded else None, True if graded else None, not graded),
    )


# ── notes and highlights ──


def test_create_read_list_and_replay(client: TestClient, key: SigningKey, ink: Ink, reindexed: list[Any]) -> None:
    h = ink.h(key)
    nid = str(uuid.uuid4())
    r = create(client, h, id=nid, title="  Renal   tubule ", body=doc("Loop of Henle", "NKCC2"))
    assert r.status_code == 201, r.text
    note = r.json()
    assert note["id"] == nid and note["kind"] == "note" and note["title"] == "Renal tubule"
    assert note["body_text"] == "Loop of Henle\nNKCC2" and note["excerpt"] == "Loop of Henle NKCC2"
    assert note["source"] is None and note["canvas"] == "paper" and note["has_ink"] is False
    assert reindexed == [{"kinds": ("notes",), "owner": ink.owner, "ref_ids": [nid]}]
    again = create(client, h, id=nid, title="different", body=doc("x"))
    assert again.status_code == 200 and again.json()["title"] == "Renal tubule"  # the stored note, unchanged
    assert len(reindexed) == 1
    got = client.get(f"/api/notes/{nid}", headers=h).json()
    assert got["body"] == doc("Loop of Henle", "NKCC2")
    listed = client.get("/api/notes", params={"q": "henle"}, headers=h).json()
    assert [n["id"] for n in listed["items"]] == [nid]
    assert listed["counts"]["notes"] == 2  # this one + the fixture's free note
    assert client.get("/api/notes", params={"q": "100%_"}, headers=h).json()["items"] == []


def test_highlight_on_a_visible_section_with_its_source(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    r = create(
        client,
        h,
        kind="highlight",
        anchor_type="section",
        anchor_id=ink.section,
        anchor_pos=pos(),
        highlight_color="violet",
    )
    assert r.status_code == 201, r.text
    hl = r.json()
    assert hl["anchor_pos"] == {**pos(), "detached": None}
    src = hl["source"]
    assert src["type"] == "section" and src["section_id"] == ink.section and src["chapter_id"] == ink.chapter
    assert src["section_heading"] == "H" and len(src["section_revision"]) == 32
    hidden = create(
        client,
        h,
        kind="highlight",
        anchor_type="section",
        anchor_id=ink.draft_section,
        anchor_pos=pos(),
        highlight_color="lime",
    )
    assert hidden.status_code == 404 and hidden.json()["error"]["code"] == "note_anchor_not_found"
    out = client.get("/api/notes", params={"kind": "highlight", "color": "violet"}, headers=h).json()
    assert [n["id"] for n in out["items"]] == [hl["id"]]
    assert out["counts"]["by_color"] == {"lime": 0, "violet": 1, "amber": 0}
    assert out["counts"]["by_anchor"]["section"] == 1 and out["counts"]["highlights"] == 1
    by_anchor = client.get("/api/notes", params={"anchor_type": "section", "anchor_id": ink.section}, headers=h).json()
    assert [n["id"] for n in by_anchor["items"]] == [hl["id"]]


def test_question_anchor_is_refused_before_a_graded_attempt(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any]
) -> None:
    h = ink.h(key)
    body = {
        "kind": "highlight",
        "anchor_type": "question",
        "anchor_id": ink.question,
        "anchor_pos": pos("because"),
        "highlight_color": "amber",
    }
    r = create(client, h, **body)
    assert r.status_code == 403 and r.json()["error"]["code"] == "note_question_not_attempted"
    graded_attempt(pg, ink, graded=False)  # a flag-only row does not open the gate
    assert create(client, h, **body).status_code == 403
    pg.execute("delete from public.attempts where user_id = %s", (ink.owner,))
    graded_attempt(pg, ink)
    ok = create(client, h, **body)
    assert ok.status_code == 201, ok.text
    src = ok.json()["source"]
    assert src == {**src, "type": "question", "question_id": ink.question, "block_id": ink.block, "question_number": 1}
    # another student, without an attempt, is refused; a draft question is unknown
    assert create(client, ink.h(key, ink.other), **body).status_code == 403
    draft = create(client, h, **{**body, "anchor_id": ink.draft_question})
    assert draft.status_code == 404 and draft.json()["error"]["code"] == "note_anchor_not_found"
    # a note (not a highlight) on the question takes the same gate
    assert create(client, ink.h(key, ink.other), anchor_type="question", anchor_id=ink.question).status_code == 403


def test_one_page_note_per_section(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    first = create(client, h, anchor_type="section", anchor_id=ink.section, body=doc("mine"))
    assert first.status_code == 201
    second = create(client, h, id=str(uuid.uuid4()), anchor_type="section", anchor_id=ink.section, body=doc("x"))
    assert second.status_code == 200 and second.json()["id"] == first.json()["id"]
    other = create(client, ink.h(key, ink.other), anchor_type="section", anchor_id=ink.section)
    assert other.status_code == 201 and other.json()["id"] != first.json()["id"]


def test_whiteboard_text_is_extracted_and_lists_omit_the_scene(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    scene = {"elements": [{"type": "text", "x": 1, "y": 1, "text": "ADH"}, {"type": "freedraw", "x": 0, "y": 0}]}
    r = create(client, h, canvas="whiteboard", body=scene)
    assert r.status_code == 201 and r.json()["body_text"] == "ADH"
    listed = client.get("/api/notes", params={"q": "ADH"}, headers=h).json()["items"][0]
    assert listed["body"] is None and listed["body_omitted"] is True
    assert client.get(f"/api/notes/{r.json()['id']}", headers=h).json()["body"] == scene
    no_bodies = client.get("/api/notes", params={"include_body": "false"}, headers=h).json()["items"]
    assert all(n["body"] is None and n["body_omitted"] for n in no_bodies)
    assert create(client, h, canvas="whiteboard", body=doc("x")).json()["error"]["code"] == "note_body_invalid"
    assert create(client, h, body={"type": "doc", "content": 3}).json()["error"]["code"] == "note_body_invalid"


def test_invalid_shapes_and_limits(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    assert create(client, h, kind="highlight", anchor_type="section", anchor_id=ink.section).status_code == 422
    assert create(client, h, highlight_color="lime").status_code == 422
    assert create(client, h, kind="page").status_code == 422
    big = doc("x" * 1_100_000)
    r = create(client, h, body=big)
    assert r.status_code == 413 and r.json()["error"]["code"] == "note_body_too_large"
    assert create(client, h, notebook_id=str(uuid.uuid4())).json()["error"]["code"] == "notebook_not_found"


def test_patch_partial_conflict_and_reanchor(
    client: TestClient, key: SigningKey, ink: Ink, reindexed: list[Any]
) -> None:
    h = ink.h(key)
    hl = create(
        client,
        h,
        kind="highlight",
        anchor_type="section",
        anchor_id=ink.section,
        anchor_pos=pos(),
        highlight_color="lime",
    ).json()
    r = client.patch(f"/api/notes/{hl['id']}", json={"highlight_color": "amber", "body": doc("my mistake")}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["highlight_color"] == "amber" and r.json()["body_text"] == "my mistake"
    assert reindexed[-1]["ref_ids"] == [hl["id"]]
    stale = client.patch(f"/api/notes/{hl['id']}", json={"title": "x", "if_updated_at": hl["updated_at"]}, headers=h)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "note_conflict"
    fresh = r.json()["updated_at"]
    ok = client.patch(f"/api/notes/{hl['id']}", json={"anchor_pos": pos("Two"), "if_updated_at": fresh}, headers=h)
    assert ok.status_code == 200 and ok.json()["anchor_pos"]["quote"] == "Two"
    assert client.patch(f"/api/notes/{hl['id']}", json={"highlight_color": None}, headers=h).status_code == 422
    hidden = client.patch(
        f"/api/notes/{hl['id']}", json={"anchor_type": "section", "anchor_id": ink.draft_section}, headers=h
    )
    assert hidden.status_code == 404
    note = create(client, h, body=doc("free")).json()
    assert client.patch(f"/api/notes/{note['id']}", json={"highlight_color": "lime"}, headers=h).status_code == 422
    cleared = client.patch(f"/api/notes/{note['id']}", json={"title": None, "body": None}, headers=h).json()
    assert cleared["title"] is None and cleared["body"] is None and cleared["body_text"] == ""
    assert client.patch(f"/api/notes/{uuid.uuid4()}", json={"title": "x"}, headers=h).status_code == 404


def test_students_never_reach_each_others_notes(client: TestClient, key: SigningKey, ink: Ink) -> None:
    mine = create(client, ink.h(key), body=doc("secret")).json()
    them = ink.h(key, ink.other)
    for res in (
        client.get(f"/api/notes/{mine['id']}", headers=them),
        client.patch(f"/api/notes/{mine['id']}", json={"title": "x"}, headers=them),
        client.delete(f"/api/notes/{mine['id']}", headers=them),
    ):
        assert res.status_code == 404 and res.json()["error"]["code"] == "note_not_found"
    assert mine["id"] not in [n["id"] for n in client.get("/api/notes", headers=them).json()["items"]]
    # replaying someone else's id is a conflict, never their note
    clash = create(client, them, id=mine["id"], body=doc("x"))
    assert clash.status_code == 409 and clash.json()["error"]["code"] == "note_id_conflict"
    assert client.get(f"/api/notes/{mine['id']}", headers=ink.h(key)).json()["body_text"] == "secret"


def test_delete_removes_index_rows(client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any]) -> None:
    h = ink.h(key)
    note = create(client, h, body=doc("indexed")).json()
    pg.execute(
        "insert into public.embeddings (owner_user_id, ref_type, ref_id, chunk_index, content)"
        " values (%s, 'note', %s, 0, 'indexed'), (%s, 'note', %s, 1, 'more')",
        (ink.owner, note["id"], ink.owner, note["id"]),
    )
    assert client.delete(f"/api/notes/{note['id']}", headers=h).status_code == 204
    left = pg.execute("select count(*) from public.embeddings where ref_id = %s", (note["id"],)).fetchone()
    assert left == (0,)
    assert client.delete(f"/api/notes/{note['id']}", headers=h).status_code == 404
    assert client.get(f"/api/notes/{note['id']}", headers=h).status_code == 404


# ── notebooks and pages ──


def pages(client: TestClient, h: dict[str, str], nb: str) -> list[str]:
    return [p["id"] for p in client.get(f"/api/notebooks/{nb}", headers=h).json()["pages"]]


def sorts(pg: psycopg.Connection[Any], nb: str) -> list[int]:
    return [r[0] for r in pg.execute("select sort from public.notes where notebook_id = %s order by sort", (nb,))]


def test_notebook_lifecycle_and_page_order(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any]
) -> None:
    h = ink.h(key)
    nb_id, p1 = str(uuid.uuid4()), str(uuid.uuid4())
    r = client.post(
        "/api/notebooks",
        json={"id": nb_id, "first_page_id": p1, "title": "Renal", "cover": "lime", "paper": "grid"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    nb = r.json()
    assert nb["page_count"] == 1 and [p["id"] for p in nb["pages"]] == [p1] and nb["paper"] == "grid"
    assert client.post("/api/notebooks", json={"id": nb_id, "title": "again"}, headers=h).status_code == 200
    p3 = client.post(f"/api/notebooks/{nb_id}/pages", json={"paper": "cornell"}, headers=h).json()["id"]
    p2 = client.post(f"/api/notebooks/{nb_id}/pages", json={"position": 1, "canvas": "whiteboard"}, headers=h)
    assert p2.status_code == 201, p2.text
    p2id = p2.json()["id"]
    assert pages(client, h, nb_id) == [p1, p2id, p3] and sorts(pg, nb_id) == [0, 1, 2]
    replay = client.post(f"/api/notebooks/{nb_id}/pages", json={"id": p2id, "position": 0}, headers=h)
    assert replay.status_code == 200 and pages(client, h, nb_id) == [p1, p2id, p3]
    order = client.put(f"/api/notebooks/{nb_id}/pages/order", json={"page_ids": [p3, p1, p2id]}, headers=h)
    assert order.status_code == 200 and [p["id"] for p in order.json()["pages"]] == [p3, p1, p2id]
    bad = client.put(f"/api/notebooks/{nb_id}/pages/order", json={"page_ids": [p3, p1]}, headers=h)
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "notebook_pages_changed"
    dup = client.put(f"/api/notebooks/{nb_id}/pages/order", json={"page_ids": [p3, p3, p1]}, headers=h)
    assert dup.status_code == 409
    moved = client.patch(f"/api/notes/{p3}", json={"sort": 99}, headers=h)
    assert moved.status_code == 200 and pages(client, h, nb_id) == [p1, p2id, p3]
    assert client.patch(f"/api/notes/{p3}", json={"notebook_id": None}, headers=h).status_code == 422
    assert client.delete(f"/api/notes/{p1}", headers=h).status_code == 204
    assert pages(client, h, nb_id) == [p2id, p3] and sorts(pg, nb_id) == [0, 1]
    patched = client.patch(f"/api/notebooks/{nb_id}", json={"title": "Renal II", "cover": None}, headers=h).json()
    assert patched["title"] == "Renal II" and patched["cover"] is None
    assert client.patch(f"/api/notebooks/{nb_id}", json={"cover": "neon"}, headers=h).status_code == 422
    assert client.patch(f"/api/notebooks/{nb_id}", json={"title": "  "}, headers=h).status_code == 422
    shelf = client.get("/api/notebooks", headers=h).json()["items"]
    assert [b["id"] for b in shelf] == [nb_id] and shelf[0]["page_count"] == 2


def test_deleting_a_notebook_takes_its_pages_ink_and_index_rows(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any]
) -> None:
    h = ink.h(key)
    nb = client.post("/api/notebooks", json={"title": "Cardio"}, headers=h).json()
    page = nb["pages"][0]["id"]
    filed = create(client, h, notebook_id=nb["id"], body=doc("filed")).json()
    assert client.get(f"/api/notebooks/{nb['id']}", headers=h).json()["note_count"] == 1
    pg.execute(
        "insert into public.ink_layers (user_id, surface_type, surface_id, item_count)"
        " values (%s, 'notebook_page', %s, 2)",
        (ink.owner, page),
    )
    assert client.get(f"/api/notes/{page}", headers=h).json()["has_ink"] is True
    pg.execute(
        "insert into public.embeddings (owner_user_id, ref_type, ref_id, chunk_index, content)"
        " values (%s, 'ink', %s, 0, 'hw'), (%s, 'note', %s, 0, 'page')",
        (ink.owner, f"notebook_page:{page}", ink.owner, page),
    )
    assert client.delete(f"/api/notebooks/{nb['id']}", headers=h).status_code == 204
    assert client.get(f"/api/notes/{page}", headers=h).status_code == 404
    assert client.get(f"/api/notes/{filed['id']}", headers=h).json()["notebook_id"] is None  # kept, unfiled
    assert pg.execute(
        "select count(*) from public.ink_layers where surface_type = 'notebook_page' and surface_id = %s", (page,)
    ).fetchone() == (0,)
    assert pg.execute(
        "select count(*) from public.embeddings where owner_user_id = %s and ref_id = any(%s)",
        (ink.owner, [page, f"notebook_page:{page}"]),
    ).fetchone() == (0,)
    assert client.get(f"/api/notebooks/{nb['id']}", headers=h).status_code == 404


def test_notebooks_are_per_student(client: TestClient, key: SigningKey, ink: Ink) -> None:
    nb = client.post("/api/notebooks", json={"title": "Pharm"}, headers=ink.h(key)).json()
    them = ink.h(key, ink.other)
    assert client.get(f"/api/notebooks/{nb['id']}", headers=them).status_code == 404
    assert client.post(f"/api/notebooks/{nb['id']}/pages", json={}, headers=them).status_code == 404
    assert client.delete(f"/api/notebooks/{nb['id']}", headers=them).status_code == 404
    filed = create(client, them, notebook_id=nb["id"])
    assert filed.status_code == 404 and filed.json()["error"]["code"] == "notebook_not_found"
    assert client.get("/api/notebooks", headers=them).json()["items"] == []


def test_page_ink_surface_follows_the_page(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    nb = client.post("/api/notebooks", json={"title": "Ink"}, headers=h).json()
    page = nb["pages"][0]["id"]
    assert client.get(f"/api/ink/notebook_page/{page}", headers=h).status_code == 200
    assert client.get(f"/api/ink/notebook_page/{page}", headers=ink.h(key, ink.other)).status_code == 404
    client.delete(f"/api/notes/{page}", headers=h)
    assert client.get(f"/api/ink/notebook_page/{page}", headers=h).status_code == 404


def test_database_constraints_back_the_api(pg: psycopg.Connection[Any], ink: Ink) -> None:
    """0017's checks hold even for a privileged writer."""
    bad_rows = [
        {"user_id": ink.owner, "kind": "highlight", "anchor_type": "section", "anchor_id": ink.section},
        {"user_id": ink.owner, "kind": "note", "highlight_color": "lime"},
        {"user_id": ink.owner, "anchor_type": "section"},
        {"user_id": ink.owner, "kind": "note", "canvas": "slides"},
        {"user_id": ink.owner, "body": Jsonb([1, 2])},
    ]
    for row in bad_rows:
        with pytest.raises(psycopg.errors.CheckViolation), pg.transaction():
            insert_row(pg, "notes", row)
    with pytest.raises(psycopg.errors.CheckViolation), pg.transaction():
        pg.execute("insert into public.notebooks (user_id, title, cover) values (%s, 'x', '#ff0000')", (ink.owner,))


def test_note_bodies_never_reach_the_logs(
    client: TestClient, key: SigningKey, ink: Ink, capfd: pytest.CaptureFixture[str]
) -> None:
    marker = f"PRIVATE-NOTE-{uuid.uuid4().hex}"
    h = ink.h(key)
    note = create(client, h, title=marker, body=doc(marker)).json()
    client.patch(f"/api/notes/{note['id']}", json={"body": doc(marker, marker)}, headers=h)
    client.get("/api/notes", params={"q": marker}, headers=h)
    create(client, h, kind="highlight", anchor_type="section", anchor_id=ink.section, anchor_pos=pos(marker))
    out, err = capfd.readouterr()
    assert marker not in out and marker not in err
