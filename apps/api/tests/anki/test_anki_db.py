"""Anki import / export through the real HTTP path against a real Postgres: privacy, idempotence, round trip."""

from __future__ import annotations

import io
import zipfile
from typing import Any

import psycopg
from fastapi.testclient import TestClient

from naadi_api.anki.html import doc_text
from tests.db.rls import DB_MARKS, as_user
from tests.mediadb import FakeStore, World, student

from .builders import BASIC, BASIC_REV, CLOZE, NoteSpec, build_apkg, png

pytestmark = DB_MARKS


def deck_bytes(kind: str = "anki21b") -> bytes:
    notes = [
        NoteSpec(BASIC.mid, ['<img src="cell.png"> What is a cell?', "Unit of life"], "guid-1", "bio easy", deck=2),
        NoteSpec(BASIC_REV.mid, ["cat", "gato"], "guid-2", "", deck=2, cards=[0, 1]),
        NoteSpec(CLOZE.mid, ["{{c1::Mitochondria}} make ATP [sound:m.mp3]", "powerhouse"], "guid-3", deck=1),
    ]
    return build_apkg(kind, notes, media={"cell.png": png()})


def post(client: TestClient, key: Any, uid: Any, data: bytes, query: str = "") -> Any:
    return client.post(
        f"/api/anki/import{query}",
        content=data,
        headers={**student(key, uid), "content-type": "application/octet-stream"},
    )


def test_import_creates_private_new_cards_with_pictures(
    client: TestClient, key: Any, world: World, pg: psycopg.Connection[Any], store: FakeStore
) -> None:
    r = post(client, key, world.a, deck_bytes())
    assert r.status_code == 200, r.text
    rep = r.json()
    assert (rep["cards_added"], rep["cards_existing"], rep["cards_skipped"]) == (4, 0, 0)
    assert rep["images_added"] == 1 and rep["audio_skipped"] == 1 and rep["media_manifest"] == "ok"
    assert {d["deck"] for d in rep["decks"]} == {"Biology / Cells", "Default"}
    rows = pg.execute(
        "select owner_user_id, origin, anki_guid, tags, deck, anki_note_type from public.flashcards"
        " where owner_user_id = %s order by anki_guid",
        (world.a,),
    ).fetchall()
    assert [r[2] for r in rows] == ["guid-1", "guid-2", "guid-2:1", "guid-3:0"]
    assert all(r[0] == world.a and r[1] == "anki_import" for r in rows)
    assert rows[0][3] == ["bio", "easy"] and rows[0][4] == "Biology / Cells" and rows[0][5] == "Basic"
    # Anki's own scheduling is not imported: no review row, so FSRS sees new cards.
    assert pg.execute("select count(*) from public.card_reviews where user_id = %s", (world.a,)).fetchone() == (0,)
    media = pg.execute(
        "select owner_user_id, source_id, kind, url from public.media_assets where owner_user_id = %s", (world.a,)
    ).fetchall()
    assert (
        len(media) == 1
        and media[0][1:3] == ("user-upload", "image")
        and media[0][3].startswith(f"user-uploads/{world.a}/anki/")
    )
    assert next(iter(store.objects)) == media[0][3].removeprefix("user-uploads/")
    # They come back in the due list as new cards, with the picture in `media`.
    due = client.get("/api/cards/due?limit=200", headers=student(key, world.a)).json()
    mine = [c for c in due["cards"] if c["origin"] == "anki_import"]
    assert len(mine) == 4 and all(c["schedule"]["state"] == 0 and c["editable"] is False for c in mine)
    pic = next(c for c in mine if c["media"])
    assert pic["front"]["content"][0]["type"] == "figure" and next(iter(pic["media"].values()))["kind"] == "image"


def test_reimport_is_a_no_op_and_keeps_reviews(
    client: TestClient, key: Any, world: World, pg: psycopg.Connection[Any]
) -> None:
    assert post(client, key, world.a, deck_bytes()).status_code == 200
    again = post(client, key, world.a, deck_bytes("anki2"))  # another container of the same notes
    assert again.status_code == 200
    rep = again.json()
    assert rep["cards_added"] == 0 and rep["cards_existing"] == 4 and rep["images_added"] == 0
    assert pg.execute("select count(*) from public.flashcards where owner_user_id = %s", (world.a,)).fetchone() == (4,)


def test_another_student_never_sees_imported_cards_or_pictures(
    client: TestClient, key: Any, world: World, pg: psycopg.Connection[Any]
) -> None:
    assert post(client, key, world.a, deck_bytes()).status_code == 200
    due_b = client.get("/api/cards/due?limit=200", headers=student(key, world.b)).json()
    assert [c for c in due_b["cards"] if c["origin"] == "anki_import"] == []
    mine = client.get("/api/cards/due?limit=200", headers=student(key, world.a)).json()["cards"]
    some = next(c for c in mine if c["origin"] == "anki_import")
    assert client.get(f"/api/cards/{some['id']}", headers=student(key, world.b)).status_code == 404
    with as_user(pg, world.b) as conn:
        assert conn.execute(
            "select count(*) from public.flashcards where owner_user_id = %s", (world.a,)
        ).fetchone() == (0,)
        assert conn.execute(
            "select count(*) from public.media_assets where owner_user_id = %s", (world.a,)
        ).fetchone() == (0,)
    with as_user(pg, world.a) as conn:
        assert conn.execute(
            "select count(*) from public.media_assets where owner_user_id = %s", (world.a,)
        ).fetchone() == (1,)


def test_export_then_import_round_trips_and_a_reimport_changes_nothing(
    client: TestClient, key: Any, world: World, pg: psycopg.Connection[Any]
) -> None:
    assert post(client, key, world.a, deck_bytes()).status_code == 200
    # one card the student typed herself is exported too
    made = client.post(
        "/api/cards",
        json={"front_text": "Own front", "back_text": "Own back", "deck": "Mine / Sub"},
        headers=student(key, world.a),
    )
    assert made.status_code == 201, made.text
    before = pg.execute(
        "select deck, front_html, back_html, tags, anki_guid from public.flashcards where owner_user_id = %s",
        (world.a,),
    ).fetchall()
    r = client.get("/api/anki/export", headers=student(key, world.a))
    assert (
        r.status_code == 200
        and "attachment" in r.headers["content-disposition"]
        and r.headers["content-disposition"].endswith(".apkg\"; filename*=UTF-8''naadi-all-decks.apkg")
    )
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert "collection.anki2" in z.namelist() and "media" in z.namelist()
    # B imports A's export: same decks, tags and faces
    got = post(client, key, world.b, r.content)
    assert got.status_code == 200, got.text
    assert got.json()["cards_added"] == 5 and got.json()["images_added"] == 1
    after = pg.execute(
        "select deck, front_html, back_html, tags from public.flashcards where owner_user_id = %s", (world.b,)
    ).fetchall()
    assert sorted(x[:3] for x in after) == sorted(x[:3] for x in before)
    assert sorted(tuple(x[3]) for x in after) == sorted(tuple(x[3]) for x in before)
    # A importing her own export again is a no-op (stable GUIDs)
    noop = post(client, key, world.a, r.content)
    assert noop.json()["cards_added"] == 0 and noop.json()["cards_existing"] == 5
    # one deck only (with its sub-decks)
    one = client.get("/api/anki/export", params={"deck": "Mine"}, headers=student(key, world.a))
    assert one.status_code == 200
    none = client.get("/api/anki/export", params={"deck": "Nope"}, headers=student(key, world.a))
    assert none.status_code == 404 and none.json()["error"]["code"] == "anki_no_cards_to_export"
    assert doc_text({"type": "doc", "content": []}) == ""


def test_export_never_includes_shared_cards_or_other_students(
    client: TestClient, key: Any, world: World, pg: psycopg.Connection[Any]
) -> None:
    pg.execute(
        "insert into public.flashcards (deck, front_html, back_html, origin) values ('Shared', 'sf', 'sb', 'seed')"
    )
    try:
        assert post(client, key, world.b, deck_bytes()).status_code == 200
        r = client.get("/api/anki/export", headers=student(key, world.a))
        assert r.status_code == 404  # A owns nothing, shared cards are not hers
    finally:
        pg.execute("delete from public.flashcards where deck = 'Shared' and origin = 'seed'")


def test_decks_list_and_delete_only_imported_cards(client: TestClient, key: Any, world: World) -> None:
    h = student(key, world.a)
    assert post(client, key, world.a, deck_bytes()).status_code == 200
    client.post("/api/cards", json={"front_text": "f", "back_text": "b", "deck": "Biology / Cells"}, headers=h)
    decks = client.get("/api/anki/decks", headers=h).json()
    cells = next(d for d in decks["decks"] if d["deck"] == "Biology / Cells")
    assert cells["cards"] == 4 and cells["imported"] == 3 and decks["total_cards"] == 5
    d = client.delete("/api/anki/decks", params={"deck": "Biology / Cells"}, headers=h)
    assert d.status_code == 200 and d.json() == {"deleted": 3}
    assert client.get("/api/anki/decks", headers=h).json()["total_cards"] == 2
    assert client.get("/api/anki/decks", headers=student(key, world.b)).json()["total_cards"] == 0


def test_bad_uploads_have_codes_and_nothing_is_written(
    client: TestClient, key: Any, world: World, pg: psycopg.Connection[Any]
) -> None:
    assert post(client, key, world.a, b"definitely not a zip").json()["error"]["code"] == "anki_not_a_package"
    assert post(client, key, world.a, b"").json()["error"]["code"] == "anki_not_a_package"
    empty = build_apkg("anki2", [])
    assert post(client, key, world.a, empty).json()["error"]["code"] == "anki_no_cards"
    assert pg.execute("select count(*) from public.flashcards where owner_user_id = %s", (world.a,)).fetchone() == (0,)
    assert client.post("/api/anki/import", content=b"x").status_code == 401
    assert client.get("/api/anki/export").status_code == 401


def test_the_size_cap_applies_while_streaming(client: TestClient, key: Any, world: World, monkeypatch: Any) -> None:
    monkeypatch.setattr("naadi_api.anki.routes.MAX_UPLOAD_BYTES", 1000)
    r = post(client, key, world.a, b"x" * 5000)
    assert r.status_code == 413 and r.json()["error"]["code"] == "anki_too_large"


def test_a_student_cannot_write_anki_columns_through_rls(world: World, pg: psycopg.Connection[Any]) -> None:
    import pytest
    from psycopg import errors

    with as_user(pg, world.a) as conn, pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute(
            "insert into public.flashcards (owner_user_id, deck, front_html, back_html, origin)"
            " values (%s, 'x', 'f', 'b', 'anki_import')",
            (world.a,),
        )


def test_imported_cards_name_their_source_and_a_student_cannot_stamp_anki_columns(
    client: TestClient, key: Any, world: World, pg: psycopg.Connection[Any]
) -> None:
    import pytest
    from psycopg import errors

    assert post(client, key, world.a, deck_bytes()).status_code == 200
    assert pg.execute(
        "select count(*) filter (where source_id = 'user-upload'), count(*) from public.flashcards"
        " where owner_user_id = %s and origin = 'anki_import'",
        (world.a,),
    ).fetchone() == (4, 4)
    # a typed card cannot carry an Anki id, an Anki note type or a source (those are the import API's columns)
    with as_user(pg, world.a) as conn:
        for col, val in (("anki_guid", "x"), ("anki_note_type", "Basic"), ("source_id", "user-upload")):
            with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
                conn.execute(
                    f"insert into public.flashcards (owner_user_id, deck, front_html, back_html, origin, {col})"  # noqa: S608
                    " values (%s, 'x', 'f', 'b', 'user', %s)",
                    (world.a, val),
                )
        conn.execute(
            "insert into public.flashcards (owner_user_id, deck, front_html, back_html, origin)"
            " values (%s, 'x', 'f', 'b', 'user')",
            (world.a,),
        )
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute(
                "update public.flashcards set anki_guid = 'stamp' where owner_user_id = %s and origin = 'user'",
                (world.a,),
            )
    # and an imported card must name the upload source
    with pytest.raises(errors.CheckViolation), pg.transaction():
        pg.execute(
            "insert into public.flashcards (owner_user_id, deck, front_html, back_html, origin)"
            " values (%s, 'x', 'f', 'b', 'anki_import')",
            (world.a,),
        )
