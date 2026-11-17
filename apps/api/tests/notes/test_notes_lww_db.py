"""Integration (real Postgres, migrated schema, full HTTP path): bounded last-writer-wins between two devices.

Two devices of the same student write the same note, page or notebook. Each write carries `client_updated_at` (when
the edit was made). A write older than the stored edit changes nothing and answers 409 with the stored copy; a newer
one applies; a stamp from the far future is clamped to server time + 5 min; ties pass. Also: image references
outside the student's notes folder are dropped from what is stored.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from naadi_api.notes import lww
from tests.auth.jwt_helpers import SigningKey

from .conftest import Ink

pytestmark = [pytest.mark.integration, pytest.mark.db]

MIN = 60_000


def doc(*texts: str) -> dict[str, Any]:
    return {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": t}]} for t in texts]}


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_ms(raw: str) -> int:
    return lww.ms(dt.datetime.fromisoformat(raw))


def stored(pg: psycopg.Connection[Any], note_id: str) -> tuple[str, dt.datetime]:
    row = pg.execute("select body_text, edited_at from public.notes where id = %s", (note_id,)).fetchone()
    assert row is not None
    return row[0], row[1]


def test_a_stale_offline_edit_never_overwrites_a_newer_edit_from_another_device(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any]
) -> None:
    h = ink.h(key)
    t0 = now_ms() - 30 * MIN
    page_note = client.post(
        "/api/notes",
        json={"anchor_type": "section", "anchor_id": ink.section, "body": doc("first"), "client_updated_at": t0},
        headers=h,
    )
    assert page_note.status_code == 201, page_note.text
    note = page_note.json()
    assert iso_ms(note["edited_at"]) == t0
    t1 = t0 + 10 * MIN  # device A, offline: its edit waits on the device
    t2 = t0 + 20 * MIN  # device B, online later: saved at once
    b = client.patch(f"/api/notes/{note['id']}", json={"body": doc("B text"), "client_updated_at": t2}, headers=h)
    assert b.status_code == 200, b.text
    assert iso_ms(b.json()["edited_at"]) == t2

    # A reconnects and replays its older edit: refused, with the stored (newer) copy to adopt.
    a = client.patch(
        f"/api/notes/{note['id']}", json={"body": doc("A text"), "title": "A", "client_updated_at": t1}, headers=h
    )
    assert a.status_code == 409, a.text
    out = a.json()
    assert out["error"]["code"] == "note_conflict"
    assert out["current"]["id"] == note["id"] and out["current"]["body_text"] == "B text"
    assert out["current"]["title"] is None and iso_ms(out["current"]["edited_at"]) == t2
    assert abs(out["server_now"] - now_ms()) < 60_000
    text, edited = stored(pg, note["id"])
    assert text == "B text" and lww.ms(edited) == t2

    # A tie passes (a replay of B's own write), and a newer edit from A applies and moves the clock forward.
    assert (
        client.patch(f"/api/notes/{note['id']}", json={"body": doc("B text"), "client_updated_at": t2}, headers=h)
    ).status_code == 200
    t3 = t2 + MIN
    newer = client.patch(f"/api/notes/{note['id']}", json={"body": doc("A later"), "client_updated_at": t3}, headers=h)
    assert newer.status_code == 200 and newer.json()["body_text"] == "A later"
    assert lww.ms(stored(pg, note["id"])[1]) == t3


def test_a_far_future_stamp_is_clamped_to_server_time_plus_five_minutes(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any]
) -> None:
    h = ink.h(key)
    note = client.post("/api/notes", json={"body": doc("x")}, headers=h).json()
    before = now_ms()
    far = before + 365 * 24 * 60 * MIN
    r = client.patch(f"/api/notes/{note['id']}", json={"body": doc("wrong clock"), "client_updated_at": far}, headers=h)
    after = now_ms()
    assert r.status_code == 200, r.text
    edited = lww.ms(stored(pg, note["id"])[1])
    assert before + lww.FUTURE_SKEW_MS <= edited <= after + lww.FUTURE_SKEW_MS
    # Bounded: an honest device's edit made now loses against it, but one made after the window wins.
    honest = client.patch(f"/api/notes/{note['id']}", json={"body": doc("now"), "client_updated_at": after}, headers=h)
    assert honest.status_code == 409 and honest.json()["current"]["body_text"] == "wrong clock"
    later = client.patch(
        f"/api/notes/{note['id']}", json={"body": doc("later"), "client_updated_at": edited + 1}, headers=h
    )
    assert later.status_code == 200 and later.json()["body_text"] == "later"


def test_no_stamp_means_now_and_creates_keep_their_stamp(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any]
) -> None:
    h = ink.h(key)
    before = now_ms()
    note = client.post("/api/notes", json={"body": doc("plain")}, headers=h).json()
    assert before <= iso_ms(note["edited_at"]) <= now_ms()
    old = client.patch(f"/api/notes/{note['id']}", json={"title": "old", "client_updated_at": before - MIN}, headers=h)
    assert old.status_code == 409
    unstamped = client.patch(f"/api/notes/{note['id']}", json={"title": "kept"}, headers=h)
    assert unstamped.status_code == 200 and unstamped.json()["title"] == "kept"
    # A replayed create (same id) returns the stored note and does not move its clock.
    nid = str(uuid.uuid4())
    t = now_ms() - 5 * MIN
    first = client.post("/api/notes", json={"id": nid, "body": doc("one"), "client_updated_at": t}, headers=h)
    again = client.post("/api/notes", json={"id": nid, "body": doc("two"), "client_updated_at": t + MIN}, headers=h)
    assert first.status_code == 201 and again.status_code == 200
    assert iso_ms(again.json()["edited_at"]) == t and again.json()["body_text"] == "one"


def test_if_updated_at_mismatch_carries_the_current_copy(client: TestClient, key: SigningKey, ink: Ink) -> None:
    h = ink.h(key)
    note = client.post("/api/notes", json={"body": doc("v1")}, headers=h).json()
    client.patch(f"/api/notes/{note['id']}", json={"body": doc("v2")}, headers=h)
    r = client.patch(f"/api/notes/{note['id']}", json={"title": "x", "if_updated_at": note["updated_at"]}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "note_conflict"
    assert r.json()["current"]["body_text"] == "v2"


def test_a_stale_delete_keeps_the_note_edited_later(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any]
) -> None:
    h = ink.h(key)
    t0 = now_ms() - 30 * MIN
    note = client.post("/api/notes", json={"body": doc("keep me"), "client_updated_at": t0}, headers=h).json()
    client.patch(
        f"/api/notes/{note['id']}", json={"body": doc("edited on B"), "client_updated_at": t0 + 20 * MIN}, headers=h
    )
    stale = client.delete(f"/api/notes/{note['id']}", params={"client_updated_at": t0 + 10 * MIN}, headers=h)
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "note_conflict"
    assert stale.json()["current"]["body_text"] == "edited on B"
    assert stored(pg, note["id"])[0] == "edited on B"
    fresh = client.delete(f"/api/notes/{note['id']}", params={"client_updated_at": t0 + 25 * MIN}, headers=h)
    assert fresh.status_code == 204
    assert pg.execute("select 1 from public.notes where id = %s", (note["id"],)).fetchone() is None
    assert client.delete(f"/api/notes/{note['id']}", headers=h).status_code == 404


def test_notebook_rename_reorder_and_delete_are_last_edit_wins(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any]
) -> None:
    h = ink.h(key)
    t0 = now_ms() - 60 * MIN
    nb = client.post("/api/notebooks", json={"title": "Renal", "client_updated_at": t0}, headers=h).json()
    assert iso_ms(nb["edited_at"]) == t0 and iso_ms(nb["pages"][0]["edited_at"]) == t0
    p2 = client.post(f"/api/notebooks/{nb['id']}/pages", json={"client_updated_at": t0 + MIN}, headers=h).json()
    assert iso_ms(p2["edited_at"]) == t0 + MIN

    renamed = client.patch(
        f"/api/notebooks/{nb['id']}", json={"title": "Renal (B)", "client_updated_at": t0 + 20 * MIN}, headers=h
    )
    assert renamed.status_code == 200 and iso_ms(renamed.json()["edited_at"]) == t0 + 20 * MIN
    stale = client.patch(
        f"/api/notebooks/{nb['id']}", json={"title": "Renal (A)", "client_updated_at": t0 + 10 * MIN}, headers=h
    )
    assert stale.status_code == 409, stale.text
    body = stale.json()
    assert body["error"]["code"] == "notebook_conflict"
    assert body["current"]["title"] == "Renal (B)" and len(body["current"]["pages"]) == 2
    row = pg.execute("select title from public.notebooks where id = %s", (nb["id"],)).fetchone()
    assert row is not None and row[0] == "Renal (B)"

    order = [p2["id"], nb["pages"][0]["id"]]
    stale_order = client.put(
        f"/api/notebooks/{nb['id']}/pages/order",
        json={"page_ids": order, "client_updated_at": t0 + 15 * MIN},
        headers=h,
    )
    assert stale_order.status_code == 409 and stale_order.json()["error"]["code"] == "notebook_conflict"
    assert [p["id"] for p in stale_order.json()["current"]["pages"]] == [nb["pages"][0]["id"], p2["id"]]
    fresh_order = client.put(
        f"/api/notebooks/{nb['id']}/pages/order",
        json={"page_ids": order, "client_updated_at": t0 + 30 * MIN},
        headers=h,
    )
    assert fresh_order.status_code == 200 and [p["id"] for p in fresh_order.json()["pages"]] == order
    assert iso_ms(fresh_order.json()["edited_at"]) == t0 + 30 * MIN

    # A page's own edits are the page's clock: a stale whiteboard/paper patch of a page is a note conflict.
    page = client.patch(f"/api/notes/{p2['id']}", json={"paper": "grid", "client_updated_at": t0 + 40 * MIN}, headers=h)
    assert page.status_code == 200
    stale_page = client.patch(
        f"/api/notes/{p2['id']}", json={"paper": "dotted", "client_updated_at": t0 + 35 * MIN}, headers=h
    )
    assert stale_page.status_code == 409 and stale_page.json()["current"]["paper"] == "grid"

    gone = client.delete(f"/api/notebooks/{nb['id']}", params={"client_updated_at": t0 + 25 * MIN}, headers=h)
    assert gone.status_code == 409 and gone.json()["current"]["title"] == "Renal (B)"
    assert (
        client.delete(f"/api/notebooks/{nb['id']}", params={"client_updated_at": now_ms()}, headers=h).status_code
        == 204
    )


def test_a_conflict_never_shows_another_students_note(client: TestClient, key: SigningKey, ink: Ink) -> None:
    mine = client.post("/api/notes", json={"body": doc("mine")}, headers=ink.h(key)).json()
    other = client.patch(
        f"/api/notes/{mine['id']}", json={"body": doc("x"), "client_updated_at": 0}, headers=ink.h(key, ink.other)
    )
    assert other.status_code == 404 and "current" not in other.json()


def test_image_references_outside_the_students_notes_folder_are_dropped(
    client: TestClient, key: SigningKey, ink: Ink, pg: psycopg.Connection[Any]
) -> None:
    h = ink.h(key)
    own = f"{ink.owner}/notes/n1/a.png"

    def img(path: str) -> dict[str, Any]:
        return {"type": "image", "attrs": {"path": path, "alt": None}}

    body = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "pics"}]},
            img(own),
            img(f"{ink.other}/notes/n1/b.png"),
            img(f"{ink.owner}/notes/../exports/x.zip"),
        ],
    }
    note = client.post("/api/notes", json={"body": body}, headers=h)
    assert note.status_code == 201, note.text
    kept = [n for n in note.json()["body"]["content"] if n["type"] == "image"]
    assert [n["attrs"]["path"] for n in kept] == [own]
    patched = client.patch(
        f"/api/notes/{note.json()['id']}",
        json={"body": {"type": "doc", "content": [img("elsewhere/notes/x.png")]}},
        headers=h,
    )
    assert patched.status_code == 200 and patched.json()["body"]["content"] == []
    nb = client.post("/api/notebooks", json={"title": "Board"}, headers=h).json()
    scene = {
        "elements": [],
        "files": {
            "f1": {"id": "f1", "mimeType": "image/png", "path": own, "created": 1},
            "f2": {"id": "f2", "mimeType": "image/png", "path": f"{ink.other}/notes/p/f2.png", "created": 1},
        },
    }
    wb = client.post(f"/api/notebooks/{nb['id']}/pages", json={"canvas": "whiteboard", "body": scene}, headers=h)
    assert wb.status_code == 201, wb.text
    row = pg.execute("select body from public.notes where id = %s", (wb.json()["id"],)).fetchone()
    assert row is not None and sorted(row[0]["files"]) == ["f1"]
